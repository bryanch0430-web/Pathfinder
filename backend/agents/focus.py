"""Chat focus: the one part of the plan (a day, a stop, the hotel or a ticket) a message is about.

The plan workspace lets the traveller select a part of the plan; every chat message sent while
it is selected carries it as `focus`. The focus is checked against the current plan before the
turn starts, so a turn never runs against a part that does not exist.
"""

from __future__ import annotations

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
