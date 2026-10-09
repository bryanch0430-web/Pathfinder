"""Chat focus: the one part of the plan (a day, a stop, the hotel or a ticket) a message is about.

The plan workspace lets the traveller select a part of the plan; every chat message sent while
it is selected carries it as `focus`. The focus is checked against the current plan before the
turn starts, so a turn never runs against a part that does not exist.
"""

from __future__ import annotations

import json
from datetime import time

from backend.agents.plan_ops import PlanRequestError
from backend.schemas.routing import FocusKind, PlanFocus
from backend.schemas.trip_plan import TripPlan


def focus_ids(plan: TripPlan, kind: FocusKind) -> set[str]:
    """Every id of that kind in the plan: ISO dates, item ids, the hotel id, ticket ids."""
    if kind is FocusKind.DAY:
        return {d.date.isoformat() for d in plan.days}
    if kind is FocusKind.ITEM:
        return {i.item_id for i in plan.all_items()}
    if kind is FocusKind.HOTEL:
        return {plan.hotel.hotel.hotel_id} if plan.hotel else set()
    return {t.ticket_id for t in plan.tickets}


def check_focus(plan: TripPlan | None, focus: PlanFocus) -> None:
    """Raise PlanRequestError when the focus cannot be served: 409 without a plan, 422 when the
    id is not in the current plan (for example a selection left over from an older plan)."""
    if plan is None:
        raise PlanRequestError("session has no plan", status_code=409)
    if focus.id not in focus_ids(plan, focus.kind):
        raise PlanRequestError(
            f"focus {focus.kind.value} '{focus.id}' is not in the current plan", status_code=422
        )


def _hhmm(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


def focus_summary(plan: TripPlan, focus: PlanFocus) -> str:
    """The focused part as JSON for a fenced `focus` prompt block. It is built from plan data only
    (never from the traveller's message or notes) and carries a one-line `label` to quote."""
    check_focus(plan, focus)
    data: dict[str, object] = {"kind": focus.kind.value, "id": focus.id}
    if focus.kind is FocusKind.DAY:
        number, day = next((n, d) for n, d in enumerate(plan.days, start=1) if d.date.isoformat() == focus.id)
        titles = [i.title for i in day.items]
        data |= {
            "label": f"Day {number} ({focus.id}): " + (", ".join(titles) or "no stops"),
            "date": focus.id,
            "stops": [
                {
                    "item_id": i.item_id,
                    "title": i.title,
                    "start_time": _hhmm(i.start_time),
                    "end_time": _hhmm(i.end_time),
                    "confirmed": i.confirmed,
                }
                for i in day.items
            ],
            "forecast": day.forecast.summary if day.forecast else None,
        }
    elif focus.kind is FocusKind.ITEM:
        day, item = next((d, i) for d in plan.days for i in d.items if i.item_id == focus.id)
        place = plan.place(item.place_id)
        start, end = _hhmm(item.start_time), _hhmm(item.end_time)
        data |= {
            "label": f"{item.title} on {day.date.isoformat()}" + (f", {start}-{end}" if start and end else ""),
            "date": day.date.isoformat(),
            "place_id": item.place_id,
            "title": item.title,
            "category": place.category if place else None,
            "start_time": start,
            "end_time": end,
            "confirmed": item.confirmed,
        }
    elif focus.kind is FocusKind.HOTEL:
        assert plan.hotel is not None  # check_focus guarantees it
        hotel = plan.hotel.hotel
        price = hotel.nightly_price
        data |= {
            "label": f"{hotel.name}, {price.amount:.0f} {price.currency} per night",
            "name": hotel.name,
            "nightly_price": price.model_dump(),
            "check_in": plan.hotel.check_in.isoformat(),
            "check_out": plan.hotel.check_out.isoformat(),
            "confirmed": plan.hotel.confirmed,
        }
    else:
        ticket = next(t for t in plan.tickets if t.ticket_id == focus.id)
        data |= {
            "label": (
                f"{ticket.direction} {ticket.mode} {ticket.origin} to {ticket.destination}, "
                f"{ticket.depart_at:%Y-%m-%d %H:%M}"
            ),
            "direction": ticket.direction,
            "mode": ticket.mode,
            "carrier": ticket.carrier,
            "depart_at": ticket.depart_at.isoformat(),
            "arrive_at": ticket.arrive_at.isoformat(),
            "status": ticket.status,
            "confirmed": ticket.confirmed,
        }
    return json.dumps(data, ensure_ascii=False)
