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
    TransportLeg,
    TripContext,
    TripPlan,
    TripSegment,
)
from app.evidence.providers import Providers
from app.plan.agents import gather
from app.plan.builder import compose, fallback_plan


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
    total = max(len(stops), int(context.days or 0))
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


def _merge(plans: list[TripPlan], ranges: list[SegmentRange], context: TripContext) -> TripPlan:
    first = plans[0]
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
    for plan in plans:
        for ticket in plan.tickets:
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
    legs = [
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

    constraints: list[ConstraintCheck] = []
    seen_constraints: set[str] = set()
    for plan in plans:
        for check in plan.constraints:
            key = check.text.strip().lower()
            if key and key not in seen_constraints:
                seen_constraints.add(key)
                constraints.append(check)

    cost_lines: list[CostLine] = []
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

    gaps: list[DataGap] = []
    for plan in plans:
        gaps.extend(plan.data_quality.gaps)
    if legs:
        gaps.append(
            DataGap(
                section="transport",
                kind="intercity",
                message=(
                    "Intercity transport is not booked or priced in this draft. "
                    "Book "
                    + " and ".join(f"{leg.from_place} → {leg.to_place}" for leg in legs)
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
            f"A {len(days)}-day route: {route}. Each city is planned in order; "
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
    return _merge(plans, ranges, context)
