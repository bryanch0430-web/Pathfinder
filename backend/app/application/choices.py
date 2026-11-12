"""The traveller picks another option in the UI: a stop's alternative, a hotel, a flight.

Every swap is deterministic: no model call, no place or hotel search. A swapped stop's transfers come
from the transport model through a logged ``routes.segments`` call (no network), and cost, reservations
and the related checks are recomputed from the plan's own facts. The caller saves the result as a new
plan version.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from app.application.planner import PACE_BUFFER_MINUTES, Planner, _hours, _pace, _reservations, annotate_stays
from app.application.transfers import refresh_transfers
from app.application.trip_rules import flight_windows
from app.domain.models import (
    DataGap,
    DataStatus,
    DayPlan,
    FailureKind,
    HotelStay,
    ItemType,
    PlanAlternative,
    PlanItem,
    PriceKind,
    TripContext,
    TripPlan,
    selected_ticket,
    utcnow,
)
from app.infrastructure.http import haversine_km

ChoiceKind = Literal["item", "hotel", "flight"]

#: Opens the plan note a chosen flight leaves when its times no longer fit the first or last day.
FLIGHT_TIMES_NOTE = "The chosen flight"
#: Said on a stay chosen from an option the source priced for other dates.
UNCHECKED_RATE_NOTE = "Rate for these dates not checked; the hotel source priced it for other dates."


class ChoiceNotFound(LookupError):
    """The stop, stay or flight named is not in the plan (or the option is not one of its choices)."""


class ChoiceRefused(ValueError):
    """The swap would undo something the traveller fixed (a locked or confirmed stop)."""


async def apply_choice(
    plan: TripPlan,
    *,
    kind: ChoiceKind,
    target_id: str | None,
    option_id: str,
    planner: Planner,
    context: TripContext | None,
    toolbox: Any | None = None,
    transfers: Any | None = None,
) -> TripPlan:
    """A copy of ``plan`` with the choice made, at the next version. ``plan`` itself is not changed."""
    updated = plan.model_copy(deep=True)
    normalise_hotel_options(updated)
    if kind == "item":
        summary = await _choose_item(updated, plan, target_id, option_id, context, toolbox, transfers)
    elif kind == "hotel":
        summary = _choose_hotel(updated, option_id)
    elif kind == "flight":
        summary = _choose_flight(updated, option_id)
    else:  # the request model already refuses other kinds
        raise ValueError(f"unknown choice kind {kind!r}")
    _recost(updated, planner, context)
    annotate_stays(updated)
    # The choice is checked once more by the refine loop. A chosen stop's own day may be refined (the
    # chosen stop itself is never swapped back or moved out); a stay or flight only refreshes the
    # plan's checks. Every change the loop makes is told in the summary and a plan note.
    from app.application.refine import refine_and_refresh

    changes: list[str] = []
    day_index = _day_of(updated, target_id) if kind == "item" else None
    updated, _, _ = await refine_and_refresh(
        updated, context, settings=getattr(planner, "settings", None),
        max_rounds=1 if day_index is not None else 0, toolbox=toolbox, transfers=transfers,
        keep_ids={target_id} if kind == "item" and target_id else (),
        only_days={day_index} if day_index is not None else set(),
        changes=changes,
    )
    if plan.assessment is not None:
        # The assessment follows the choice (no further research: the traveller chose).
        from app.application.assessment import reassess

        updated.assessment = reassess(updated, context, getattr(planner, "settings", None))
    updated.version = plan.version + 1
    updated.updated_at = utcnow()
    updated.change_summary = " ".join([summary, *changes])
    return updated


def _day_of(plan: TripPlan, item_id: str | None) -> int | None:
    return next(
        (index for index, day in enumerate(plan.days) for item in day.items if item_id and item.id == item_id), None
    )


# --- stops -------------------------------------------------------------------


async def _choose_item(
    updated: TripPlan,
    original: TripPlan,
    target_id: str | None,
    option_id: str,
    context: TripContext | None,
    toolbox: Any | None,
    transfers: Any | None,
) -> str:
    found = next(
        ((day, position) for day in updated.days for position, item in enumerate(day.items) if item.id == target_id),
        None,
    )
    if found is None:
        raise ChoiceNotFound("stop not found")
    day, position = found
    item = day.items[position]
    option = next((alt for alt in item.alternatives if alt.id == option_id), None)
    if option is None:
        raise ChoiceNotFound("option not found")
    if item.locked or item.confirmed:
        raise ChoiceRefused("This stop is locked or confirmed; unlock it before choosing another.")
    if item.type is ItemType.ATTRACTION and not option.source_refs:
        raise ChoiceRefused("This option has no source, so it cannot become an attraction stop.")

    swap_option(updated, day, position, option)
    moved = await refresh_transfers(
        original,
        updated,
        toolbox=toolbox,
        source=transfers,
        buffer=PACE_BUFFER_MINUTES.get(_pace(context) if context is not None else "balanced", 5),
    )
    summary = f"Chose {option.name} instead of {item.name} on {day.date.isoformat()}."
    if moved:
        summary += f" {_names(moved)} {'starts' if len(moved) == 1 else 'start'} later to leave time for the transfer."
    return summary


def _names(names: list[str]) -> str:
    names = list(dict.fromkeys(names))
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def swap_option(plan: TripPlan, day: DayPlan, position: int, option: PlanAlternative) -> PlanItem:
    """Put ``option`` in place of the stop at ``day.items[position]``; returns the stop it replaced.

    Every fact on the new stop is the option's own (source-backed); the replaced stop becomes an
    alternative, distances are measured again, and a reservation the old stop needed is dropped.
    The item keeps its id and times; its transfer is cleared for the caller to fetch again.
    """
    item = day.items[position]
    previous = _as_alternative(item)
    others = [alt for alt in item.alternatives if alt.id != option.id]
    taken = {alt.id for alt in others}
    if previous.id in taken or previous.id == option.id:
        previous.id = f"{previous.id}_{len(taken) + 1}"
    hours = _hours({"opening_hours": option.opening_hours}, day.date)[0] if option.opening_hours else []
    sources = ", ".join(dict.fromkeys(ref.source for ref in option.source_refs if ref.source)) or "a travel source"
    label = (item.meal_slot or ("meal" if item.type is ItemType.MEAL else "stop")).capitalize()
    chosen = item.model_copy(
        update={
            "name": option.name,
            "geo": option.geo,
            "address": option.address,
            "notes": f"{label} chosen from the options; from {sources}.",
            "source_refs": list(option.source_refs),
            "data_status": DataStatus.OK,
            "fetched_at": utcnow(),
            "price": None,
            "price_kind": PriceKind.NONE,
            "booking_url": option.booking_url,
            "tags": ["meal"] if item.type is ItemType.MEAL else [],
            "opening_hours": hours,
            "hours_status": "stated" if hours else "unknown",
            "transfer": None,
            "names": {},
            "rating": option.rating,
            "review_count": option.review_count,
            "price_level": option.price_level,
            "cuisine": option.cuisine,
            "reservable": option.reservable,
            "source_types": list(option.source_types),
            "source_summary": option.source_summary,
            "alternatives": _remeasure([previous, *others], option),
        }
    )
    day.items[position] = PlanItem.model_validate(chosen.model_dump())
    # A reservation the old stop needed is not this stop's.
    plan.reservations = [row for row in plan.reservations if row.name != item.name]
    return item


def _as_alternative(item: PlanItem) -> PlanAlternative:
    return PlanAlternative(
        id=f"alt_{item.id}",
        name=item.name,
        geo=item.geo,
        address=item.address,
        rating=item.rating,
        review_count=item.review_count,
        price_level=item.price_level,
        cuisine=item.cuisine,
        reservable=item.reservable,
        opening_hours=list(item.opening_hours),
        source_refs=list(item.source_refs),
        booking_url=item.booking_url,
        source_types=list(item.source_types),
        source_summary=item.source_summary,
    )


def _remeasure(alternatives: list[PlanAlternative], origin: PlanAlternative) -> list[PlanAlternative]:
    """Each alternative's straight-line distance from the new default, computed again."""
    out: list[PlanAlternative] = []
    for alt in alternatives:
        distance = None
        if origin.geo is not None and alt.geo is not None:
            distance = int(round(haversine_km(origin.geo.lat, origin.geo.lng, alt.geo.lat, alt.geo.lng) * 1000))
        out.append(alt.model_copy(update={"distance_m": distance}))
    return out


# --- hotel -------------------------------------------------------------------


def _choose_hotel(updated: TripPlan, option_id: str) -> str:
    option = next((stay for stay in updated.hotel_options if stay.id == option_id), None)
    if option is None:
        raise ChoiceNotFound("hotel option not found")
    current = updated.hotels[0] if updated.hotels else None
    if current is not None and current.id == option.id:
        return f"Kept {option.name} as the stay."
    if current is not None and (current.locked or current.confirmed):
        raise ChoiceRefused("The stay is locked or confirmed; unlock it before choosing another.")
    check_in = current.check_in if current else updated.dates.start
    check_out = current.check_out if current else updated.dates.end
    changes: dict = {"check_in": check_in, "check_out": check_out, "confirmed": False, "locked": False}
    if (option.check_in, option.check_out) != (check_in, check_out):
        # The source priced this option (and listed its rooms) for other dates: neither is this stay's.
        changes.update(
            price_per_night=None, price_kind=PriceKind.NONE, room_types=[], rooms_checked_for=None,
            notes=UNCHECKED_RATE_NOTE,
        )
    chosen = option.model_copy(update=changes)
    updated.hotels = [chosen, *updated.hotels[1:]] if updated.hotels else [chosen]
    updated.hotel_options = [chosen if stay.id == chosen.id else stay for stay in updated.hotel_options]
    if current is not None and not any(stay.id == current.id for stay in updated.hotel_options):
        updated.hotel_options.append(current)
    if current is not None:
        _retell(updated, "Hotel", current.name, chosen.name)
    return f"Chose {chosen.name} as the stay instead of {current.name}." if current else f"Chose {chosen.name} as the stay."


def stay_dates_match(option: HotelStay, stay: HotelStay | None) -> bool:
    """True when ``option`` was found (and priced) for the current stay's own check-in and check-out."""
    return stay is None or (option.check_in, option.check_out) == (stay.check_in, stay.check_out)


def normalise_hotel_options(plan: TripPlan) -> None:
    """The current stay first among the options, as it stands now.

    An edit may move or replace ``hotels[0]`` without touching the options; the options it left keep
    the dates (and nightly rate) the source gave them, which ``stay_dates_match`` tells apart.
    """
    if not plan.hotels:
        return
    current = plan.hotels[0]
    others = [option for option in plan.hotel_options if option.id != current.id]
    plan.hotel_options = [current.model_copy(deep=True), *others]


# --- flight ------------------------------------------------------------------


def _choose_flight(updated: TripPlan, option_id: str) -> str:
    option = next((ticket for ticket in updated.tickets if ticket.id == option_id), None)
    if option is None:
        raise ChoiceNotFound("flight option not found")
    before = selected_ticket(updated.tickets)
    for ticket in updated.tickets:
        ticket.selected = ticket.id == option.id
    updated.reservations = [row for row in updated.reservations if not row.name.startswith("Flight · ")] + _reservations(
        [], updated.tickets
    )
    if before is not None and before.id != option.id:
        _retell(updated, "Flight", before.name, option.name)
    updated.data_quality.notes = [note for note in updated.data_quality.notes if not note.startswith(FLIGHT_TIMES_NOTE)]
    clash = _flight_clashes(updated)
    if clash:
        updated.data_quality.notes.append(
            f"{FLIGHT_TIMES_NOTE} ({option.name}) does not leave time for {clash}; move or drop those stops."
        )
    return f"Chose the flight {option.name}." if before is None or before.id == option.id else (
        f"Chose the flight {option.name} instead of {before.name}."
    )


def _flight_clashes(plan: TripPlan) -> str:
    """The stops the chosen flight's times leave no room for on the first or last day, as one phrase."""
    ticket = selected_ticket(plan.tickets)
    if ticket is None:
        return ""
    dates: list[date] = [day.date for day in plan.days]
    windows = flight_windows([ticket.model_dump()], dates)
    clashes: list[str] = []
    for day in plan.days:
        window = windows.get(day.date)
        if window is None:
            continue
        for item in day.items:
            start, end = _minutes(item.start), _minutes(item.end)
            if (window.start is not None and start < window.start) or (window.end is not None and end > window.end):
                clashes.append(f"{item.name} on {day.date.isoformat()}")
    return ", ".join(clashes[:6])


# --- shared ------------------------------------------------------------------


def _recost(plan: TripPlan, planner: Planner, context: TripContext | None) -> None:
    """Cost from the chosen stay and flight, against the budget the plan was costed with."""
    priced = TripContext(
        session_id=plan.session_id,
        currency=plan.cost.currency or (context.currency if context else "") or "USD",
        budget=plan.cost.budget if plan.cost.budget is not None else (context.budget if context else None),
    )
    stay = plan.hotels[0] if plan.hotels else None
    plan.cost = planner._cost(priced, stay, plan.tickets, plan.dates)
    if stay is not None and stay.price_per_night is None and stay.notes == UNCHECKED_RATE_NOTE:
        plan.cost.notes = [
            f"{stay.name}: {UNCHECKED_RATE_NOTE[0].lower()}{UNCHECKED_RATE_NOTE[1:]}" if note == "Hotel price was not returned." else note
            for note in plan.cost.notes
        ]
    plan.data_quality.gaps = [gap for gap in plan.data_quality.gaps if gap.section != "budget"]
    if plan.cost.status == "over" and plan.cost.budget is not None:
        over = plan.cost.total - plan.cost.budget
        plan.data_quality.gaps.append(
            DataGap(
                section="budget",
                kind=FailureKind.NO_DATA,
                message=(
                    f"The verified cost is {plan.cost.currency} {plan.cost.total:,.0f}, which is {over:,.0f} over the "
                    f"{plan.cost.currency} {plan.cost.budget:,.0f} budget."
                ),
            )
        )


def _retell(plan: TripPlan, lead: str, old: str, new: str) -> None:
    """A check whose evidence named the old choice names the new one."""
    for check in plan.constraints:
        if check.evidence == old and lead.lower() in check.text.lower():
            check.evidence = new


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


__all__ = ["ChoiceNotFound", "ChoiceRefused", "apply_choice", "normalise_hotel_options", "stay_dates_match"]
