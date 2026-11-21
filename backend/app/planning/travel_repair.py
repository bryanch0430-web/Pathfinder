"""One deterministic repair after a travel check. A second failure stops."""

from __future__ import annotations

import copy

from app.services.route_reality import check_plan_days, leg, base_point, item_point


def _capacity(pace: str) -> float:
    return {"relaxed": 6.0, "balanced": 8.0, "packed": 11.0}.get(pace, 8.0)


def _far_item(state: dict, day: dict, mode: str) -> dict | None:
    base = base_point(state, day.get("base") or "")
    for item in day.get("items") or []:
        if item.get("option") or item.get("time_of_day") not in ("afternoon", "evening"):
            continue
        direct = leg(state, base, item_point(state, item), mode)
        if direct and direct["hours"] > 3.5:
            return item
    return None


def repair_once(days: list[dict], state: dict, mode: str, pace: str) -> tuple[list[dict], list[str], list[dict]]:
    """Move one offending stop, then check again. The second result is not repaired."""
    capacity = _capacity(pace)
    warnings = check_plan_days(state, days, mode, capacity)
    impossible = [warning for warning in warnings if warning.get("severity") == "impossible"]
    if not impossible:
        return days, [], warnings
    updated = copy.deepcopy(days)
    warning = impossible[0]
    day = next((row for row in updated if row.get("day") == warning.get("day")), None)
    if day is None:
        return days, [], warnings
    item = _far_item(state, day, mode)
    if item is None:
        real = [row for row in day.get("items") or [] if not row.get("option")]
        item = real[-1] if len(real) > 1 else None
    if item is None:
        return days, [f"Day {warning['day']} still needs a person: {warning['message']}"], warnings
    others = [row for row in updated if row is not day and row.get("base") and row.get("base") != day.get("base")]
    home = others[0] if others else next((row for row in updated if row is not day), None)
    if home is None:
        return days, [f"Day {warning['day']} still needs a person: {warning['message']}"], warnings
    day["items"] = [row for row in day.get("items") or [] if row is not item]
    item = {**item, "time_of_day": "morning"}
    home.setdefault("items", []).append(item)
    note = f"Moved {item.get('name')} from day {day.get('day')} to day {home.get('day')}: {warning['message']}"
    again = check_plan_days(state, updated, mode, capacity)
    still = [row for row in again if row.get("severity") == "impossible"]
    if still:
        note += f" Day {still[0]['day']} still does not fit, so this stops here."
    return updated, [note], again
