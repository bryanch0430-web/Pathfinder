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

    problems = validate_structure(plan, context)
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
    hours = [entry.strip() for entry in (item.opening_hours or []) if entry.strip()]
    if not hours or item.hours_status != "stated":
        return None
    weekday = WEEKDAYS[day_date.weekday()]
    if any("24 hours" in entry.lower() or "open 24" in entry.lower() for entry in hours):
        return None
    # Closure comes first: a list can say both "10:00-17:00" and "closed Monday".
    for entry in hours:
        lowered = entry.lower()
        if lowered.startswith("closed"):
            closed_days = [name for name in WEEKDAYS if name.lower() in lowered]
            if weekday in closed_days or "daily" in lowered:
                return "closed that day"
    for entry in hours:
        window = _window(entry)
        if window:
            open_minutes, close_minutes = window
            start = _minutes(item.start)
            if start is None:
                continue
            if open_minutes <= close_minutes:
                if not (open_minutes - 5 <= start <= close_minutes):
                    return f"opens {entry}"
            else:  # overnight, e.g. 18:00-02:00
                if not (start >= open_minutes or start <= close_minutes):
                    return f"opens {entry}"
            return None
    return None


def _window(entry: str) -> tuple[int, int] | None:
    text = entry.replace("–", "-").replace(".", ":")
    if "-" not in text:
        return None
    left, _, right = text.partition("-")
    first = _minutes(left.strip()[-5:])
    second = _minutes(right.strip()[:5])
    if first is None or second is None:
        return None
    return first, second


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
        return None
    payload = {
        "trip": context_brief(context),
        "constraints": constraints,
        "plan": plan_text(plan),
        "cost": {
            "total": plan.cost.total,
            "currency": plan.cost.currency,
            "budget": plan.cost.budget,
            "status": plan.cost.status,
        },
    }
    try:
        result = await llm.json(
            purpose="verify",
            system=skill("verify", skill_overrides),
            user=json.dumps(payload, ensure_ascii=False, indent=1),
            schema=VerifyResult,
        )
    except LLMError:
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
