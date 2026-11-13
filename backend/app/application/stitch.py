"""A multi-city trip is planned as several single-city plans and put together here.

``city_context`` is the trip form as one city of the route would be planned alone. ``stitch`` puts the per-city plans in
date order, gives every id a place of its own, starts each travel day with the chosen leg and takes out the stops the
traveller cannot reach. ``split`` is the inverse, for edits that change one city.

Nothing here calls a source or a model.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.domain.errors import PlanInvalidError
from app.domain.models import (
    CostBreakdown,
    DataGap,
    DataQuality,
    DateRange,
    DayPlan,
    FreshnessMark,
    ItemType,
    Money,
    PlanItem,
    TicketOption,
    TransportLeg,
    TripContext,
    TripPlan,
    TripSegment,
    utcnow,
)

#: Stops starting within this long of arriving are left out of the travel day.
ARRIVAL_BUFFER_MINUTES = 60
#: Stops ending within this long of a flight home leaving are left out of the last day.
HOME_BUFFER_MINUTES = 120
#: A travel day's leg is the plan item with this prefix and the leg's id.
TRANSIT_PREFIX = "transit_"
_ESTIMATE_DEPARTS = 9 * 60
_LAST_MINUTE = 23 * 60 + 59


def city_context(
    context: TripContext, segment: TripSegment, *, last: bool, budget: float | None
) -> TripContext:
    """The trip form as it would be for this city alone.

    The destination is the city; it starts the day the traveller arrives and ends the day before it is left (the
    departure day itself for the last city); the stops, the origin and the way home are left out because the legs are
    planned separately; the budget is this city's share.
    """
    start = segment.dates.start
    end = (
        segment.dates.end if last else max(start, segment.dates.end - timedelta(days=1))
    )
    return context.model_copy(
        deep=True,
        update={
            "destination": segment.destination.name,
            "stops": [],
            "origin": "",
            "one_way": False,
            "start_date": start,
            "end_date": end,
            "days": (end - start).days + 1,
            "budget": budget,
        },
    )


# -- ids --------------------------------------------------------------------------------------------------------------


def _swap(value: Any, mapping: dict[str, str]) -> Any:
    """``value`` with every string that is a key of ``mapping`` replaced, wherever it stands."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_swap(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _swap(item, mapping) for key, item in value.items()}
    return value


def _ids(plan: TripPlan) -> set[str]:
    found: set[str] = set()
    for _, item in plan.iter_items():
        found.add(item.id)
        found.update(alternative.id for alternative in item.alternatives)
    found.update(stay.id for stay in [*plan.hotels, *plan.hotel_options])
    found.update(row.id for row in [*plan.reservations, *plan.disruptions])
    return found


def _prefixed(plan: TripPlan, number: int) -> TripPlan:
    """The plan of the ``number``th city with every id prefixed ``s<number>_`` (the first keeps its ids)."""
    if number <= 1:
        return plan.model_copy(deep=True)
    mapping = {old: f"s{number}_{old}" for old in _ids(plan)}
    return TripPlan.model_validate(_swap(plan.model_dump(), mapping))


def _unprefixed(plan: TripPlan, number: int) -> TripPlan:
    if number <= 1:
        return plan
    prefix = f"s{number}_"
    mapping = {old: old[len(prefix) :] for old in _ids(plan) if old.startswith(prefix)}
    return TripPlan.model_validate(_swap(plan.model_dump(), mapping))


# -- travel days ------------------------------------------------------------------------------------------------------


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _span(option: TicketOption) -> tuple[int, int]:
    """Minutes into the day an option leaves and arrives; an arrival on a later day, or none, ends the day."""
    depart, arrive = option.depart_at, option.arrive_at
    start = depart.hour * 60 + depart.minute if depart else _ESTIMATE_DEPARTS
    if arrive is not None and depart is not None and arrive.date() > depart.date():
        end = _LAST_MINUTE
    elif arrive is not None:
        end = arrive.hour * 60 + arrive.minute
    else:
        end = start + (option.duration_minutes or 60)
    start = min(start, _LAST_MINUTE - 1)
    return start, min(max(end, start + 1), _LAST_MINUTE)


def _transit_item(
    leg: TransportLeg, option: TicketOption, start: int, end: int
) -> PlanItem:
    return PlanItem(
        id=f"{TRANSIT_PREFIX}{leg.id}",
        type=ItemType.TRANSIT,
        name=f"{leg.from_place} → {leg.to_place} ({option.mode})",
        start=_clock(start),
        end=_clock(end),
        notes="Estimated time" if option.estimated else "",
        source_refs=list(option.source_refs),
        price=option.price,
        price_kind=option.price_kind,
        booking_url=option.booking_url,
    )


def _without(day: DayPlan, dropped: list[PlanItem], kept: list[PlanItem]) -> DayPlan:
    """The day with ``kept`` (in time order); a stop whose transfer came from a dropped stop has no transfer."""
    gone = {item.id for item in dropped}
    cleaned = [
        item.model_copy(update={"transfer": None})
        if item.transfer and item.transfer.from_item_id in gone
        else item
        for item in sorted(kept, key=lambda item: (item.start, item.end))
    ]
    return day.model_copy(update={"items": cleaned})


def _arrive(
    day: DayPlan, leg: TransportLeg, option: TicketOption, notes: list[str]
) -> DayPlan:
    """The day with the leg first and the stops that start before the traveller can be there taken out."""
    start, end = _span(option)
    cutoff = end + ARRIVAL_BUFFER_MINUTES
    dropped = [
        item
        for item in day.items
        if not (item.locked or item.confirmed)
        and (
            _minutes(item.start) < cutoff
            or (_minutes(item.start) < end and _minutes(item.end) > start)
        )
    ]
    kept = [item for item in day.items if item not in dropped]
    notes.extend(
        f"{item.name} was left out of {day.date.isoformat()}: you arrive at {_clock(end)}."
        for item in dropped
    )
    if any(_minutes(item.start) < end and _minutes(item.end) > start for item in kept):
        # A stop the traveller locked or confirmed is in the way: it stays, and so does the day as it was.
        notes.append(
            f"{leg.from_place} → {leg.to_place} could not be placed on {day.date.isoformat()}: a fixed stop is in the way."
        )
        return _without(day, dropped, kept)
    return _without(day, dropped, [_transit_item(leg, option, start, end), *kept])


def _leave(day: DayPlan, option: TicketOption, notes: list[str]) -> DayPlan:
    """The last day without the stops that would run into the flight home."""
    if option.depart_at is None:
        return day
    cutoff = option.depart_at.hour * 60 + option.depart_at.minute - HOME_BUFFER_MINUTES
    dropped = [
        item
        for item in day.items
        if not (item.locked or item.confirmed) and _minutes(item.end) > cutoff
    ]
    kept = [item for item in day.items if item not in dropped]
    notes.extend(
        f"{item.name} was left out of {day.date.isoformat()}: your transport home leaves at {option.depart_at:%H:%M}."
        for item in dropped
    )
    return _without(day, dropped, kept)


def _chosen(tickets: list[TicketOption], leg: TransportLeg) -> TicketOption | None:
    options = [ticket for ticket in tickets if ticket.leg_id == leg.id]
    return next(
        (ticket for ticket in options if ticket.selected),
        options[0] if options else None,
    )


# -- putting the cities together --------------------------------------------------------------------------------------


def _city_gap(gap: DataGap, city: str) -> DataGap:
    return (
        gap
        if gap.message.startswith(f"{city}: ")
        else gap.model_copy(update={"message": f"{city}: {gap.message}"})
    )


def _oldest_marks(marks: list[FreshnessMark]) -> list[FreshnessMark]:
    kept: dict[str, FreshnessMark] = {}
    for mark in marks:
        if mark.section not in kept or mark.fetched_at < kept[mark.section].fetched_at:
            kept[mark.section] = mark
    return list(kept.values())


def stitch(
    parts: list[TripPlan],
    segments: list[TripSegment],
    legs: list[TransportLeg],
    tickets: list[TicketOption],
    context: TripContext,
    *,
    session_id: str,
    version: int,
    trip_id: str | None,
    note: str = "",
) -> TripPlan:
    """One plan from the per-city plans.

    A city's plan may run to the day it is left; that day belongs to the next city, so it is taken from there. The
    cost and the constraint checks are left for the caller to work out on the whole plan.
    """
    if len(parts) != len(segments) or not segments:
        raise PlanInvalidError("The cities of the trip and their plans do not match.")
    dates = DateRange(start=segments[0].dates.start, end=segments[-1].dates.end)
    days: list[DayPlan] = []
    stays, options, reservations, disruptions, videos = [], [], [], [], []
    gaps: list[DataGap] = []
    notes: list[str] = []
    sources: set[str] = set()
    marks: list[FreshnessMark] = []
    for number, (part, segment) in enumerate(zip(parts, segments), 1):
        part = _prefixed(part, number)
        city = segment.destination.name
        last = number == len(segments)
        for day in part.days:
            inside = segment.dates.start <= day.date < segment.dates.end or (
                last and day.date == segment.dates.end
            )
            if inside:
                days.append(
                    day.model_copy(deep=True, update={"segment_id": segment.id})
                )
        stays += [
            stay.model_copy(
                update={
                    "check_in": segment.dates.start,
                    "check_out": segment.dates.end,
                    "segment_id": segment.id,
                }
            )
            for stay in part.hotels
        ]
        options += [
            stay.model_copy(update={"segment_id": segment.id})
            for stay in part.hotel_options
        ]
        reservations += part.reservations
        disruptions += part.disruptions
        videos += part.video_notes
        gaps += [_city_gap(gap, city) for gap in part.data_quality.gaps]
        notes += part.data_quality.notes
        sources.update(part.data_quality.sources_used)
        marks += part.data_quality.freshness
    if [day.date for day in days] != dates.dates:
        raise PlanInvalidError(
            "The plans of the cities do not cover every day of the trip."
        )

    by_date = {day.date: index for index, day in enumerate(days)}
    for leg in legs:
        option = _chosen(tickets, leg)
        if option is None:
            continue
        if leg.kind in ("outbound", "between") and leg.depart_date in by_date:
            index = by_date[leg.depart_date]
            days[index] = _arrive(days[index], leg, option, notes)
        elif (
            leg.kind == "return"
            and not option.estimated
            and option.depart_at is not None
        ):
            index = by_date[dates.end]
            days[index] = _leave(days[index], option, notes)

    names = " → ".join(segment.destination.name for segment in segments)
    summary = f"{names}: {len(days)} days." + (f" {note}" if note else "")
    plan = TripPlan(
        **({"trip_id": trip_id} if trip_id else {}),
        session_id=session_id,
        version=version,
        destination=segments[0].destination,
        origin=context.origin,
        dates=dates,
        segments=segments,
        party_size=context.party_size or 1,
        budget=Money(amount=context.budget, currency=context.currency)
        if context.budget is not None
        else None,
        summary=summary,
        days=days,
        hotels=stays,
        hotel_options=options,
        tickets=tickets,
        legs=legs,
        cost=CostBreakdown(),
        reservations=reservations,
        disruptions=disruptions,
        data_quality=DataQuality(
            sources_used=sorted(sources),
            gaps=gaps,
            notes=list(dict.fromkeys(notes))[:30],
            search_window_months=parts[0].data_quality.search_window_months,
            generated_at=utcnow(),
            freshness=_oldest_marks(marks),
        ),
        video_notes=videos[:6],
    )
    # What is stored must be readable again (a day whose stops overlap cannot be).
    return TripPlan.model_validate(plan.model_dump())


def split(plan: TripPlan) -> list[TripPlan]:
    """The per-city plans of a stitched plan: the inverse of ``stitch``.

    A city that is not the last one gets an empty departure day, as a city planned alone from its arrival to the day it
    is left has; the legs between cities are not part of any city.
    """
    leg_items = {f"{TRANSIT_PREFIX}{leg.id}" for leg in plan.legs}
    parts: list[TripPlan] = []
    for number, segment in enumerate(plan.segments, 1):
        days = [
            day.model_copy(
                deep=True,
                update={"items": [i for i in day.items if i.id not in leg_items]},
            )
            for day in plan.days
            if day.segment_id == segment.id
        ]
        if number < len(plan.segments):
            days.append(DayPlan(date=segment.dates.end, segment_id=segment.id))
        city = segment.destination.name
        item_ids = {item.id for day in days for item in day.items}
        part = TripPlan(
            trip_id=plan.trip_id,
            session_id=plan.session_id,
            version=plan.version,
            destination=segment.destination,
            dates=segment.dates,
            party_size=plan.party_size,
            days=days,
            hotels=[stay.model_copy(deep=True) for stay in plan.stays_in(segment.id)],
            hotel_options=[
                stay.model_copy(deep=True)
                for stay in plan.hotel_options
                if stay.segment_id == segment.id
            ],
            disruptions=[
                row for row in plan.disruptions if set(row.affected_item_ids) & item_ids
            ],
            data_quality=DataQuality(
                gaps=[
                    gap.model_copy(
                        update={"message": gap.message.removeprefix(f"{city}: ")}
                    )
                    for gap in plan.data_quality.gaps
                    if gap.message.startswith(f"{city}: ")
                ],
                sources_used=list(plan.data_quality.sources_used),
                search_window_months=plan.data_quality.search_window_months,
            ),
        )
        parts.append(_unprefixed(part, number))
    return parts
