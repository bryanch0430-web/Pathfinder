"""Apply one named add after the place search.

The router has already chosen modify. This does not choose among plan, ask,
clarify, and modify. Place runs first. Then one outcome: assess a stop that was
written, keep a real row in the next free stretch, tell the traveller the name
was not located, or write nothing. No model is called here.

ponytail: one matching source row is placed when the usual slots are full. A name
with no coordinates is not written. An attraction without a geo fails the plan
check, and a made-up pin would be a false map point. Upgrade: store an unpinned
stop once the plan allows one.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from app.infrastructure.http import haversine_km

_USUAL = {
    ("09:00", "10:30"),
    ("10:30", "12:00"),
    ("14:00", "15:30"),
    ("15:30", "17:00"),
    ("17:00", "18:30"),
    ("18:30", "19:30"),
}
_DEFAULTS = {
    "too_far": "Heads-up: {name} is about {km} km from the other stops on day {day}.",
    "too_expensive": "Heads-up: the trip is already over its budget, and {name} was still added.",
    "time_overlap": (
        "Heads-up: day {day}'s usual times were taken, so {name} was placed at {start}–{end} "
        "instead of overlapping another stop."
    ),
    "day_runs_late": "Heads-up: {name} ends at {end}, which is after {day_end}.",
    "no_time": "Heads-up: day {day} has no free minute left, so {name} was not added.",
    "not_confirmed": "Heads-up: the place search did not return a located {name}, so it was not added.",
    "already": "Heads-up: {name} is already on the plan, so it was not added again.",
}


class ApplyState(TypedDict, total=False):
    plan: Any
    rows: list
    message: str
    day: int | None
    named: str
    added: list[str]
    warnings: list[str]
    matched: bool
    asked: str
    already: bool


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _say(template: str, **values: object) -> str:
    text = template.format_map(_Blank({key: "" if value is None else value for key, value in values.items()})).strip()
    if "heads-up:" not in text.lower():
        return f"Heads-up: {text}"
    return text


def _policy() -> tuple[float, int, dict[str, str]]:
    """Thresholds and sentences from the warn-edit skill. The file is the policy."""
    from app.application.skills import skill_by_name

    body = ""
    skill = skill_by_name("warn-edit")
    if skill is not None:
        body = skill.body
    km = 25.0
    end = 21 * 60
    found = re.search(r"^too_far_km:\s*(\d+(?:\.\d+)?)\s*$", body, re.M)
    if found:
        km = float(found.group(1))
    found = re.search(r"^day_end:\s*(\d{1,2}):(\d{2})\s*$", body, re.M)
    if found:
        end = int(found.group(1)) * 60 + int(found.group(2))
    lines = dict(_DEFAULTS)
    for key, text in re.findall(
        r"^(too_far|too_expensive|time_overlap|day_runs_late|no_time|not_confirmed|already):\s+(.+)$",
        body,
        re.M,
    ):
        lines[key] = text.strip()
    return km, end, lines


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _point(geo: Any) -> tuple[float, float] | None:
    if geo is None:
        return None
    if isinstance(geo, dict):
        lat, lng = geo.get("lat"), geo.get("lng")
    else:
        lat, lng = getattr(geo, "lat", None), getattr(geo, "lng", None)
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


def _nearest_km(plan: Any, day: Any, item: Any) -> float | None:
    here = _point(getattr(item, "geo", None))
    if here is None:
        return None
    others: list[tuple[float, float]] = []
    for other in day.items:
        if other is item:
            continue
        point = _point(getattr(other, "geo", None))
        if point:
            others.append(point)
    stay = getattr(plan, "first_stay", None)
    if stay is not None:
        point = _point(getattr(stay, "geo", None))
        if point:
            others.append(point)
    if not others:
        return None
    return min(haversine_km(here[0], here[1], lat, lng) for lat, lng in others)


def _over_budget(plan: Any) -> bool:
    cost = getattr(plan, "cost", None)
    if cost is None:
        return False
    if getattr(cost, "status", "") == "over":
        return True
    budget = getattr(plan, "budget", None)
    amount = getattr(budget, "amount", None)
    return amount is not None and float(cost.total) > float(amount)


def _concerns(
    plan: Any,
    day: Any,
    day_number: int,
    item: Any,
    slot: tuple[str, str],
    *,
    forced: bool,
) -> list[str]:
    """Facts only. A missing price or a missing pin is not filled in."""
    km_limit, day_end, lines = _policy()
    start, end = slot
    notes: list[str] = []
    if forced and (start, end) not in _USUAL:
        notes.append(_say(lines["time_overlap"], name=item.name, day=day_number, start=start, end=end))
    if _minutes(end) > day_end:
        notes.append(
            _say(lines["day_runs_late"], name=item.name, day=day_number, end=end, day_end=_hhmm(day_end))
        )
    distance = _nearest_km(plan, day, item)
    if distance is not None and distance > km_limit:
        notes.append(_say(lines["too_far"], name=item.name, day=day_number, km=f"{distance:.0f}"))
    if _over_budget(plan):
        notes.append(_say(lines["too_expensive"], name=item.name, day=day_number))
    return notes


def _locate(plan: Any, name: str) -> tuple[Any, int, Any] | None:
    found = None
    for number, day in enumerate(plan.days, start=1):
        for item in day.items:
            if item.name == name:
                found = (day, number, item)
    return found


def _days_to_try(plan: Any, message: str, day_number: int | None) -> list[tuple[Any, int]]:
    from app.application.modification import _rule_day, date_named

    number = day_number or _rule_day(message)
    if not (number and 1 <= number <= len(plan.days)):
        named_date = date_named(message, plan)
        number = next(
            (index for index, entry in enumerate(plan.days, start=1) if named_date and entry.date == named_date),
            None,
        )
    if number and 1 <= number <= len(plan.days):
        return [(plan.days[number - 1], number)]
    ordered = sorted(
        plan.days,
        key=lambda entry: sum(1 for item in entry.items if item.type.value == "attraction"),
    )
    return [(day, plan.days.index(day) + 1) for day in ordered]


def _later_span(day: Any) -> tuple[str, str] | None:
    from app.application.modification import _open_span

    for minutes in (90, 45, 30):
        slot = _open_span(day, minutes)
        if slot is not None:
            return slot
    return None


def place(state: ApplyState) -> dict:
    from app.application.modification import _add_attractions, _fresh_places, _is_a_kind, _matches_name, _named_place

    plan = state["plan"]
    message = state.get("message") or ""
    named = (state.get("named") or "").strip() or (_named_place(message) or "")
    if named and _is_a_kind(named):
        named = ""
    already = False
    if named:
        for _, item in plan.iter_items():
            labels = [item.name, *item.names.values()]
            if any(_matches_name(label, named) for label in labels if label):
                already = True
                break
    pool = _fresh_places(plan, state.get("rows") or [], message, named=named or None)
    added = _add_attractions(plan, state.get("rows") or [], message, day=state.get("day"), named=named or None)
    return {"added": added, "matched": bool(pool), "asked": named, "already": already}


def route_after_place(state: ApplyState) -> str:
    if state.get("added"):
        return "assess"
    if state.get("matched"):
        return "keep"
    if state.get("asked"):
        return "tell"
    return "hold"


def assess(state: ApplyState) -> dict:
    plan = state["plan"]
    warnings: list[str] = []
    for name in state.get("added") or []:
        found = _locate(plan, name)
        if found is None:
            continue
        day, number, item = found
        warnings.extend(_concerns(plan, day, number, item, (item.start, item.end), forced=False))
    return {"warnings": warnings}


def keep(state: ApplyState) -> dict:
    """The source row is real, and the usual slots are full. Place it in the next free stretch."""
    from app.application.modification import _append_row, _fresh_places

    plan = state["plan"]
    message = state.get("message") or ""
    asked = state.get("asked") or ""
    pool = [
        row
        for row in _fresh_places(plan, state.get("rows") or [], message, named=asked or None)
        if row.get("geo") and row.get("source_refs")
    ]
    if not pool:
        return {"added": [], "warnings": []}
    row = pool[0]
    for day, number in _days_to_try(plan, message, state.get("day")):
        slot = _later_span(day)
        if slot is None:
            continue
        name = _append_row(plan, day, row, slot[0], slot[1], seq=0)
        item = _locate(plan, name)
        warnings = _concerns(plan, day, number, item[2], slot, forced=True) if item else []
        return {"added": [name], "warnings": warnings}
    number = _days_to_try(plan, message, state.get("day"))[0][1]
    _, _, lines = _policy()
    return {
        "added": [],
        "warnings": [_say(lines["no_time"], name=row.get("name") or asked, day=number)],
    }


def tell(state: ApplyState) -> dict:
    _, _, lines = _policy()
    name = state.get("asked") or "that place"
    if state.get("already"):
        return {"warnings": [_say(lines["already"], name=name)]}
    return {"warnings": [_say(lines["not_confirmed"], name=name)]}


def run_apply(
    plan: Any,
    rows: list,
    message: str,
    *,
    day: int | None = None,
    named: str | None = None,
) -> tuple[list[str], list[str]]:
    """Place the named stop, then one follow-up. Returns names written and warning lines."""
    state: dict[str, Any] = {
        "plan": plan,
        "rows": list(rows or []),
        "message": message,
        "day": day,
        "named": named or "",
        "added": [],
        "warnings": [],
        "matched": False,
        "asked": "",
    }
    state.update(place(state))
    step = route_after_place(state)
    if step == "assess":
        state.update(assess(state))
    elif step == "keep":
        state.update(keep(state))
    elif step == "tell":
        state.update(tell(state))
    return list(state.get("added") or []), list(state.get("warnings") or [])
