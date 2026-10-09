"""Merge step of "check and merge" plus the selection helpers both paths share.

Proposal: "When users prompt for an edit, System 1 identifies the parts to be changed. Only the
specific agents responsible will be called. All confirmed items that do not require changes will
be preserved. The result is then checked and merged into the existing plan." Everything here is
deterministic code: the modify path makes no planner model call.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from backend.agents.checks import check_plan
from backend.agents.geo import normalise_name
from backend.agents.plan_ops import (
    PEOPLE_PER_ROOM,
    TARGET_ITEMS_PER_DAY,
    build_sections,
    compute_cost,
    order_and_schedule_day,
    touch,
)
from backend.agents.preplanning.hotel import HOTEL_BUDGET_SHARE
from backend.agents.route_order import RouteOrderer
from backend.schemas.agents import (
    AgentResult,
    AttractionData,
    HotelData,
    TicketData,
    WeatherData,
)
from backend.schemas.common import AgentName, SectionStatus
from backend.schemas.routing import ChangeRequest, FocusKind, PlanFocus
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import (
    DayPlan,
    Disruption,
    DisruptionKind,
    Hotel,
    HotelStay,
    ItineraryItem,
    Place,
    Reservation,
    TicketOption,
    TripPlan,
)

# ------------------------------------------------------------------------------------------------
# Selection helpers (plan path and modify path)
# ------------------------------------------------------------------------------------------------


def cheaper_than(candidates: Sequence[Hotel], current: Hotel) -> list[Hotel]:
    """Candidates with a lower nightly price than `current`, in the same currency (there is no
    exchange-rate tool, so a price in another currency cannot be compared)."""
    price = current.nightly_price
    return [
        h
        for h in candidates
        if h.nightly_price.currency == price.currency and h.nightly_price.amount < price.amount
    ]


def choose_hotel(
    candidates: Sequence[Hotel], context: TripContext, *, preferred_id: str | None = None
) -> HotelStay | None:
    """Pick a fetched candidate for the trip nights: the preferred one if it fits the hotel share
    of the budget, else the best-ranked one that fits, else the cheapest."""
    if not (context.start_date and context.end_date) or context.end_date <= context.start_date:
        return None
    if not candidates:
        return None
    nights = (context.end_date - context.start_date).days
    rooms = math.ceil((context.party_size or 1) / PEOPLE_PER_ROOM)
    cap = context.budget.amount * HOTEL_BUDGET_SHARE if context.budget else None

    def fits(h: Hotel) -> bool:
        if cap is None or context.budget is None:
            return True
        same_currency = h.nightly_price.currency == context.budget.currency
        return same_currency and h.nightly_price.amount * nights * rooms <= cap

    ordered = list(candidates)
    preferred = next((h for h in ordered if h.hotel_id == preferred_id), None)
    chosen = (
        preferred
        if preferred is not None and fits(preferred)
        else next((h for h in ordered if fits(h)), None)
        or min(ordered, key=lambda h: (h.nightly_price.amount, h.hotel_id))
    )
    return HotelStay(hotel=chosen, check_in=context.start_date, check_out=context.end_date)


def _pick(options: Sequence[TicketOption], on: date | None, preferred: set[str], latest: bool) -> TicketOption | None:
    usable = [t for t in options if t.status == "scheduled" and (on is None or t.depart_at.date() == on)]
    if not usable:
        return None
    for option in usable:
        if option.ticket_id in preferred:
            return option
    return max(usable, key=lambda t: t.depart_at) if latest else min(usable, key=lambda t: t.depart_at)


def choose_tickets(
    data: TicketData, context: TripContext, *, preferred_ids: Iterable[str] = ()
) -> list[TicketOption]:
    """One scheduled outbound on the start date (earliest) and one scheduled return on the end
    date (latest), preferring the planner's picks. Delayed/cancelled options are never chosen."""
    preferred = set(preferred_ids)
    chosen: list[TicketOption] = []
    out = _pick(data.outbound, context.start_date, preferred, latest=False)
    back = _pick(data.inbound, context.end_date, preferred, latest=True)
    chosen += [t for t in (out, back) if t is not None]
    return chosen


def link_reservations(reservations: Sequence[Reservation], places: Sequence[Place]) -> list[Reservation]:
    by_name = {normalise_name(p.name): p.place_id for p in places}
    return [
        r.model_copy(update={"place_id": by_name.get(normalise_name(r.place_name), r.place_id)})
        for r in reservations
    ]


def _blocked(place: Place, day: DayPlan) -> bool:
    warning = day.forecast.warning_signal if day.forecast else None
    return day.date in place.closed_dates or (warning is not None and place.indoor is False)


def enforce_day_rules(plan: TripPlan) -> tuple[TripPlan, list[Disruption]]:
    """Remove stops that cannot happen: a venue closed that day, or an outdoor stop under an
    official weather warning (typhoon signal). Applies to confirmed items too: a confirmed visit
    to a closed venue is no longer valid. Each removal is recorded as a disruption."""
    disruptions: list[Disruption] = []
    days: list[DayPlan] = []
    for day in plan.days:
        kept: list[ItineraryItem] = []
        for item in day.items:
            place = plan.place(item.place_id)
            if place is None or not _blocked(place, day):
                kept.append(item)
                continue
            if day.date in place.closed_dates:
                disruptions.append(
                    Disruption(
                        kind=DisruptionKind.VENUE_CLOSED,
                        detail=f"{place.name} is closed on {day.date}",
                        date=day.date,
                        affected_ids=[item.item_id, place.place_id],
                    )
                )
            else:
                signal = day.forecast.warning_signal if day.forecast else ""
                disruptions.append(
                    Disruption(
                        kind=DisruptionKind.WEATHER_WARNING,
                        detail=f"Outdoor stop {place.name} removed: warning {signal} on {day.date}",
                        date=day.date,
                        affected_ids=[item.item_id, place.place_id],
                    )
                )
        days.append(day.model_copy(update={"items": kept}))
    return plan.model_copy(update={"days": days}), disruptions


def fill_day(
    plan: TripPlan,
    day: DayPlan,
    pool: Sequence[Place],
    *,
    target: int = TARGET_ITEMS_PER_DAY,
    prefer_categories: Sequence[str] = (),
    prefer_names: Sequence[str] = (),
) -> DayPlan:
    """Top a day up to `target` stops with fetched places not used elsewhere in the plan that can
    actually happen that day (open, and indoor when a warning is in force)."""
    if len(day.items) >= target:
        return day
    used = {i.place_id for d in plan.days for i in d.items}
    wanted_categories = {normalise_name(c) for c in prefer_categories}
    wanted_names = {normalise_name(n) for n in prefer_names}

    def rank(p: Place) -> tuple[int, int, float, str]:
        return (
            0 if normalise_name(p.category) in wanted_categories else 1,
            0 if normalise_name(p.name) in wanted_names else 1,
            -(p.rating or 0.0),
            p.place_id,
        )

    candidates = sorted(
        (p for p in pool if p.place_id not in used and not _blocked(p, day)), key=rank
    )
    items = list(day.items)
    for place in candidates[: target - len(items)]:
        items.append(
            ItineraryItem(
                item_id=f"{place.place_id}@{day.date.isoformat()}",
                place_id=place.place_id,
                title=place.name,
                needs_reservation=place.needs_reservation,
            )
        )
    return day.model_copy(update={"items": items})


def resolve_disruptions(plan: TripPlan, disruptions: Iterable[Disruption]) -> list[Disruption]:
    """Deduplicate and mark each disruption resolved when the plan no longer contains the
    invalid item (closed venue gone that day, no outdoor stop under the warning, delayed ticket
    not selected)."""
    out: dict[tuple[str, str, str], Disruption] = {}
    selected = {t.ticket_id for t in plan.tickets}
    for d in disruptions:
        key = (d.kind.value, d.date.isoformat() if d.date else "", d.detail)
        day = plan.day(d.date) if d.date else None
        if d.kind is DisruptionKind.TRANSIT_DELAY:
            resolved = not (set(d.affected_ids) & selected)
            resolution = "a scheduled alternative was selected" if resolved else None
        elif day is None:
            resolved, resolution = True, "date no longer in the trip"
        elif d.kind is DisruptionKind.VENUE_CLOSED:
            resolved = not any(i.place_id in d.affected_ids for i in day.items)
            resolution = "venue replaced" if resolved else None
        else:
            outdoor = [
                i for i in day.items if (p := plan.place(i.place_id)) is not None and p.indoor is False
            ]
            resolved = not outdoor
            resolution = "outdoor stops replaced with indoor ones" if resolved else None
        out[key] = d.model_copy(update={"resolved": resolved, "resolution": resolution})
    return list(out.values())


# ------------------------------------------------------------------------------------------------
# Modify path
# ------------------------------------------------------------------------------------------------


def apply_change(context: TripContext, change: ChangeRequest) -> TripContext:
    data = context.model_dump()
    if change.start_date:
        data["start_date"] = change.start_date
        if not change.end_date and context.start_date and context.end_date:
            data["end_date"] = change.start_date + (context.end_date - context.start_date)
    if change.end_date:
        data["end_date"] = change.end_date
    data["days"] = None
    for field in ("party_size", "budget", "hotel_style"):
        value = getattr(change, field)
        if value is not None:
            data[field] = value.model_dump() if hasattr(value, "model_dump") else value
    return TripContext.model_validate(data)


def agents_for_change(change: ChangeRequest, old: TripContext, new: TripContext) -> set[AgentName]:
    """Which agents a structured change requires (the router's affected_parts are added to this)."""
    agents: set[AgentName] = set(change.refresh)
    if (old.start_date, old.end_date) != (new.start_date, new.end_date):
        agents |= {AgentName.WEATHER, AgentName.HOTEL, AgentName.TICKET}
        if (new.days or 0) > (old.days or 0):
            agents.add(AgentName.ATTRACTION)
    if change.party_size is not None and change.party_size != old.party_size:
        agents |= {AgentName.HOTEL, AgentName.TICKET}
    if change.budget is not None and change.budget != old.budget:
        agents.add(AgentName.HOTEL)
    if change.replace_hotel or change.cheaper_hotel or (change.hotel_style and change.hotel_style != old.hotel_style):
        agents.add(AgentName.HOTEL)
    if change.remove_place_ids or change.add_requests:
        agents.add(AgentName.ATTRACTION)
    return agents


@dataclass
class MergeOutcome:
    plan: TripPlan
    touched_days: set[date]
    notes: list[str]


def merge_hotel(
    plan: TripPlan,
    hotel_result: AgentResult | None,
    *,
    context: TripContext,
    change: ChangeRequest,
    dates_changed: bool,
) -> tuple[HotelStay | None, list[str]]:
    """The hotel stay after a modify turn, plus any notes for the reply. A confirmed stay is kept
    (and refreshed) unless a new hotel was asked for; a "cheaper" request never picks a pricier
    hotel; without fresh hotel data a date change drops an unconfirmed stay."""
    notes: list[str] = []
    hotel = plan.hotel
    if hotel_result is not None and hotel_result.status is SectionStatus.OK and isinstance(hotel_result.data, HotelData):
        candidates = hotel_result.data.candidates
        wants_new = change.replace_hotel or change.cheaper_hotel
        keep_confirmed = hotel is not None and hotel.confirmed and not wants_new
        if keep_confirmed and hotel is not None and context.start_date and context.end_date:
            refreshed = next((h for h in candidates if h.hotel_id == hotel.hotel.hotel_id), hotel.hotel)
            hotel = hotel.model_copy(
                update={"hotel": refreshed, "check_in": context.start_date, "check_out": context.end_date}
            )
        else:
            excluded = {plan.hotel.hotel.hotel_id} if (wants_new and plan.hotel) else set()
            hotel_pool = [h for h in candidates if h.hotel_id not in excluded]
            if change.cheaper_hotel and plan.hotel is not None:
                hotel_pool = cheaper_than(hotel_pool, plan.hotel.hotel)
            if not hotel_pool and change.cheaper_hotel and plan.hotel is not None:
                # Nothing cheaper was fetched: keep the current stay rather than pick a pricier one.
                hotel = plan.hotel
                if context.start_date and context.end_date:
                    hotel = hotel.model_copy(update={"check_in": context.start_date, "check_out": context.end_date})
                kept = f"no cheaper hotel than {plan.hotel.hotel.name} was found; kept it"
                if dates_changed:
                    kept += " (its price for the new dates was not re-checked)"
                notes.append(kept)
            else:
                hotel = choose_hotel(hotel_pool, context)
    elif dates_changed and hotel is not None and not hotel.confirmed:
        hotel = None
        notes.append("hotel needs re-checking for the new dates; hotel source unavailable")
    elif dates_changed and hotel is not None and context.start_date and context.end_date:
        hotel = hotel.model_copy(update={"check_in": context.start_date, "check_out": context.end_date})
    return hotel, notes


def _rebase_days(plan: TripPlan, context: TripContext) -> tuple[list[DayPlan], set[date], list[str]]:
    """Move day N of the old plan to day N of the new date range (dates changed)."""
    assert context.start_date and context.end_date
    span = (context.end_date - context.start_date).days + 1
    notes: list[str] = []
    days: list[DayPlan] = []
    for index in range(span):
        new_date = context.start_date + timedelta(days=index)
        old = plan.days[index] if index < len(plan.days) else None
        # Item ids are kept (they only need to be unique) so confirmations stay addressable.
        items = list(old.items) if old is not None else []
        days.append(DayPlan(date=new_date, items=items, forecast=None))
    for old in plan.days[span:]:
        if any(i.confirmed for i in old.items):
            notes.append(f"confirmed stops on {old.date} dropped: the trip is now shorter")
    return days, {d.date for d in days}, notes


def merge_modify(
    plan: TripPlan,
    results: Mapping[AgentName, AgentResult],
    *,
    context: TripContext,
    change: ChangeRequest,
    orderer: RouteOrderer,
) -> MergeOutcome:
    notes: list[str] = []
    touched: set[date] = set()
    dates_changed = (plan.start_date, plan.end_date) != (context.start_date, context.end_date)

    days = [d.model_copy(deep=True) for d in plan.days]
    if dates_changed:
        days, touched, notes = _rebase_days(plan, context)

    removed = set(change.remove_place_ids)
    if removed:
        for i, day in enumerate(days):
            if any(item.place_id in removed for item in day.items):
                days[i] = day.model_copy(
                    update={"items": [it for it in day.items if it.place_id not in removed]}
                )
                touched.add(day.date)

    places = {p.place_id: p for p in plan.places}
    pool: list[Place] = []
    attraction = results.get(AgentName.ATTRACTION)
    if attraction and attraction.status is SectionStatus.OK and isinstance(attraction.data, AttractionData):
        fresh = {p.place_id: p for p in attraction.data.places}
        places.update({pid: fresh[pid] for pid in places.keys() & fresh.keys()})  # refresh records
        pool = [p for p in attraction.data.places if p.place_id not in removed]

    weather = results.get(AgentName.WEATHER)
    if weather is not None:
        forecasts = (
            {f.date: f for f in weather.data.forecasts}
            if weather.status is SectionStatus.OK and isinstance(weather.data, WeatherData)
            else {}
        )
        days = [d.model_copy(update={"forecast": forecasts.get(d.date)}) for d in days]
    elif dates_changed:
        days = [d.model_copy(update={"forecast": None}) for d in days]

    hotel, hotel_notes = merge_hotel(
        plan, results.get(AgentName.HOTEL), context=context, change=change, dates_changed=dates_changed
    )
    notes += hotel_notes

    tickets = list(plan.tickets)
    reservations = list(plan.reservations)
    ticket_result = results.get(AgentName.TICKET)
    if ticket_result is not None and ticket_result.status is SectionStatus.OK and isinstance(ticket_result.data, TicketData):
        data = ticket_result.data
        fresh_by_id = {t.ticket_id: t for t in [*data.outbound, *data.inbound]}
        preferred = []
        for t in plan.tickets:
            current = fresh_by_id.get(t.ticket_id)
            if t.confirmed and current is not None and current.status == "scheduled":
                preferred.append(t.ticket_id)
        chosen = choose_tickets(data, context, preferred_ids=preferred)
        confirmed_ids = {t.ticket_id for t in plan.tickets if t.confirmed}
        tickets = [t.model_copy(update={"confirmed": t.ticket_id in confirmed_ids}) for t in chosen]
        reservations = list(data.reservations)
    elif dates_changed:
        tickets = [t for t in tickets if t.confirmed]

    merged = TripPlan(
        plan_id=plan.plan_id,
        version=plan.version,
        destination=plan.destination,
        origin=context.origin,
        start_date=context.start_date or plan.start_date,
        end_date=context.end_date or plan.end_date,
        party_size=context.party_size or plan.party_size,
        budget=context.budget,
        days=days,
        places=list(places.values()),
        hotel=hotel,
        tickets=tickets,
        reservations=link_reservations(reservations, list(places.values())),
        sections=plan.sections,
        disruptions=plan.disruptions,
        saved_trip_refs=plan.saved_trip_refs,
        created_at=plan.created_at,
    )

    merged, rule_disruptions = enforce_day_rules(merged)
    touched |= {d.date for d in rule_disruptions if d.date}
    if attraction is not None:
        touched |= {d.date for d in merged.days if len(d.items) < TARGET_ITEMS_PER_DAY}

    prefer = [*change.add_requests]
    new_days: list[DayPlan] = []
    for day in merged.days:
        if day.date in touched:
            filled = fill_day(merged, day, pool, prefer_categories=_categories_in(prefer, pool))
            merged = merged.model_copy(update={"days": [filled if d.date == day.date else d for d in merged.days]})
            ordered, dropped = order_and_schedule_day(merged, filled, orderer)
            if dropped:
                notes.append(f"{len(dropped)} stop(s) on {day.date} did not fit the day")
            new_days.append(ordered)
        else:
            new_days.append(day)
    merged = merged.model_copy(update={"days": new_days})

    referenced = {i.place_id for d in merged.days for i in d.items}
    pool_by_id = {p.place_id: p for p in pool}
    merged_places = [p for p in merged.places if p.place_id in referenced or p.place_id in places]
    merged_places += [pool_by_id[pid] for pid in sorted(referenced - {p.place_id for p in merged_places})]

    new_disruptions = [d for r in results.values() for d in r.disruptions] + rule_disruptions
    merged = merged.model_copy(
        update={
            "places": [p for p in merged_places if p.place_id in referenced],
            "sections": build_sections(results, previous=plan.sections),
        }
    )
    merged = merged.model_copy(
        update={"disruptions": resolve_disruptions(merged, [*plan.disruptions, *new_disruptions])}
    )
    merged = merged.model_copy(update={"cost": compute_cost(merged)})
    merged = merged.model_copy(update={"violations": check_plan(merged, context)})
    final = touch(merged, version=plan.version + 1)
    return MergeOutcome(plan=final, touched_days=touched, notes=notes)


def _categories_in(requests: Sequence[str], pool: Sequence[Place]) -> list[str]:
    words = {normalise_name(w) for r in requests for w in normalise_name(r).split()}
    words |= {w[:-1] for w in words if w.endswith("s")}
    return sorted({p.category for p in pool if normalise_name(p.category) in words})


# ------------------------------------------------------------------------------------------------
# Focused modify (plan workspace: the traveller selected one part of the plan)
# ------------------------------------------------------------------------------------------------


def _attraction_pool(results: Mapping[AgentName, AgentResult], removed: Sequence[str]) -> list[Place]:
    attraction = results.get(AgentName.ATTRACTION)
    if attraction and attraction.status is SectionStatus.OK and isinstance(attraction.data, AttractionData):
        skip = set(removed)
        return [p for p in attraction.data.places if p.place_id not in skip]
    return []


def replace_item(
    plan: TripPlan, item_id: str, pool: Sequence[Place], *, prefer_categories: Sequence[str] = ()
) -> tuple[list[DayPlan], list[str]]:
    """Swap one stop for the best pool place that is not used anywhere in the plan and can happen
    that day, keeping the stop's position and time slot. Every other stop is left as it is."""
    days = list(plan.days)
    found = next(
        ((d, p) for d, day in enumerate(days) for p, item in enumerate(day.items) if item.item_id == item_id),
        None,
    )
    if found is None:
        return days, []
    index, position = found
    day = days[index]
    old = day.items[position]
    used = {i.place_id for i in plan.all_items()}
    wanted = {normalise_name(c) for c in prefer_categories}

    def rank(p: Place) -> tuple[int, float, str]:
        return (0 if normalise_name(p.category) in wanted else 1, -(p.rating or 0.0), p.place_id)

    candidates = sorted((p for p in pool if p.place_id not in used and not _blocked(p, day)), key=rank)
    if not candidates:
        return days, [f"no other place was found to replace {old.title}; kept it"]
    place = candidates[0]
    items = list(day.items)
    items[position] = ItineraryItem(
        item_id=f"{place.place_id}@{day.date.isoformat()}",
        place_id=place.place_id,
        title=place.name,
        start_time=old.start_time,
        end_time=old.end_time,
        needs_reservation=place.needs_reservation,
    )
    days[index] = day.model_copy(update={"items": items})
    return days, []


def refill_day(
    plan: TripPlan,
    on: date,
    pool: Sequence[Place],
    *,
    removed: Sequence[str],
    add_requests: Sequence[str],
    orderer: RouteOrderer,
) -> tuple[list[DayPlan], list[str]]:
    """Change one day only: drop its `removed` places, top it up from the pool (one extra stop
    when something was asked for, preferring the asked-for categories), then re-order and re-time
    that day's unconfirmed stops. Confirmed stops and every other day are left as they are."""
    days = list(plan.days)
    day = plan.day(on)
    if day is None or not (removed or add_requests):
        return days, []
    gone = set(removed)
    kept = [i for i in day.items if i.place_id not in gone]
    trimmed = day.model_copy(update={"items": kept})
    base = plan.model_copy(update={"days": [trimmed if d.date == on else d for d in plan.days]})
    target = max(TARGET_ITEMS_PER_DAY, len(kept) + (1 if add_requests else 0))
    filled = fill_day(base, trimmed, pool, target=target, prefer_categories=_categories_in(add_requests, pool))
    base = base.model_copy(update={"days": [filled if d.date == on else d for d in base.days]})
    ordered, dropped = order_and_schedule_day(base, filled, orderer)
    notes = [f"{len(dropped)} stop(s) on {on} did not fit the day"] if dropped else []
    return [ordered if d.date == on else d for d in plan.days], notes


def replace_ticket(
    plan: TripPlan, ticket_id: str, ticket_result: AgentResult | None, *, context: TripContext, replace: bool
) -> tuple[list[TicketOption], list[str]]:
    """Change one ticket only. `replace` picks another scheduled option for the same leg (the
    agent already excluded the current one); otherwise the ticket's fresh record (status, delay)
    is taken. The other leg is kept exactly as it is; without fresh data nothing changes."""
    tickets = list(plan.tickets)
    index = next((n for n, t in enumerate(tickets) if t.ticket_id == ticket_id), None)
    if index is None:
        return tickets, []
    current = tickets[index]
    if not (ticket_result and ticket_result.status is SectionStatus.OK and isinstance(ticket_result.data, TicketData)):
        return tickets, []
    data = ticket_result.data
    if replace:
        others = [t.ticket_id for t in tickets if t.ticket_id != ticket_id]
        picked = next(
            (
                t
                for t in choose_tickets(data, context, preferred_ids=others)
                if t.direction == current.direction and t.ticket_id != ticket_id
            ),
            None,
        )
        if picked is None:
            return tickets, [f"no other scheduled {current.direction} option was found; kept {current.carrier}"]
        tickets[index] = picked.model_copy(update={"confirmed": False})
        return tickets, []
    fresh = next((t for t in [*data.outbound, *data.inbound] if t.ticket_id == ticket_id), None)
    if fresh is not None:
        tickets[index] = fresh.model_copy(update={"confirmed": current.confirmed})
    return tickets, []


def _finish_focused(
    plan: TripPlan,
    results: Mapping[AgentName, AgentResult],
    *,
    context: TripContext,
    days: list[DayPlan],
    hotel: HotelStay | None,
    tickets: list[TicketOption],
    pool: Sequence[Place],
    notes: list[str],
) -> MergeOutcome:
    referenced = {i.place_id for d in days for i in d.items}
    known = {p.place_id: p for p in plan.places}
    for p in pool:
        known.setdefault(p.place_id, p)
    places = [p for p in known.values() if p.place_id in referenced]
    merged = plan.model_copy(
        update={
            "days": days,
            "places": places,
            "hotel": hotel,
            "tickets": tickets,
            "reservations": link_reservations(plan.reservations, places),
            "sections": build_sections(results, previous=plan.sections),
        }
    )
    new_disruptions = [d for r in results.values() for d in r.disruptions]
    merged = merged.model_copy(
        update={"disruptions": resolve_disruptions(merged, [*plan.disruptions, *new_disruptions])}
    )
    merged = merged.model_copy(update={"cost": compute_cost(merged)})
    merged = merged.model_copy(update={"violations": check_plan(merged, context)})
    touched = {d.date for d, old in zip(days, plan.days, strict=True) if d != old}
    return MergeOutcome(plan=touch(merged, version=plan.version + 1), touched_days=touched, notes=notes)


def merge_focused(
    plan: TripPlan,
    results: Mapping[AgentName, AgentResult],
    *,
    context: TripContext,
    change: ChangeRequest,
    focus: PlanFocus,
    orderer: RouteOrderer,
) -> MergeOutcome:
    """Merge for a focused modify turn (design 4.1 "Modify scope"). Only the focused part may
    change; every other stop, the hotel and the tickets are kept exactly as they are, as if they
    were confirmed for this turn. Dates, party size and budget never change here (`change` was
    clamped by focus.scope_change). Cost and checks are recomputed; the version goes up by one."""
    notes: list[str] = []
    days = list(plan.days)
    hotel = plan.hotel
    tickets = list(plan.tickets)
    pool = _attraction_pool(results, change.remove_place_ids)
    if focus.kind is FocusKind.ITEM and change.replace_focus:
        days, notes = replace_item(
            plan, focus.id, pool, prefer_categories=_categories_in(change.add_requests, pool)
        )
    elif focus.kind is FocusKind.HOTEL:
        hotel, notes = merge_hotel(
            plan, results.get(AgentName.HOTEL), context=context, change=change, dates_changed=False
        )
    elif focus.kind is FocusKind.DAY:
        days, notes = refill_day(
            plan,
            date.fromisoformat(focus.id),
            pool,
            removed=change.remove_place_ids,
            add_requests=change.add_requests,
            orderer=orderer,
        )
    elif focus.kind is FocusKind.TICKET:
        tickets, notes = replace_ticket(
            plan, focus.id, results.get(AgentName.TICKET), context=context, replace=change.replace_focus
        )
    return _finish_focused(
        plan, results, context=context, days=days, hotel=hotel, tickets=tickets, pool=pool, notes=notes
    )


# ------------------------------------------------------------------------------------------------
# Confirmation (what the user accepted is preserved by the modify path)
# ------------------------------------------------------------------------------------------------


def set_confirmation(
    plan: TripPlan,
    *,
    item_ids: Sequence[str] = (),
    ticket_ids: Sequence[str] = (),
    hotel: bool | None = None,
    confirmed: bool = True,
) -> TripPlan:
    items = set(item_ids)
    tickets = set(ticket_ids)
    unknown = items - {i.item_id for i in plan.all_items()}
    unknown |= tickets - {t.ticket_id for t in plan.tickets}
    if unknown:
        raise KeyError(f"unknown ids: {sorted(unknown)}")
    days = [
        d.model_copy(
            update={
                "items": [
                    i.model_copy(update={"confirmed": confirmed}) if i.item_id in items else i
                    for i in d.items
                ]
            }
        )
        for d in plan.days
    ]
    new_tickets = [
        t.model_copy(update={"confirmed": confirmed}) if t.ticket_id in tickets else t
        for t in plan.tickets
    ]
    stay = plan.hotel
    if hotel is not None and stay is not None:
        stay = stay.model_copy(update={"confirmed": hotel if confirmed else False})
    return touch(plan, days=[d.model_dump() for d in days], tickets=[t.model_dump() for t in new_tickets], hotel=stay.model_dump() if stay else None)
