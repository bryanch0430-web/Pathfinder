"""Which city of a multi-city plan a chat edit is about.

In order: the stop or stay the traveller selected; a day the message names (or the caller already worked out); a city
named in the message. A city the trip visits twice cannot be told apart by its name, so that is a question. A message
that names nothing is about every city.
"""

from __future__ import annotations

import re
from typing import Literal

from app.application.plan_focus import focus_day
from app.domain.models import TripPlan, TripSegment


def _named(plan: TripPlan, message: str) -> list[TripSegment]:
    """The segments of the cities the message names, as whole words and in any case."""
    lower = message.lower()
    found: list[TripSegment] = []
    for segment in plan.segments:
        name = segment.destination.name.strip().lower()
        if name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lower):
            found.append(segment)
    return found


def segment_for_edit(
    plan: TripPlan, message: str, scope_item_id: str | None, target_day: int | None
) -> TripSegment | Literal["ask", "all"]:
    """The segment the edit is about, ``"ask"`` when the city it names is visited twice, ``"all"`` when it names none."""
    if len(plan.segments) == 1:
        return plan.segments[0]
    if scope_item_id:
        found = plan.find_item(scope_item_id)
        segment_id = found[0].segment_id if found else None
        if segment_id is None:
            stay = plan.find_hotel(scope_item_id)
            segment_id = stay.segment_id if stay else None
        if segment_id:
            return next(
                segment for segment in plan.segments if segment.id == segment_id
            )
    day = (
        target_day
        if target_day is not None and 1 <= target_day <= len(plan.days)
        else focus_day(plan, message, None)
    )
    if day is not None:
        segment_id = plan.days[day - 1].segment_id
        return next(segment for segment in plan.segments if segment.id == segment_id)
    named = _named(plan, message)
    if not named:
        return _by_stop_name(plan, message) or "all"
    names = {segment.destination.name.strip().lower() for segment in named}
    if len(named) > 1 and len(names) == 1:
        return "ask"
    return named[0] if len(named) == 1 else "all"


def _by_stop_name(plan: TripPlan, message: str) -> TripSegment | None:
    """The one city whose stops the message names ("remove Sumiyoshi Taisha"); None when it names none or stops of
    several cities."""
    lower = message.lower()
    found: set[str] = set()
    for day in plan.days:
        for item in day.items:
            name = item.name.strip().lower()
            if item.type.value != "transit" and len(name) >= 4 and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lower):
                found.add(day.segment_id)
    if len(found) != 1:
        return None
    return next((s for s in plan.segments if s.id in found), None)


def which_visit_question(plan: TripPlan, message: str) -> str:
    """The question for a message that names a city the trip visits twice (or more)."""
    visits = _named(plan, message)
    city = visits[0].destination.name if visits else "that city"
    spans = [f"{v.dates.start:%d %b}–{v.dates.end:%d %b}" for v in visits]
    joined = spans[0] if len(spans) == 1 else ", ".join(spans[:-1]) + f" or {spans[-1]}"
    return f"You visit {city} twice. Which visit do you mean: {joined}?"
