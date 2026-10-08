"""Deterministic plan operations shared by the plan and modify paths.

Facts that code can decide (section status, cost, time slots, provenance) are computed here, not
by a model. This keeps the model's job to selection and scheduling, and keeps the plan's numbers
reproducible.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, time, timedelta

from backend.agents.route_order import Locate, RouteOrderer
from backend.schemas.agents import AgentResult
from backend.schemas.common import ALL_AGENTS, AgentName, GeoPoint, SectionStatus
from backend.schemas.trip_plan import (
    CostBreakdown,
    DayPlan,
    ItineraryItem,
    SectionState,
    TripPlan,
)

DAY_START = time(9, 0)
DAY_END = time(20, 0)
VISIT_MINUTES = 120
TRANSFER_MINUTES = 30
ARRIVAL_BUFFER_MINUTES = 60
DEPARTURE_BUFFER_MINUTES = 90
PEOPLE_PER_ROOM = 2
TARGET_ITEMS_PER_DAY = 3


def _add(t: time, minutes: int) -> time | None:
    total = t.hour * 60 + t.minute + minutes
    if total >= 24 * 60:
        return None
    return time(total // 60, total % 60)


def _round_up_half_hour(t: time) -> time:
    minutes = t.hour * 60 + t.minute
    rounded = int(math.ceil(minutes / 30) * 30)
    return time(min(rounded, 23 * 60 + 30) // 60, min(rounded, 23 * 60 + 30) % 60)


def day_window(plan: TripPlan, day: DayPlan) -> tuple[time, time]:
    """Earliest start and latest end for a day, respecting outbound arrival and return departure."""
    earliest, latest = DAY_START, DAY_END
    for ticket in plan.tickets:
        if ticket.direction == "outbound" and ticket.arrive_at.date() == day.date:
            arrival = _add(ticket.arrive_at.time(), ARRIVAL_BUFFER_MINUTES)
            earliest = max(earliest, _round_up_half_hour(arrival or DAY_END))
        if ticket.direction == "return" and ticket.depart_at.date() == day.date:
            leave = ticket.depart_at - timedelta(minutes=DEPARTURE_BUFFER_MINUTES)
            latest = min(latest, leave.time() if leave.date() == day.date else time(0, 0))
    return earliest, latest


def schedule_items(
    items: Sequence[ItineraryItem], *, earliest: time, latest: time
) -> tuple[list[ItineraryItem], list[ItineraryItem]]:
    """Assign sequential visit slots. Confirmed items keep their times; unconfirmed items fill
    slots after the latest confirmed end. Unconfirmed items that do not fit are returned as
    dropped (never silently scheduled into an impossible slot)."""
    confirmed = [i for i in items if i.confirmed and i.start_time and i.end_time]
    cursor = max([earliest, *[i.end_time for i in confirmed if i.end_time]])
    if confirmed:
        cursor = _add(cursor, TRANSFER_MINUTES) or DAY_END
    scheduled: list[ItineraryItem] = list(confirmed)
    dropped: list[ItineraryItem] = []
    for item in items:
        if item in confirmed:
            continue
        end = _add(cursor, VISIT_MINUTES)
        if end is None or end > latest:
            dropped.append(item)
            continue
        scheduled.append(item.model_copy(update={"start_time": cursor, "end_time": end}))
        cursor = _add(end, TRANSFER_MINUTES) or DAY_END
    scheduled.sort(key=lambda i: (i.start_time or DAY_END, i.item_id))
    return scheduled, dropped


def locator(plan: TripPlan) -> Locate:
    index = {p.place_id: p.location for p in plan.places}
    return lambda place_id: index.get(place_id)


def hotel_point(plan: TripPlan) -> GeoPoint | None:
    return plan.hotel.hotel.location if plan.hotel else None


def order_and_schedule_day(plan: TripPlan, day: DayPlan, orderer: RouteOrderer) -> tuple[DayPlan, list[ItineraryItem]]:
    """Order a day's unconfirmed stops to avoid backtracking, then time them."""
    confirmed = [i for i in day.items if i.confirmed]
    loose = [i for i in day.items if not i.confirmed]
    locate = locator(plan)
    start = hotel_point(plan)
    if confirmed:
        last = max(confirmed, key=lambda i: i.end_time or DAY_START)
        start = locate(last.place_id) or start
    ordered = orderer.order(start, loose, locate)
    earliest, latest = day_window(plan, day)
    scheduled, dropped = schedule_items([*confirmed, *ordered], earliest=earliest, latest=latest)
    return day.model_copy(update={"items": scheduled}), dropped


def build_sections(
    results: Mapping[AgentName, AgentResult], previous: Iterable[SectionState] = ()
) -> list[SectionState]:
    """Section states come from what the tools actually returned, never from the model."""
    by_agent = {s.agent: s for s in previous}
    for agent, result in results.items():
        by_agent[agent] = SectionState(
            agent=agent,
            status=result.status,
            fetched_at=result.fetched_at,
            reason=result.reason,
            tool_call_ids=result.tool_call_ids,
        )
    return [by_agent[a] for a in ALL_AGENTS if a in by_agent]


def compute_cost(plan: TripPlan) -> CostBreakdown:
    """Hotel (rooms x nights) + tickets (per person x party) + admissions (per person x party).

    Prices are only summed in the plan currency; a price in another currency makes the total
    incomplete rather than being converted with a guessed rate (there is no FX tool)."""
    currency = (
        plan.budget.currency
        if plan.budget
        else plan.hotel.hotel.nightly_price.currency
        if plan.hotel
        else next((t.price.currency for t in plan.tickets), "USD")
    )
    complete = True
    party = plan.party_size
    hotel_total = 0.0
    if plan.hotel:
        nights = (plan.hotel.check_out - plan.hotel.check_in).days
        rooms = math.ceil(party / PEOPLE_PER_ROOM)
        if plan.hotel.hotel.nightly_price.currency == currency:
            hotel_total = plan.hotel.hotel.nightly_price.amount * nights * rooms
        else:
            complete = False
    tickets_total = 0.0
    for ticket in plan.tickets:
        if ticket.price.currency == currency:
            tickets_total += ticket.price.amount * party
        else:
            complete = False
    attractions_total = 0.0
    for item in plan.all_items():
        place = plan.place(item.place_id)
        if place and place.price:
            if place.price.currency == currency:
                attractions_total += place.price.amount * party
            else:
                complete = False
    for agent in (AgentName.HOTEL, AgentName.TICKET, AgentName.ATTRACTION):
        section = plan.section(agent)
        if section is not None and section.status is not SectionStatus.OK:
            complete = False
    total = hotel_total + tickets_total + attractions_total
    return CostBreakdown(
        currency=currency,
        hotel=round(hotel_total, 2),
        tickets=round(tickets_total, 2),
        attractions=round(attractions_total, 2),
        total=round(total, 2),
        complete=complete,
    )


def touch(plan: TripPlan, **updates: object) -> TripPlan:
    """Copy with updates and a fresh updated_at, re-validated against the schema."""
    data = plan.model_dump()
    data.update(updates)
    data["updated_at"] = datetime.now(tz=plan.created_at.tzinfo)
    return TripPlan.model_validate(data)
