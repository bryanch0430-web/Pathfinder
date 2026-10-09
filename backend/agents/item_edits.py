"""Manual edits of one stop (plan workspace, design 4.2): change its times or note, move it to
another trip day, or delete it. No agent and no model runs; code re-prices and re-checks the
plan, so later AI turns see the edited plan as the single source of truth.
"""

from __future__ import annotations

from datetime import time

from backend.agents.checks import check_plan
from backend.agents.plan_ops import PlanRequestError, compute_cost, touch
from backend.schemas.edits import PlanItemPatch
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import DayPlan, ItineraryItem, TripPlan


def _editable(plan: TripPlan, item_id: str) -> tuple[DayPlan, ItineraryItem]:
    """The stop and its day; 422 when it is not in the plan, 409 when it is locked."""
    for day in plan.days:
        for item in day.items:
            if item.item_id == item_id:
                if item.confirmed:
                    raise PlanRequestError(f"{item.title} is locked: unlock it first", status_code=409)
                return day, item
    raise PlanRequestError(f"unknown item_id: {item_id}", status_code=422)


def _by_start(items: list[ItineraryItem]) -> list[ItineraryItem]:
    """Stable sort by start time; stops without a time keep their order at the end."""
    return sorted(items, key=lambda i: (i.start_time is None, i.start_time or time.min))


def apply_item_patch(plan: TripPlan, item_id: str, patch: PlanItemPatch) -> TripPlan:
    """The plan with one stop edited. Times are checked after merging with the stored ones; a
    new `day` must be a trip day (the stop is appended there); a new start time or day re-sorts
    that day by start time. Cost and checks are not recomputed here (see recompute_plan)."""
    day, item = _editable(plan, item_id)
    sent = patch.model_fields_set
    start = patch.start_time if "start_time" in sent else item.start_time
    end = patch.end_time if "end_time" in sent else item.end_time
    if start is not None and end is not None and end <= start:
        raise PlanRequestError(
            f"end_time {end:%H:%M} must be after start_time {start:%H:%M}", status_code=422
        )
    target = patch.day if patch.day is not None else day.date
    if plan.day(target) is None:
        raise PlanRequestError(
            f"day {target.isoformat()} is outside the trip "
            f"({plan.start_date.isoformat()} to {plan.end_date.isoformat()})",
            status_code=422,
        )
    updated = item.model_copy(
        update={"start_time": start, "end_time": end, "note": patch.note if "note" in sent else item.note}
    )
    resort = bool(sent & {"start_time", "day"})
    days: list[DayPlan] = []
    for d in plan.days:
        if d.date == target:
            items = (
                [updated if i.item_id == item_id else i for i in d.items]
                if d.date == day.date
                else [*d.items, updated]
            )
            days.append(d.model_copy(update={"items": _by_start(items) if resort else items}))
        elif d.date == day.date:
            days.append(d.model_copy(update={"items": [i for i in d.items if i.item_id != item_id]}))
        else:
            days.append(d)
    return plan.model_copy(update={"days": days})


def remove_item(plan: TripPlan, item_id: str) -> TripPlan:
    """The plan without one stop; its place is dropped too when no other stop uses it."""
    day, item = _editable(plan, item_id)
    days = [
        d.model_copy(update={"items": [i for i in d.items if i.item_id != item_id]}) if d.date == day.date else d
        for d in plan.days
    ]
    still_used = any(i.place_id == item.place_id for d in days for i in d.items)
    places = plan.places if still_used else [p for p in plan.places if p.place_id != item.place_id]
    return plan.model_copy(update={"days": days, "places": places})


def recompute_plan(plan: TripPlan, context: TripContext) -> TripPlan:
    """Cost and constraint checks for an edited plan, re-validated as the next version."""
    plan = plan.model_copy(update={"cost": compute_cost(plan)})
    plan = plan.model_copy(update={"violations": check_plan(plan, context)})
    return touch(plan, version=plan.version + 1)
