"""Validation: deterministic checks first, then the model's constraint audit.

Two layers, for two different jobs:

* :func:`check_structure` is code on structured facts (overlap, transfer time,
  opening hours, budget, closed stops).  It is cheap and never wrong about what it
  can see.
* :func:`audit_constraints` asks the verifier skill to judge the traveller's hard
  constraints against the plan, returning pass/fail/unknown with evidence.  It is
  used for meaning ("must include Fushimi Inari"), not for arithmetic.

Any problem is fed back to the planner for a bounded repair; after the budget the
plan ships with the problem surfaced as a gap or a failed check, never silently.
"""

from __future__ import annotations

import json
import re
from datetime import date

from app.core.dates import minutes_between, parse_date
from app.core.llm import LLMClient, LLMError
from app.core.prompts import context_brief, plan_text, skill
from app.core.trace import TurnTrace
from app.domain.models import ConstraintCheck, TripContext, TripPlan
from app.plan.builder import Problem
from app.understand.schemas import VerifyResult

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def check_structure(plan: TripPlan, context: TripContext) -> list[Problem]:
    from app.plan.builder import validate_structure
    from app.plan.continuity import route_continuity_problems
    from app.plan.flight_windows import flight_boundary_problems

    problems = validate_structure(plan, context)
    problems.extend(route_continuity_problems(plan, context))
    problems.extend(flight_boundary_problems(plan, context))
    for index, day in enumerate(plan.days, start=1):
        day_date = parse_date(day.date)
        if day_date is None:
            problems.append(Problem(code="bad_date", message=f"Day {index} has an invalid date.", day=index))
            continue
        for item in day.items:
            reason = hours_violation(item, day_date)
            if reason:
                problems.append(Problem(code="closed_at_time", message=f"Day {index}: {item.name} — {reason}", day=index, item_id=item.id))
    return problems[:16]


def hours_violation(item, day_date: date) -> str | None:
    """Why this stop's scheduled time clashes with its stated opening hours, if it does.

    Checks the day (closed days), the start (inside an opening window) and the end
    (not after closing).  Unknown or unparseable hours are never a violation.
    """
    windows = hours_windows(item, day_date)
    if windows is None:
        return None
    label = ", ".join(entry for entry in _day_entries(item, day_date)) or "stated hours"
    if windows == []:
        return "closed that day"
    start = _minutes(item.start)
    if start is None:
        return None
    end = _minutes(item.end)
    for open_minutes, close_minutes in windows:
        if open_minutes <= close_minutes:
            if open_minutes - 5 <= start <= close_minutes:
                if end is not None and end > start and end > close_minutes:
                    return f"closes at {_clock(close_minutes)} ({label})"
                return None
        elif start >= open_minutes or start <= close_minutes:  # overnight, e.g. 18:00-02:00
            return None
    return f"opens {label}"


def fit_to_hours(item, day_date: date, minimum: int = 30) -> str | None:
    """Fit a stop into its opening hours on that date; None when it fits, else why not.

    A start before opening moves to opening time (length kept); an end after closing
    is clamped to closing.  When less than ``minimum`` minutes remain before closing
    nothing is invented: the reason is returned so the caller can surface it.
    """
    windows = hours_windows(item, day_date)
    if windows is None:
        return None
    if windows == []:
        return "closed that day"
    start, end = _minutes(item.start), _minutes(item.end)
    if start is None or end is None:
        return None
    if any(open_minutes > close_minutes for open_minutes, close_minutes in windows):
        return None
    length = max(end - start, minimum)
    if not any(o - 5 <= start <= c for o, c in windows):
        later = sorted(pair for pair in windows if pair[0] > start)
        if not later:
            return hours_violation(item, day_date)
        start = later[0][0]
        end = start + length
        item.start, item.end = _clock(start), _clock(end)
    close_minutes = next(c for o, c in windows if o - 5 <= start <= c)
    if end > close_minutes:
        if close_minutes - start < minimum:
            return f"closes at {_clock(close_minutes)}, too soon after {item.start} to fit a visit"
        item.end = _clock(close_minutes)
    return None


def hours_windows(item, day_date: date) -> list[tuple[int, int]] | None:
    """Opening windows (minutes) for that date; [] means closed, None means unknown."""
    hours = [_clean(entry) for entry in (item.opening_hours or []) if entry and entry.strip()]
    if not hours or item.hours_status != "stated":
        return None
    if any("24 hours" in entry.lower() or "open 24" in entry.lower() for entry in hours):
        return None
    weekday = WEEKDAYS[day_date.weekday()]
    # Closure comes first: a list can say both "10:00-17:00" and "closed Monday".
    for entry in hours:
        lowered = entry.lower()
        if lowered.startswith("closed"):
            closed_days = [name for name in WEEKDAYS if name.lower() in lowered]
            if weekday in closed_days or "daily" in lowered:
                return []
    entries = _day_entries(item, day_date)
    if not entries:
        return None
    windows: list[tuple[int, int]] = []
    for entry in entries:
        if entry.lower().startswith("closed"):
            continue
        windows.extend(_windows(entry))
    if not windows:
        return [] if any(entry.lower().startswith("closed") for entry in entries) else None
    return windows


def _day_entries(item, day_date: date) -> list[str]:
    """The hour lines that apply to the date, without the weekday label."""
    hours = [_clean(entry) for entry in (item.opening_hours or []) if entry and entry.strip()]
    weekday = WEEKDAYS[day_date.weekday()]
    labelled = [(_label(entry), entry) for entry in hours]
    if any(day for day, _ in labelled):
        return [entry.partition(":")[2].strip() for day, entry in labelled if day == weekday]
    return [entry for entry in hours if not entry.lower().startswith("closed")]


def _label(entry: str) -> str | None:
    head = entry.partition(":")[0].strip().lower()
    for name in WEEKDAYS:
        if head == name.lower():
            return name
    return None


def _clean(entry: str) -> str:
    # Google writes narrow no-break spaces and en dashes in its hour lines.
    for code in (0x202F, 0x2009, 0xA0):
        entry = entry.replace(chr(code), " ")
    return entry.replace(chr(0x2013), "-").replace(chr(0x2014), "-").strip()


_RANGE = re.compile(
    r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\s*(?:-|to)\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?",
    re.IGNORECASE,
)


def _windows(entry: str) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for match in _RANGE.finditer(entry):
        h1, m1, s1, h2, m2, s2 = match.groups()
        first_hour, second_hour = int(h1), int(h2)
        if first_hour > 24 or second_hour > 24:
            continue
        if s2 and not s1:
            s1 = s2
            if _to_24(first_hour, s1) > _to_24(second_hour, s2):
                s1 = "am"
        first = _to_24(first_hour, s1) * 60 + int(m1 or 0)
        second = _to_24(second_hour, s2) * 60 + int(m2 or 0)
        if second == 0 and first > 0:
            second = 24 * 60
        found.append((first, second))
    return found


def _to_24(hour: int, suffix: str | None) -> int:
    if not suffix:
        return hour
    hour %= 12
    return hour + (12 if suffix.lower() == "pm" else 0)


def _clock(minutes: int) -> str:
    minutes = max(0, min(minutes, 24 * 60 - 1))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _minutes(value: str) -> int | None:
    parts = (value or "").strip().split(":")
    if len(parts) < 2 or not parts[0].strip().isdigit() or not parts[1].strip().isdigit():
        return None
    return int(parts[0]) * 60 + int(parts[1])


async def audit_constraints(
    *,
    llm: LLMClient,
    plan: TripPlan,
    context: TripContext,
    trace: TurnTrace | None = None,
    skill_overrides: dict[str, str] | None = None,
) -> VerifyResult | None:
    constraints = [check.text for check in plan.constraints if check.text]
    if not constraints:
        if trace:
            trace.say("Constraint audit: no hard constraints to check.")
        return None
    payload = {
        "trip": context_brief(context),
        "constraints": constraints,
        "plan": plan_text(plan, with_sources=True),
        "cost": {
            "total": plan.cost.total,
            "currency": plan.cost.currency,
            "budget": plan.cost.budget,
            "status": plan.cost.status,
        },
    }
    try:
        result = await llm.json(
            purpose="verifier",
            system=skill("verify", skill_overrides),
            user=json.dumps(payload, ensure_ascii=False, indent=1),
            schema=VerifyResult,
        )
    except LLMError as exc:
        if trace:
            trace.say(f"Constraint audit unavailable: {str(exc)[:180]}")
        return None
    status_by_text = {row.text.strip().lower(): row for row in result.checks if row.text}
    updated: list[ConstraintCheck] = []
    for check in plan.constraints:
        row = status_by_text.get(check.text.strip().lower())
        if row is None:
            updated.append(check)
        else:
            updated.append(
                check.model_copy(update={"status": row.status, "evidence": row.evidence[:300] or check.evidence})
            )
    plan.constraints = updated
    if trace:
        failed = sum(1 for check in updated if check.status == "fail")
        trace.say(
            f"Constraint audit: {sum(1 for c in updated if c.status == 'pass')} pass, "
            f"{failed} fail, {sum(1 for c in updated if c.status == 'unknown')} unknown."
        )
    return result


def problems_from_verify(result: VerifyResult | None) -> list[Problem]:
    if result is None:
        return []
    problems = [Problem(code="constraint_failed", message=text) for text in result.problems if text]
    for row in result.checks:
        if row.status == "fail":
            problems.append(Problem(code="constraint_failed", message=f"Constraint not met: {row.text}. {row.evidence}"))
    return problems[:8]
