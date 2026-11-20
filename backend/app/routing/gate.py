"""The confidence gate. Below the threshold the turn asks, instead of planning or editing."""

from __future__ import annotations

from typing import Any

from app.routing.models import Intent, RouteDecision, TripSnapshot


def apply_gate(
    decision: RouteDecision,
    snapshot: TripSnapshot,
    plan_summary: dict[str, Any] | None,
    *,
    threshold: float,
    source: str,
) -> RouteDecision:
    missing = snapshot.missing_slots()
    has_plan = plan_summary is not None or snapshot.has_plan
    intent = decision.intent
    if not has_plan and intent == Intent.MODIFY:
        intent = Intent.UNCLEAR
    if decision.confidence < threshold:
        if not missing and not has_plan and intent in {Intent.UNCLEAR, Intent.PLAN}:
            return decision.replace(
                intent=Intent.PLAN, need_clarification=False, reason_code="context_complete",
                missing_slots=[], source=source,
            )
        return decision.replace(
            intent=intent, need_clarification=True, reason_code=f"below_threshold_{source}",
            missing_slots=missing, source=source,
        )
    if intent == Intent.PLAN and missing:
        return decision.replace(
            intent=Intent.PLAN, need_clarification=True,
            reason_code="missing_" + "_".join(missing[:2]), missing_slots=missing, source=source,
        )
    return decision.replace(intent=intent, missing_slots=missing, source=source)
