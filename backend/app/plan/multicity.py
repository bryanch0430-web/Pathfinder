"""A first, conservative multi-city composer.

It plans one city at a time with the existing evidence workers and composer,
then joins the segments. Intercity transport is deliberately *not* invented:
the plan carries a gap that asks the traveller to book that leg, while route
continuity checks that the joined days and segments are consecutive.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta

from app.config import Settings
from app.core.dates import parse_date
from app.core.llm import LLMClient, LLMError
from app.core.trace import TurnTrace
from app.domain.models import (
    ConstraintCheck,
    CostBreakdown,
    CostLine,
    DataGap,
    DataQuality,
    DateRange,
    Destination,
    TicketOption,
    TransportLeg,
    TripContext,
    TripPlan,
    TripSegment,
)
from app.evidence.models import Bundle
from app.evidence.providers import Providers
from app.plan.agents import gather
from app.plan.builder import _cost, _same_city, _tickets, a_or_an, compose, fallback_plan
from app.understand.schemas import PlanDraft


@dataclass
class SegmentRange:
    city: str
    start: date
    end: date
    days: int


def segment_ranges(context: TripContext) -> list[SegmentRange]:
    stops = [stop for stop in context.stops if (stop.city or "").strip()]
    if len(stops) < 2:
        return []
    start = parse_date(context.start_date)
    if start is None:
        return []
    total = int(context.days or 0)
    if total <= 0:
        end = parse_date(context.end_date)
        total = (end - start).days + 1 if end is not None and end >= start else len(stops)
    total = max(len(stops), total)
    counts = [max(1, int(stop.nights or 0)) for stop in stops]
    if sum(counts) + 1 == total:
        counts[-1] += 1
    else:
        base = max(1, total // len(stops))
        counts = [base] * len(stops)
        counts[-1] += total - sum(counts)
    total = sum(counts)
    ranges: list[SegmentRange] = []
    cursor = start
    for index, stop in enumerate(stops):
        days = max(1, counts[index])
        end = cursor + timedelta(days=days - 1)
        if index == len(stops) - 1:
            end = start + timedelta(days=total - 1)
        ranges.append(
            SegmentRange(
                city=stop.city.strip()[:80],
                start=cursor,
                end=end,
                days=max(1, (end - cursor).days + 1),
            )
        )
        cursor = end + timedelta(days=1)
    return ranges


def _merge(
    plans: list[TripPlan],
    ranges: list[SegmentRange],
    context: TripContext,
    flights: list[TicketOption] | None = None,
) -> TripPlan:
    first = plans[0]
    # Each city's hotel runs from its first day to the next city's first day, so the
    # requested nights hold and the stays touch; the last stay ends on the final day.
    # Each segment is costed on those nights; the trip's flights are added once below.
    for index, (plan, span) in enumerate(zip(plans, ranges)):
        check_out = ranges[index + 1].start if index + 1 < len(ranges) else span.end
        for hotel in plan.hotels:
            hotel.check_in = span.start.isoformat()
            hotel.check_out = check_out.isoformat()
        plan.cost = _cost(context, plan.days, plan.hotels, [], max(1, (check_out - span.start).days))
    days = []
    seen_ids: set[str] = set()
    for index, (plan, span) in enumerate(zip(plans, ranges), start=1):
        for day in plan.days:
            day.segment_id = f"seg-{index}"
            day.city = span.city
            for item in day.items:
                if item.id in seen_ids:
                    suffix = 2
                    while f"{item.id}-{suffix}" in seen_ids:
                        suffix += 1
                    item.id = f"{item.id}-{suffix}"
                seen_ids.add(item.id)
            days.append(day)

    hotels = [hotel for plan in plans for hotel in plan.hotels]
    hotel_options = [hotel for plan in plans for hotel in plan.hotel_options]
    tickets = []
    ticket_ids: set[str] = set()
    for ticket in [*(flights or []), *(item for plan in plans for item in plan.tickets)]:
        if ticket.id not in ticket_ids:
            ticket_ids.add(ticket.id)
            tickets.append(ticket)

    segments = [
        TripSegment(
            id=f"seg-{index}",
            destination={
                "name": span.city,
                "country_code": "",
                "geo": None,
                "timezone": first.destination.timezone or "UTC",
            },
            dates={"start": span.start.isoformat(), "end": span.end.isoformat()},
        )
        for index, span in enumerate(ranges, start=1)
    ]
    origin = (context.origin or "").strip()
    legs: list[TransportLeg] = []
    if origin and not _same_city(origin, ranges[0].city):
        legs.append(
            TransportLeg(
                id="leg-out",
                from_place=origin,
                to_place=ranges[0].city,
                depart_date=ranges[0].start.isoformat(),
                kind="outbound",
                segment_id="seg-1",
            )
        )
    legs += [
        TransportLeg(
            id=f"leg-between-{index}",
            from_place=ranges[index - 1].city,
            to_place=ranges[index].city,
            depart_date=ranges[index].start.isoformat(),
            kind="between",
            segment_id=f"seg-{index + 1}",
        )
        for index in range(1, len(ranges))
    ]
    if origin and not _same_city(origin, ranges[-1].city):
        legs.append(
            TransportLeg(
                id="leg-back",
                from_place=ranges[-1].city,
                to_place=origin,
                depart_date=ranges[-1].end.isoformat(),
                kind="return",
                segment_id=f"seg-{len(ranges)}",
            )
        )
    between = [leg for leg in legs if leg.kind == "between"]

    constraints: list[ConstraintCheck] = []
    seen_constraints: set[str] = set()
    for plan in plans:
        for check in plan.constraints:
            key = check.text.strip().lower()
            if key and key not in seen_constraints:
                seen_constraints.add(key)
                constraints.append(check)

    cost_lines: list[CostLine] = []
    chosen = next((ticket for ticket in tickets if ticket.selected and ticket.price), None)
    if chosen is None:
        chosen = next((ticket for ticket in tickets if ticket.price), None)
    if chosen is not None and chosen.price is not None and chosen.price.amount:
        cost_lines.append(
            CostLine(
                label="Travel",
                amount=round(chosen.price.amount, 2),
                kind="flight",
                note=(
                    f"One return flight for the party: {origin or 'home'} to {ranges[0].city}, "
                    f"priced as a round trip and used for the way home from {ranges[-1].city}."
                ),
            )
        )
    for plan in plans:
        cost_lines.extend(plan.cost.lines)
    total = round(sum(line.amount for line in cost_lines), 2)
    budget = context.budget
    cost = CostBreakdown(
        currency=context.currency or "JPY",
        lines=cost_lines,
        total=total,
        budget=budget,
        remaining=round((budget - total), 2) if budget is not None else None,
        status="over" if budget is not None and total > budget else ("within" if budget is not None else "unknown"),
    )
    left_out = [
        label
        for label, kind in (("hotel prices", "hotel"), ("meal prices", "meal"), ("admission prices", "ticket"))
        if not any(line.kind == kind for line in cost_lines)
    ]
    if between:
        left_out.insert(0, "intercity transport")
    if left_out:
        cost.notes.append("Not in the total (no price available): " + ", ".join(left_out) + ".")
        cost.complete = False
        if cost.status == "within":
            cost.status = "unknown"

    gaps: list[DataGap] = []
    for plan in plans:
        gaps.extend(plan.data_quality.gaps)
    if between:
        gaps.append(
            DataGap(
                section="transport",
                kind="intercity",
                message=(
                    "Intercity transport is not booked or priced in this draft. "
                    "Book "
                    + " and ".join(f"{leg.from_place} → {leg.to_place}" for leg in between)
                    + " separately."
                ),
                retryable=True,
            )
        )

    quality = DataQuality(
        sources_used=sorted({source for plan in plans for source in plan.data_quality.sources_used}),
        gaps=gaps,
        notes=[note for plan in plans for note in plan.data_quality.notes][:12],
        freshness=[mark for plan in plans for mark in plan.data_quality.freshness],
    )
    route = " → ".join(span.city for span in ranges)
    return TripPlan(
        schema_version="5.0",
        trip_id=first.trip_id,
        session_id=first.session_id,
        version=first.version,
        status="draft",
        destination=Destination(
            name=route,
            country_code=first.destination.country_code,
            geo=first.destination.geo,
            timezone=first.destination.timezone,
        ),
        origin=context.origin,
        dates=DateRange(start=ranges[0].start.isoformat(), end=ranges[-1].end.isoformat()),
        party_size=int(context.party_size or 2),
        budget=first.budget,
        summary=(
            f"{a_or_an(len(days))} {len(days)}-day route: {route}. Each city is planned in order; "
            "intercity transport is left as a booking gap."
        ),
        days=days,
        hotels=hotels,
        hotel_options=hotel_options,
        tickets=tickets,
        segments=segments,
        legs=legs,
        cost=cost,
        constraints=constraints,
        disruptions=[item for plan in plans for item in plan.disruptions],
        reservations=[item for plan in plans for item in plan.reservations],
        video_notes=[item for plan in plans for item in plan.video_notes][:8],
        data_quality=quality,
        assessment=first.assessment,
        change_summary="Planned a multi-city route city by city.",
    )


async def build_multi_city_plan(
    *,
    providers: Providers,
    settings: Settings,
    context: TripContext,
    trace: TurnTrace,
    emit,
    scenario: dict | None = None,
) -> TripPlan | None:
    ranges = segment_ranges(context)
    if len(ranges) < 2:
        return None
    plans: list[TripPlan] = []
    llm = LLMClient(settings)
    try:
        for index, span in enumerate(ranges, start=1):
            await emit(
                "progress",
                {
                    "phase": "compose",
                    "message": f"Planning segment {index} of {len(ranges)}: {span.city}.",
                    "progress": min(80, 20 + index * 12),
                },
            )
            sub = context.model_copy(deep=True)
            sub.destination = span.city
            sub.stops = []
            # A segment is one stop on the route, not a trip home: no flights are searched
            # or described for it. The trip's own flights are searched once, below.
            sub.origin = ""
            sub.start_date = span.start.isoformat()
            sub.end_date = span.end.isoformat()
            sub.days = span.days
            gathered = await gather(
                providers=providers,
                settings=settings,
                context=sub,
                trace=trace,
                emit=emit,
                scenario=scenario,
                with_tickets=False,
            )
            try:
                plan, _draft = await compose(
                    llm=llm,
                    settings=settings,
                    context=sub,
                    bundle=gathered.bundle,
                    disruptions=gathered.disruptions,
                    notes=gathered.notes,
                    trace=trace,
                    emit=emit,
                )
                if not plan.days:
                    raise LLMError("the segment composer returned no days")
            except (LLMError, ValueError):
                plan = fallback_plan(
                    context=sub,
                    bundle=gathered.bundle,
                    disruptions=gathered.disruptions,
                    weather_by_date={day.date: day for day in gathered.bundle.weather},
                    start=span.start,
                    end=span.end,
                    settings=settings,
                    reason=f"segment {span.city}",
                )
            plans.append(plan)
            if index < len(ranges):
                await asyncio.sleep(0)
    finally:
        await llm.aclose()
    return _merge(plans, ranges, context, flights=await _trip_flights(providers, context, ranges, scenario))


async def _trip_flights(
    providers: Providers, context: TripContext, ranges: list[SegmentRange], scenario: dict | None
) -> list[TicketOption]:
    """One flight search for the whole trip: from the origin to the first city, back from the last."""
    if not (context.origin or "").strip():
        return []
    first, last = ranges[0], ranges[-1]
    try:
        rides = await providers.tickets(
            context.origin,
            first.city,
            first.start,
            last.end,
            int(context.party_size or 1),
            currency=context.currency,
            scenario=scenario,
        )
    except Exception:  # noqa: BLE001 - a missing fare leaves the trip unpriced, not unplanned
        return []
    bundle = Bundle()
    bundle.items.extend(rides)
    ticket_context = context.model_copy(deep=True)
    ticket_context.destination = first.city
    tickets = _tickets(ticket_context, bundle, PlanDraft(), first.start, last.end)
    for ticket in tickets:
        ticket.return_origin = ticket.return_origin or last.city
        ticket.return_destination = ticket.return_destination or context.origin
    return tickets
