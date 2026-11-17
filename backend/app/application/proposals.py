"""Plan changes the traveller accepts one at a time.

A modify turn can build the edited plan, but the web asks to hold it. Each
change is one tick: accept writes that part, a cross leaves it. Transit rows
are left out of the list (they are filled in on a later edit). A change in the
number of days is one change that replaces the itinerary.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.domain.models import DateRange, HotelStay, PlanItem, TicketOption, TripPlan, utcnow


def locks_to_one_row(operation: str) -> bool:
    """A named stop usually limits an edit to that row. A move changes its day, and that lock would put it back."""
    return operation != "move"


def decision_record(message: str, summary: str, changes: list[dict[str, Any]], *, held: bool) -> dict[str, Any]:
    """What the chat and the trace show for this edit: the reason, then each change still waiting."""
    reason = (summary or "").strip() or "Nothing in the plan matched that request, so it is unchanged."
    steps = [reason]
    if held and changes:
        steps.extend(str(change.get("label") or "") for change in changes[:6])
        steps.append("Nothing is written until you tick a change. A cross leaves that part as it is.")
    elif "Nothing in the plan matched" in reason:
        steps.append(
            "No stop, meal, hotel, flight, or date was different after the edit, and no more specific reason was recorded."
        )
        if re.search(r"\bmove\b", message or "", re.IGNORECASE):
            steps.append(
                "A move needs a stop that is already on the plan and a day, as in “move Shibuya Sky to day 2”."
            )
    return {"ok": False, "reason": reason, "steps": [step for step in steps if step][:8]}


def mark_decision(changes: list[dict[str, Any]], change_id: str, accept: bool) -> dict[str, Any]:
    """Mark one waiting change. Accepting one of a set of swaps rejects the others."""
    change = next((row for row in changes if row.get("id") == change_id), None)
    if change is None or change.get("status") != "pending":
        raise ValueError("not pending")
    change["status"] = "accepted" if accept else "rejected"
    group = change.get("group")
    if accept and group:
        for other in changes:
            if other is not change and other.get("group") == group and other.get("status") == "pending":
                other["status"] = "rejected"
    return change


def swap_suggestions(plan: TripPlan, item: PlanItem, day_number: int) -> list[dict[str, Any]]:
    """The day has no free time. One suggestion per unlocked stop: this stop takes its hours, and that stop goes."""
    if not 1 <= day_number <= len(plan.days):
        return []
    group = f"swap-{item.id}-{day_number}"
    changes: list[dict[str, Any]] = []
    for other in plan.days[day_number - 1].items:
        if other.id == item.id or _hidden(other) or other.locked or other.confirmed:
            continue
        moved = item.model_copy(deep=True)
        moved.start, moved.end, moved.transfer = other.start, other.end, None
        changes.append(
            {
                "id": f"swap-{other.id}",
                "label": (
                    f"Replace {other.name} on Day {day_number} ({other.start}–{other.end}) with {item.name}"
                ),
                "detail": (
                    f"Day {day_number} has no free 90-minute gap, so {item.name} would take "
                    f"{other.name}'s time and {other.name} would leave the plan."
                ),
                "status": "pending",
                "group": group,
                "puts": [{"day": day_number, "item": moved.model_dump(mode="json")}],
                "drops": [other.id],
            }
        )
    return changes


def _chosen_options(plan: TripPlan) -> list[tuple[str, str]]:
    """The option chosen on each leg (``("", id)`` for the options of a plan that has no legs)."""
    chosen: list[tuple[str, str]] = []
    for leg in plan.legs:
        options = plan.options_for(leg.id)
        picked = next((o.id for o in options if o.selected), options[0].id if options else "")
        chosen.append((leg.id, picked))
    if not plan.legs:
        chosen += [("", ticket.id) for ticket in plan.tickets if ticket.selected]
    return chosen


def is_stops_only(old: TripPlan, new: TripPlan) -> bool:
    """True when the edit changed nothing but stops: the same cities and dates, the same legs with the same chosen option on
    each, the same chosen stays, and the same party size, budget and origin."""

    def shape(plan: TripPlan) -> tuple:
        return (
            [(s.id, s.destination.name, s.dates.start, s.dates.end) for s in plan.segments],
            (plan.dates.start, plan.dates.end),
            [(leg.id, leg.kind, leg.from_place, leg.to_place, leg.depart_date) for leg in plan.legs],
            _chosen_options(plan),
            [(h.id, h.name, h.check_in, h.check_out) for h in plan.hotels],
            plan.party_size,
            (plan.budget.amount, plan.budget.currency) if plan.budget else None,
            (plan.origin or "").strip().casefold(),
        )

    return shape(old) == shape(new)


WHOLE_EDIT_DETAIL = "Accepting applies every part of this edit; rejecting keeps your plan as it is."

#: The changes that carry a whole edited plan; accepting one also writes the trip form the edit held.
WHOLE_CHANGES = ("whole-edit", "rebuild")


def _whole_edit(new: TripPlan, summary: str) -> dict[str, Any]:
    """One change that carries the whole edited plan: for an edit that is more than stops, where separate ticks would
    silently drop the rest of it."""
    label = (summary or "").strip() or (new.change_summary or "").strip() or "Apply this edit to the trip"
    return {
        "id": "whole-edit",
        "label": label,
        "detail": WHOLE_EDIT_DETAIL,
        "status": "pending",
        "puts": [],
        "drops": [],
        "plan": new.model_dump(mode="json"),
    }


def review_tier(old: TripPlan, new: TripPlan, changes: list[dict[str, Any]]) -> str:
    """What the edit asks of the traveller: ``direct`` (nothing, it changed one stop or nothing), ``stops`` (a tick for
    each of several changes to stops) or ``whole`` (one whole-edit, it was more than stops)."""
    if not is_stops_only(old, new):
        return "whole"
    if not changes:
        return "direct"
    if len(changes) == 1 and len(changes[0].get("puts") or []) <= 1:
        return "direct"
    return "stops"


def proposal_changes(old: TripPlan, new: TripPlan, *, summary: str = "") -> list[dict[str, Any]]:
    """The edits a traveller can accept: a tick for each change to stops when the edit is only stops, else one whole-edit.
    Empty when nothing they would tick has changed. ``summary`` is the text the whole-edit is labelled with."""
    if len(old.days) != len(new.days):
        return [
            {
                "id": "rebuild",
                "label": (
                    f"Change the trip from {len(old.days)} days ({old.dates.start} – {old.dates.end}) "
                    f"to {len(new.days)} days ({new.dates.start} – {new.dates.end})"
                ),
                "detail": "Accepting replaces the itinerary with the edited one.",
                "status": "pending",
                "plan": new.model_dump(mode="json"),
            }
        ]
    if not is_stops_only(old, new):
        return [_whole_edit(new, summary)]
    old_items = {item.id: (index, item) for index, item in _rows(old)}
    new_items = {item.id: (index, item) for index, item in _rows(new)}
    moves, retimes, renames, adds = [], [], [], []
    removes = [item_id for item_id in old_items if item_id not in new_items and not _hidden(old_items[item_id][1])]
    for item_id, (day, item) in new_items.items():
        if _hidden(item):
            continue
        if item_id not in old_items:
            adds.append(item_id)
            continue
        old_day, before = old_items[item_id]
        if _hidden(before):
            continue
        if old_day != day:
            moves.append(item_id)
        elif before.name != item.name:
            renames.append(item_id)
        elif (before.start, before.end) != (item.start, item.end):
            retimes.append(item_id)
    changes: list[dict[str, Any]] = []
    bundled: set[str] = set()
    if len(moves) == 1:
        item_id = moves[0]
        old_day, before = old_items[item_id]
        day, item = new_items[item_id]
        puts = [_put(day, item)]
        notes: list[str] = []
        for other_id in retimes:
            other_day, other = new_items[other_id]
            if other_day != day:
                continue
            was = old_items[other_id][1]
            puts.append(_put(other_day, other))
            notes.append(f"{other.name} moves from {was.start} to {other.start}")
            bundled.add(other_id)
        detail = ""
        if notes:
            detail = "This also changes times so the stops do not overlap: " + "; ".join(notes) + "."
        changes.append(
            {
                "id": f"move-{item_id}",
                "label": f"Move {item.name} from Day {old_day} at {before.start} to Day {day} at {item.start}",
                "detail": detail,
                "status": "pending",
                "puts": puts,
                "drops": [],
            }
        )
        bundled.add(item_id)
    for item_id in moves:
        if item_id in bundled:
            continue
        old_day, before = old_items[item_id]
        day, item = new_items[item_id]
        changes.append(
            {
                "id": f"move-{item_id}",
                "label": f"Move {item.name} from Day {old_day} at {before.start} to Day {day} at {item.start}",
                "detail": "",
                "status": "pending",
                "puts": [_put(day, item)],
                "drops": [],
            }
        )
    if len(removes) == 1 and len(adds) == 1 and old_items[removes[0]][0] == new_items[adds[0]][0]:
        removed, added = old_items[removes[0]][1], new_items[adds[0]][1]
        day = new_items[adds[0]][0]
        changes.append(
            {
                "id": f"replace-{removes[0]}",
                "label": f"Replace {removed.name} on Day {day} ({removed.start}–{removed.end}) with {added.name}",
                "detail": "",
                "status": "pending",
                "puts": [_put(day, added)],
                "drops": [removes[0]],
            }
        )
        removes, adds = [], []
    for item_id in adds:
        day, item = new_items[item_id]
        changes.append(
            {
                "id": f"add-{item_id}",
                "label": f"Add {item.name} on Day {day} at {item.start}",
                "detail": "",
                "status": "pending",
                "puts": [_put(day, item)],
                "drops": [],
            }
        )
    for item_id in removes:
        day, item = old_items[item_id]
        changes.append(
            {
                "id": f"drop-{item_id}",
                "label": f"Remove {item.name} from Day {day} ({item.start}–{item.end})",
                "detail": "",
                "status": "pending",
                "puts": [],
                "drops": [item_id],
            }
        )
    for item_id in renames:
        day, item = new_items[item_id]
        before = old_items[item_id][1]
        changes.append(
            {
                "id": f"rename-{item_id}",
                "label": f"Replace {before.name} on Day {day} with {item.name}",
                "detail": "",
                "status": "pending",
                "puts": [_put(day, item)],
                "drops": [],
            }
        )
    for item_id in retimes:
        if item_id in bundled:
            continue
        day, item = new_items[item_id]
        before = old_items[item_id][1]
        changes.append(
            {
                "id": f"time-{item_id}",
                "label": (
                    f"Change {item.name} on Day {day} from {before.start}–{before.end} to {item.start}–{item.end}"
                ),
                "detail": "",
                "status": "pending",
                "puts": [_put(day, item)],
                "drops": [],
            }
        )
    changes.extend(_stay_changes(old, new))
    changes.extend(_ticket_changes(old, new))
    changes.extend(_date_changes(old, new))
    return changes


def apply_change(plan: TripPlan, change: dict[str, Any]) -> TripPlan:
    """Write one accepted change onto a copy of the current plan."""
    label = str(change.get("label") or "Accepted a change.")
    if change.get("plan"):
        rebuilt = TripPlan.model_validate(change["plan"])
        rebuilt.session_id = plan.session_id
        rebuilt.trip_id = plan.trip_id
        rebuilt.version = plan.version + 1
        rebuilt.change_summary = label
        rebuilt.updated_at = utcnow()
        return rebuilt
    updated = plan.model_copy(deep=True)
    puts = list(change.get("puts") or [])
    drop_ids = {str(item_id) for item_id in change.get("drops") or []}
    put_ids = {str(row["item"]["id"]) for row in puts}
    gone = drop_ids | put_ids
    if gone:
        for day in updated.days:
            day.items = [item for item in day.items if item.id not in gone]
    for row in puts:
        day_number = int(row["day"])
        if not 1 <= day_number <= len(updated.days):
            raise ValueError("day")
        updated.days[day_number - 1].items.append(PlanItem.model_validate(row["item"]))
    for day in updated.days:
        day.items.sort(key=lambda item: item.start)
    segment_id = str(change.get("segment_id") or "")
    if segment_id:
        # The stay of one city of a multi-city plan: it takes the place of that city's first stay, where it stood.
        replaced = next(iter(updated.stays_in(segment_id)), None)
        if change.get("clear_hotel"):
            updated.hotels = [hotel for hotel in updated.hotels if hotel is not replaced]
        elif change.get("hotel"):
            stay = HotelStay.model_validate(change["hotel"])
            at = next((i for i, hotel in enumerate(updated.hotels) if hotel is replaced), len(updated.hotels))
            updated.hotels = [*(h for h in updated.hotels[:at] if h is not replaced), stay, *updated.hotels[at + 1 :]]
    elif change.get("clear_hotel"):
        updated.hotels = []
    elif change.get("hotel"):
        stay = HotelStay.model_validate(change["hotel"])
        # A single-city plan knows one stay (``first_stay``): it takes that stay's place.
        replaced = updated.first_stay
        updated.hotels = [stay, *(hotel for hotel in updated.hotels if hotel is not replaced)]
    if "tickets" in change:
        updated.tickets = [TicketOption.model_validate(row) for row in change["tickets"]]
    dates = change.get("dates")
    if dates:
        updated.dates = DateRange(start=date.fromisoformat(dates["start"]), end=date.fromisoformat(dates["end"]))
        for day, iso in zip(updated.days, dates["days"]):
            day.date = date.fromisoformat(iso)
    updated.version = plan.version + 1
    updated.change_summary = label
    updated.updated_at = utcnow()
    return updated


_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


#: The rows that follow the trip and are not the traveller's to move by hand.
_DERIVED_ROWS = ("transit", "ticket", "hotel")


def apply_layout(plan: TripPlan, moves: list[dict[str, Any]]) -> TripPlan:
    """Move stops to the day and times the traveller set in the grid. Unlisted rows stay put.

    A ``ValueError`` with a sentence the page can show refuses a layout that would break the plan: a locked or confirmed
    stop that moves, a travel, ticket or hotel row that moves, a stop on a day of another city, two items that overlap.
    Transfers between stops are worked out again by the caller.
    """
    home: dict[str, tuple[int, Any]] = {}
    for index, day in enumerate(plan.days, start=1):
        for item in day.items:
            home[item.id] = (index, item)
    wanted: dict[str, tuple[int, str, str]] = {}
    for move in moves:
        item_id = str(move.get("id") or "")
        found = home.get(item_id)
        if found is None:
            raise ValueError("One of those stops is no longer in the plan.")
        day_number, item = found
        start, end = str(move.get("start") or ""), str(move.get("end") or "")
        target = int(move.get("day") or 0)
        if _CLOCK.match(start) is None or _CLOCK.match(end) is None or start >= end:
            raise ValueError(f"{item.name} needs a start time that is before its end time.")
        if not 1 <= target <= len(plan.days):
            raise ValueError("That day is not in this trip.")
        if target == day_number and start == item.start and end == item.end:
            continue
        kind = getattr(item.type, "value", str(item.type))
        if kind in _DERIVED_ROWS:
            raise ValueError(f"{item.name} is a {kind} row; it follows the trip and cannot be moved by hand.")
        if item.locked or item.confirmed:
            raise ValueError(f"{item.name} is locked or confirmed; unlock it before moving it.")
        here, there = plan.days[day_number - 1].segment_id, plan.days[target - 1].segment_id
        city = next((s.destination.name for s in plan.segments if s.id == here), "")
        if here and there and here != there and city:
            raise ValueError(f"{item.name} is in {city}; it can only move to a day in {city}")
        wanted[item_id] = (target, start, end)
    if not wanted:
        return plan
    updated = plan.model_copy(deep=True)
    taken = []
    for day in updated.days:
        stay = []
        for item in day.items:
            if item.id in wanted:
                taken.append(item)
            else:
                stay.append(item)
        day.items = stay
    for item in taken:
        target, start, end = wanted[item.id]
        item.start, item.end, item.transfer = start, end, None
        updated.days[target - 1].items.append(item)
    for day in updated.days:
        day.items.sort(key=lambda item: item.start)
    touched = {home[item_id][0] - 1 for item_id in wanted} | {target - 1 for target, _, _ in wanted.values()}
    for index in sorted(touched):
        day = updated.days[index]
        for previous, current in zip(day.items, day.items[1:]):
            if current.start < previous.end:
                raise ValueError(f"{previous.name} and {current.name} would overlap on {day.date}")
    updated.version = plan.version + 1
    updated.change_summary = "Direct edit"
    updated.updated_at = utcnow()
    return updated


def _rows(plan: TripPlan):
    for index, day in enumerate(plan.days, start=1):
        for item in day.items:
            yield index, item


def _hidden(item: PlanItem) -> bool:
    return getattr(item.type, "value", str(item.type)) == "transit"


def _put(day: int, item: PlanItem) -> dict[str, Any]:
    return {"day": day, "item": item.model_dump(mode="json")}


def _stay_changes(old: TripPlan, new: TripPlan) -> list[dict[str, Any]]:
    """The change to the stay: one on a single-city plan, one for each city's stay on a multi-city plan."""
    if len(old.segments) < 2 and len(new.segments) < 2:
        return _stay_change(old.first_stay, new.first_stay, "hotel", "")
    changes: list[dict[str, Any]] = []
    for number, segment in enumerate(new.segments):
        before, after = next(iter(old.stays_in(segment.id)), None), next(iter(new.stays_in(segment.id)), None)
        change_id = "hotel" if number == 0 else f"hotel-{segment.id}"
        changes.extend(_stay_change(before, after, change_id, segment.id))
    return changes


def _stay_change(
    before: HotelStay | None, after: HotelStay | None, change_id: str, segment_id: str
) -> list[dict[str, Any]]:
    if before is None and after is None:
        return []
    old_name = before.name if before else ""
    new_name = after.name if after else ""
    if old_name != new_name:
        label = f"Change the hotel from {old_name or 'none'} to {new_name or 'none'}"
    elif before and after and before.price_per_night != after.price_per_night:
        label = f"Change the nightly rate at {new_name}"
    elif before and after and (before.check_in, before.check_out) != (after.check_in, after.check_out):
        label = f"Change the stay at {new_name} from {before.check_in} – {before.check_out} to {after.check_in} – {after.check_out}"
    else:
        return []
    change: dict[str, Any] = {
        "id": change_id,
        "label": label,
        "detail": "",
        "status": "pending",
        "puts": [],
        "drops": [],
    }
    if after is None:
        change["clear_hotel"] = True
    else:
        change["hotel"] = after.model_dump(mode="json")
    if segment_id:
        change["segment_id"] = segment_id
    return [change]


def _ticket_changes(old: TripPlan, new: TripPlan) -> list[dict[str, Any]]:
    old_name = old.tickets[0].name if old.tickets else ""
    new_name = new.tickets[0].name if new.tickets else ""
    if old_name == new_name:
        return []
    return [
        {
            "id": "flight",
            "label": f"Change the flight from {old_name or 'none'} to {new_name or 'none'}",
            "detail": "",
            "status": "pending",
            "puts": [],
            "drops": [],
            "tickets": [ticket.model_dump(mode="json") for ticket in new.tickets],
        }
    ]


def _date_changes(old: TripPlan, new: TripPlan) -> list[dict[str, Any]]:
    if [day.date for day in old.days] == [day.date for day in new.days]:
        return []
    return [
        {
            "id": "dates",
            "label": f"Move the trip dates from {old.dates.start} – {old.dates.end} to {new.dates.start} – {new.dates.end}",
            "detail": "",
            "status": "pending",
            "puts": [],
            "drops": [],
            "dates": {
                "start": new.dates.start.isoformat(),
                "end": new.dates.end.isoformat(),
                "days": [day.date.isoformat() for day in new.days],
            },
        }
    ]
