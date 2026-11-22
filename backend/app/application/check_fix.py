"""Apply "ok fix" to a failed check. The skill script only reads the reply."""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.application.planner import _window_checks
from app.application.trip_rules import flight_windows
from app.domain.models import ConstraintStatus, TripPlan, utcnow


def _script():
    name = "answer_a_check"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    script = Path(__file__).resolve().parents[1] / "skills" / "answer-a-check" / "scripts" / "answer.py"
    spec = spec_from_file_location(name, script)
    if spec is None or spec.loader is None:
        return None
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _fit(day, window) -> list[str]:
    """Move stops that sit outside the flight window. Ones that cannot fit are taken off the day."""
    if window.start is None and window.end is None:
        return []
    start_at = window.start if window.start is not None else 8 * 60
    end_at = window.end if window.end is not None else 22 * 60
    cursor = start_at
    moved: list[str] = []
    kept = []
    for item in list(day.items):
        if not item.start or not item.end or item.locked or item.confirmed:
            kept.append(item)
            continue
        duration = max(30, _minutes(item.end) - _minutes(item.start))
        begins = _minutes(item.start)
        ends = _minutes(item.end)
        outside = (window.start is not None and begins < window.start) or (
            window.end is not None and ends > window.end
        )
        if not outside:
            cursor = max(cursor, ends)
            kept.append(item)
            continue
        if cursor + duration > end_at:
            moved.append(f"removed {item.name}, which could not fit around the flight")
            continue
        item.start = _hhmm(cursor)
        item.end = _hhmm(cursor + duration)
        cursor += duration
        moved.append(f"moved {item.name} to {item.start}")
        kept.append(item)
    day.items = kept
    return moved


def fix_failed_check(plan: TripPlan, message: str) -> TripPlan | None:
    """A copy of the plan with the check fixed, or None when the message is not that answer."""
    script = _script()
    if script is None:
        return None
    heard = script.answer(message)
    if not heard.get("fix"):
        return None
    updated = plan.model_copy(deep=True)
    tickets = [ticket.model_dump(mode="json") for ticket in updated.tickets]
    chosen = next((ticket for ticket in updated.tickets if ticket.selected), None)
    if chosen is not None:
        dumped = chosen.model_dump(mode="json")
        tickets = [dumped, *[row for row in tickets if row.get("id") != dumped.get("id")]]
    windows = flight_windows(tickets, [day.date for day in updated.days])
    notes: list[str] = []
    if not heard.get("relax"):
        for day in updated.days:
            window = windows.get(day.date)
            if window is not None:
                notes.extend(_fit(day, window))
    fresh = _window_checks(updated.days, windows)
    if not fresh:
        return None
    replaced = {check.text for check in fresh}
    updated.constraints = [check for check in updated.constraints if check.text not in replaced] + fresh
    if heard.get("relax"):
        for check in updated.constraints:
            if check.text in replaced:
                check.status = ConstraintStatus.PASS
                check.evidence = "Relaxed at the traveller's request. The times were left as they were."
    named = str(heard.get("check") or "").strip()
    passed = [check.text for check in fresh if check.status == ConstraintStatus.PASS]
    if updated.assessment is not None and (not named or named in passed or heard.get("relax")):
        updated.assessment.question = ""
        updated.assessment.next_action = "none"
        updated.assessment.missing = [
            line for line in updated.assessment.missing if named not in line and "drop or relax" not in line.lower()
        ]
        updated.assessment.unverified = [line for line in updated.assessment.unverified if named not in line]
    if notes:
        done = "Flight times: " + "; ".join(notes) + "."
    elif passed:
        done = "Cleared " + " and ".join(passed) + ": the days already fit the flight, so the warning was out of date."
    else:
        done = "The flight check is still not met, and no stop could be moved around it."
    updated.change_summary = done
    updated.version = plan.version + 1
    updated.updated_at = utcnow()
    return updated
