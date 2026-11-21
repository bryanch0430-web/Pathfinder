"""Rewrite named days when a verb edit left the plan unchanged.

A clear add, move, replace or remove still uses the coded path. An open request
("a quieter day after the late flight") has no verb that matches, so this module
asks the writer for a day schema whose ids must already be on the trip or in the
candidate pool. Untouched days stay byte-identical.
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from pydantic import BaseModel, Field

from app.application.planner import (
    PACE_BUFFER_MINUTES,
    PACE_DWELL_MINUTES,
    _hhmm,
    _item_id,
    _long_leg_note,
    _minutes_of,
    _pace,
    _schedule,
)
from app.application.prompt_context import plan_context, plan_outline, trip_slots
from app.application.transfers import fetch_legs
from app.domain.models import ItemType, Message, PlanItem, TripContext, TripPlan
from app.security import fence_user_message, strip_fence_tags_deep

#: Confidence floor: below this the edit path still asks a clarifying question.
REWRITE_CONFIDENCE = 0.55

_OPEN_SHAPE = re.compile(
    r"\b("
    r"quiet(?:er)?|light(?:er)?|slow(?:er)?|relax(?:ed|ing)?|easy|gentle|"
    r"pack(?:ed)?|busy|full(?:er)?|more\s+time|less\s+walking|fewer\s+stops|"
    r"after\s+(?:the\s+)?(?:late\s+)?(?:flight|arrival)|"
    r"drop\s+the\s+far|keep\s+(?:the\s+)?(?:lunch|dinner|breakfast)|"
    r"only\s+one\s+(?:stop|attraction)|make\s+(?:day\s+)?\d+\s+"
    r")\b",
    re.I,
)


class DayRewriteRow(BaseModel):
    day: int
    item_ids: list[str] = Field(default_factory=list)
    dwell_minutes: list[int] = Field(default_factory=list)
    lunch_after: int | None = None
    title: str = ""
    why: str = ""


class DayRewriteDraft(BaseModel):
    summary: str = ""
    days: list[DayRewriteRow] = Field(default_factory=list)


def looks_like_open_day_request(message: str) -> bool:
    """A sentence about the shape of a day, not a single named verb."""
    text = (message or "").strip()
    if not text or text.endswith("?"):
        return False
    return _OPEN_SHAPE.search(text) is not None


def touch_days(
    plan: TripPlan,
    message: str,
    *,
    target_day: int | None,
    scope_days: Sequence[int] | None = None,
) -> list[int]:
    """Day numbers (1-based) this rewrite may change."""
    named: list[int] = []
    if scope_days:
        named.extend(int(day) for day in scope_days if isinstance(day, int))
    if target_day is not None:
        named.append(int(target_day))
    for match in re.finditer(r"\bday\s*(\d{1,2})\b", message.lower()):
        named.append(int(match.group(1)))
    cleaned = sorted({day for day in named if 1 <= day <= len(plan.days)})
    if cleaned:
        return cleaned
    # No day named: rewrite the first day that still has a free attraction.
    for index, day in enumerate(plan.days, start=1):
        if any(item.type == ItemType.ATTRACTION and not (item.locked or item.confirmed) for item in day.items):
            return [index]
    return []


def candidate_rows(plan: TripPlan, places: list[dict] | None = None) -> list[dict]:
    """Closed pool: attractions already on the plan, their alternatives, and fresh search rows."""
    by_id: dict[str, dict] = {}
    for row in places or []:
        item_id = str(row.get("id") or "")
        if item_id:
            by_id[item_id] = row
    for day in plan.days:
        for item in day.items:
            if item.type != ItemType.ATTRACTION:
                continue
            by_id.setdefault(
                item.id,
                {
                    "id": item.id,
                    "name": item.name,
                    "geo": item.geo.model_dump() if item.geo else None,
                    "address": item.address,
                    "source_types": list(item.tags),
                    "names": dict(item.names),
                    "image": item.image.model_dump() if item.image else None,
                    "opening_hours": list(item.opening_hours),
                    "step_free": item.step_free,
                },
            )
            for alt in item.alternatives:
                alt_id = str(getattr(alt, "id", "") or "")
                if not alt_id:
                    continue
                by_id.setdefault(
                    alt_id,
                    {
                        "id": alt_id,
                        "name": getattr(alt, "name", "") or alt_id,
                        "geo": alt.geo.model_dump() if getattr(alt, "geo", None) else None,
                        "address": getattr(alt, "address", "") or "",
                        "source_types": list(getattr(alt, "tags", []) or []),
                        "names": dict(getattr(alt, "names", {}) or {}),
                    },
                )
    return list(by_id.values())


def should_rewrite(
    *,
    message: str,
    draft_confidence: float,
    draft_operation: str,
    changed: bool,
    asked: bool,
) -> bool:
    """Only after a verb edit left the plan alone, and only for an open day request."""
    if changed or asked:
        return False
    if draft_operation in {"add", "move", "replace", "remove"} and draft_confidence >= 0.8:
        return False
    if draft_confidence < REWRITE_CONFIDENCE and not looks_like_open_day_request(message):
        return False
    return looks_like_open_day_request(message) or draft_confidence >= REWRITE_CONFIDENCE


async def rewrite_days(
    *,
    plan: TripPlan,
    context: TripContext,
    message: str,
    gateway: Any,
    days: Sequence[int],
    places: list[dict] | None = None,
    toolbox: Any = None,
    transfers: Any = None,
    history: Sequence[Message] = (),
) -> tuple[TripPlan, str] | None:
    """Apply a day rewrite. Returns the new plan and the chat summary, or None when nothing usable came back."""
    if not gateway or not getattr(gateway, "available", False) or not days:
        return None
    pool = candidate_rows(plan, places)
    allowed = {str(row.get("id")) for row in pool if row.get("id")}
    if not allowed:
        return None
    focus = days[0]
    payload = {
        "touch_days": list(days),
        "allowed_ids": sorted(allowed),
        "candidates": [
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "geo": row.get("geo"),
            }
            for row in pool
        ],
        "trip": trip_slots(context, preferences=True),
        "outline": plan_outline(plan.summary_for_prompt(), ids=True),
        "focus": plan_context(plan, focus=focus, notes=False),
    }
    user = (
        f"Rewrite payload:\n{json.dumps(strip_fence_tags_deep(payload), ensure_ascii=False, default=str)}\n\n"
        + fence_user_message(message)
    )
    try:
        data = await gateway.json("rewrite", user=user, purpose="rewrite.day")
        draft = DayRewriteDraft.model_validate(data)
    except Exception:
        return None
    if not draft.days:
        return None

    by_id = {str(row.get("id")): row for row in pool if row.get("id")}
    updated = plan.model_copy(deep=True)
    locked_ids = {
        item.id
        for day in plan.days
        for item in day.items
        if item.type == ItemType.ATTRACTION and (item.locked or item.confirmed)
    }
    pace = _pace(context)
    buffer = PACE_BUFFER_MINUTES.get(pace, 5)
    default_dwell = PACE_DWELL_MINUTES.get(pace, 90)

    for row in draft.days:
        if row.day not in days or not (1 <= row.day <= len(updated.days)):
            continue
        day = updated.days[row.day - 1]
        kept_locked = [item for item in day.items if item.id in locked_ids]
        chosen: list[dict] = []
        seen: set[str] = set()
        for item_id in row.item_ids:
            if item_id not in allowed or item_id in seen:
                continue
            candidate = by_id.get(item_id)
            if candidate is None:
                continue
            seen.add(item_id)
            chosen.append(candidate)
        for locked in kept_locked:
            if locked.id not in seen:
                chosen.insert(0, by_id.get(locked.id) or {
                    "id": locked.id,
                    "name": locked.name,
                    "geo": locked.geo.model_dump() if locked.geo else None,
                })
                seen.add(locked.id)
        if not chosen:
            continue
        dwell_by_id = {}
        for position, item_id in enumerate(row.item_ids):
            if position < len(row.dwell_minutes) and row.dwell_minutes[position] > 0:
                dwell_by_id[item_id] = row.dwell_minutes[position]
        lunch_after_id = None
        if row.lunch_after is not None and 0 <= row.lunch_after < len(row.item_ids):
            lunch_after_id = row.item_ids[row.lunch_after]
        meals = [item for item in day.items if item.type == ItemType.MEAL]
        lunch = _meal_row(meals, "lunch")
        dinner = _meal_row(meals, "dinner")
        breakfast = _meal_row(meals, "breakfast")
        stops = [entry for entry in [breakfast, *chosen, lunch, dinner] if entry and entry.get("geo")]
        legs, tool = await fetch_legs(
            [(_item_id(entry), _geo(entry)) for entry in stops if _geo(entry) is not None],
            toolbox=toolbox,
            source=transfers,
            step_free=False,
        )
        items, left_out = _schedule(
            day.date,
            chosen,
            lunch,
            dinner,
            pace=pace,
            legs=legs,
            tool=tool,
            breakfast=breakfast,
            dwell_by_id=dwell_by_id or None,
            lunch_after_id=lunch_after_id,
        )
        # Keep non-attraction, non-meal rows (transit) the rewrite did not touch.
        extras = [
            item
            for item in day.items
            if item.type not in {ItemType.ATTRACTION, ItemType.MEAL}
        ]
        day.items = sorted([*items, *extras], key=lambda item: (_minutes_of(item.start), 0 if item.type == ItemType.ATTRACTION else 1))
        if row.title.strip():
            day.title = row.title.strip()[:120]
        note_parts = [row.why.strip()]
        if left_out:
            note_parts.append(
                "Not scheduled, for lack of time inside the day or the stated opening hours: "
                + ", ".join(left_out)
                + "."
            )
        long_leg = _long_leg_note(chosen)
        if long_leg:
            note_parts.append(long_leg)
        day.note = " ".join(part for part in note_parts if part).strip()
        # Silence unused buffer for linters when transfers skip.
        _ = buffer, default_dwell

    summary = (draft.summary or "").strip() or "Updated the day from the stops already on this trip."
    updated.change_summary = summary
    return updated, summary


def _meal_row(meals: list[PlanItem], label: str) -> dict | None:
    needle = label.casefold()
    for item in meals:
        if needle in item.name.casefold():
            return {
                "id": item.id,
                "name": item.name,
                "geo": item.geo.model_dump() if item.geo else None,
                "address": item.address,
                "opening_hours": list(item.opening_hours),
                "names": dict(item.names),
                "image": item.image.model_dump() if item.image else None,
            }
    return None


def _geo(row: dict):
    from app.domain.models import GeoPoint

    geo = row.get("geo")
    if isinstance(geo, GeoPoint):
        return geo
    if isinstance(geo, dict) and geo.get("lat") is not None and geo.get("lng") is not None:
        return GeoPoint(lat=float(geo["lat"]), lng=float(geo["lng"]))
    return None
