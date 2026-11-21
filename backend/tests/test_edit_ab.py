"""A/B: a one-place edit used to resend the whole itinerary. The patch path does not."""

from app.agents.trip_planner.nodes.planning_trip_node import build_followup_prompt
from app.planning.edit_patch import apply_edit, parse_edit
from app.planning.travel_repair import repair_once
from app.services.usage_log import estimate_usd


def _plan(days: int = 10) -> list[dict]:
    return [
        {"day": number, "base": "Osaka" if number < 6 else "Tokyo",
         "items": [{"name": f"Stop {number}a", "category": "sight", "time_of_day": "morning", "option": False},
                   {"name": f"Stop {number}b", "category": "sight", "time_of_day": "afternoon", "option": False}]}
        for number in range(1, days + 1)
    ]


def _names(day: dict) -> list[str]:
    return [item["name"] for item in day["items"]]


def compare(message: str) -> dict:
    plan = _plan()
    full = build_followup_prompt(str(plan), message, "Japan", 10, "Hong Kong", {"pace": "balanced"})
    edit = parse_edit(message, plan)
    patched, notes = apply_edit(plan, edit) if edit else (plan, [])
    touched = {(edit or {}).get("day")}
    if edit and edit["op"] == "move":
        touched.add(next(day["day"] for day in plan if any(item["name"] == edit["name"] for item in day["items"])))
    untouched = all(_names(old) == _names(new) for old, new in zip(plan, patched) if old["day"] not in touched)
    _, _, full_cost = estimate_usd("deepseek-flash", full, full)
    return {
        "parsed": bool(edit),
        "rewrite_chars": len(full),
        "rewrite_est_usd": full_cost,
        "patch_calls": 0 if notes else 1,
        "patch_chars": 0 if notes else len(full),
        "untouched": untouched,
        "note": notes[0] if notes else "",
    }


def test_add_remove_and_move_avoid_a_full_rewrite():
    reports = [compare(text) for text in (
        "add Arashiyama bamboo grove to day 2",
        "remove Stop 1b from day 1",
        "move Stop 4a to day 7",
    )]
    assert all(row["parsed"] and row["patch_calls"] == 0 and row["untouched"] for row in reports)
    assert all(row["rewrite_chars"] > 1000 for row in reports)
    assert sum(row["rewrite_est_usd"] for row in reports) > 0
    saved = sum(row["rewrite_chars"] for row in reports)
    assert saved > 3000


def test_one_travel_repair_moves_the_far_stop_and_stops():
    days = [
        {"day": 1, "base": "Osaka", "items": [
            {"name": "Dotonbori", "time_of_day": "morning", "option": False, "duration_hours": 1.5},
            {"name": "Tokyo Skytree", "time_of_day": "evening", "option": False, "duration_hours": 1.5},
        ]},
        {"day": 2, "base": "Tokyo", "items": [
            {"name": "Senso-ji", "time_of_day": "morning", "option": False, "duration_hours": 1.5},
        ]},
    ]
    state = {"route_points": {
        "base:osaka": {"lat": 34.69, "lon": 135.50},
        "base:tokyo": {"lat": 35.68, "lon": 139.69},
        "item:tokyo skytree": {"lat": 35.71, "lon": 139.81},
        "item:dotonbori": {"lat": 34.67, "lon": 135.50},
        "item:senso-ji": {"lat": 35.71, "lon": 139.80},
    }, "route_times": {}}
    repaired, notes, again = repair_once(days, state, "transit", "balanced")
    assert notes and "Tokyo Skytree" in notes[0]
    assert any(item["name"] == "Tokyo Skytree" for item in repaired[1]["items"])
    assert all(item["name"] != "Tokyo Skytree" for item in repaired[0]["items"])
    assert not any(warning.get("severity") == "impossible" and "Tokyo Skytree" in warning.get("message", "") for warning in again)
