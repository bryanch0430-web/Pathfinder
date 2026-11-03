"""Edit-path efficiency: a local edit vs a full replan (Proposal TC-11, p4 and C1).

The same accepted plan is changed twice: once by the local edit path
(``ModifyPath``) and once by a full replan (understand, then the whole plan
path). The pair is compared on tool calls, writer calls, tokens and latency, on
how many accepted items outside the edit stay on their day, on constraint
satisfaction, and on whether the requested change was applied. Offline, on the
same metered rig as the ``efficiency`` dataset.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.application.router import affected_parts_for
from app.application.toolbox import Toolbox
from app.application.validation import validate_plan
from app.config import Settings
from app.domain.errors import PathfinderError
from app.domain.models import EvalCase, EvalCaseResult, EvalMetric, TripContext, TripPlan, new_id
from app.eval_efficiency import (
    SNAPSHOT,
    PathCost,
    Rig,
    SuiteReport,
    accepted_plan,
    build_rig,
    case_context,
    ignore_event,
    measure,
    path_metrics,
    saving,
    understood_context,
)
from app.eval_metering import RunLedger
from app.infrastructure.observability import start_trace


async def run_edit_suite(cases: list[EvalCase], settings: Settings, ledger: RunLedger) -> SuiteReport:
    rows = [await _edit_case(case, settings, ledger) for case in cases]
    return SuiteReport(results=[row[0] for row in rows], metrics=_edit_metrics([row[1] for row in rows]), snapshot=SNAPSHOT)


@dataclass
class EditOutcome:
    modify: PathCost
    replan: PathCost
    modify_kept: float
    replan_kept: float
    modify_ok: bool
    replan_ok: bool
    modify_applied: bool
    replan_applied: bool


async def modify_turn(
    rig: Rig, plan: TripPlan, context: TripContext, message: str, parts: list[str]
) -> tuple[PathCost, TripPlan | None]:
    before = rig.ledger.made.copy()
    request_id = new_id("req")
    tracer = start_trace(rig.settings, request_id)
    rig.tracer_proxy.bind(tracer)
    started = time.perf_counter()
    updated = None
    try:
        understood = await understood_context(rig, tracer, message, context.model_copy(deep=True))
        updated, _ = await rig.modify_path.run(
            plan=plan.model_copy(deep=True),
            context=understood,
            message=message,
            route_parts=parts,
            toolbox=Toolbox(path="modify", tracer=tracer, store=rig.ledger.store, request_id=request_id),
            tracer=tracer,
            emit=ignore_event,
        )
    except PathfinderError:
        updated = None
    finally:
        tracer.finish("ok")
    return measure(rig.ledger, before, request_id, tracer.generations, started), updated


async def replan_turn(rig: Rig, plan: TripPlan, context: TripContext, message: str) -> tuple[PathCost, TripPlan | None]:
    before = rig.ledger.made.copy()
    request_id = new_id("req")
    tracer = start_trace(rig.settings, request_id)
    rig.tracer_proxy.bind(tracer)
    started = time.perf_counter()
    replanned = None
    try:
        understood = await understood_context(rig, tracer, message, context.model_copy(deep=True))
        replanned = await rig.plan_path.run(
            context=understood,
            session_id=plan.session_id,
            toolbox=Toolbox(path="plan", tracer=tracer, store=rig.ledger.store, request_id=request_id),
            tracer=tracer,
            emit=ignore_event,
            version=plan.version + 1,
            trip_id=plan.trip_id,
            change_summary="Full replan",
        )
    except PathfinderError:
        replanned = None
    finally:
        tracer.finish("ok")
    return measure(rig.ledger, before, request_id, tracer.generations, started), replanned


def named_items(plan: TripPlan, message: str) -> set[str]:
    lower = message.lower()
    return {item.name for day in plan.days for item in day.items if item.name and item.name.lower() in lower}


def placements(plan: TripPlan | None) -> set[tuple[int, str]]:
    if plan is None:
        return set()
    return {(index, item.name) for index, day in enumerate(plan.days) for item in day.items}


def preservation(before: TripPlan, after: TripPlan | None, targets: set[str]) -> float:
    """Share of accepted items outside the edit that are still on the same day."""
    kept = {pair for pair in placements(before) if pair[1] not in targets}
    if not kept:
        return 1.0
    return round(len(kept & placements(after)) / len(kept), 3)


def edit_applied(before: TripPlan, after: TripPlan | None, targets: set[str], parts: list[str]) -> bool:
    if after is None:
        return False
    if targets:
        remaining = {name for _, name in placements(after)}
        return not (targets & remaining)
    if "hotel" in parts:
        return bool(before.hotels and after.hotels and after.hotels[0].name != before.hotels[0].name)
    return placements(after) != placements(before)


def constraints_hold(plan: TripPlan | None, case: EvalCase, settings: Settings) -> bool:
    if plan is None or validate_plan(plan, settings):
        return False
    if case.budget is not None and plan.cost.status == "over":
        return False
    if case.days and len(plan.days) != case.days:
        return False
    return not (case.party_size and plan.party_size != case.party_size)


async def _edit_case(case: EvalCase, settings: Settings, ledger: RunLedger) -> tuple[EvalCaseResult, EditOutcome]:
    rig = build_rig(settings, case.destination or "Kyoto", ledger)
    session = ledger.store.create_session("")
    context = case_context(case, session.id)
    plan = await accepted_plan(settings, context)
    targets = named_items(plan, case.input)
    parts = affected_parts_for(case.input)
    modify_cost, edited = await modify_turn(rig, plan, context, case.input, parts)
    replan_cost, replanned = await replan_turn(rig, plan, context, case.input)
    outcome = EditOutcome(
        modify=modify_cost,
        replan=replan_cost,
        modify_kept=preservation(plan, edited, targets),
        replan_kept=preservation(plan, replanned, targets),
        modify_ok=constraints_hold(edited, case, settings),
        replan_ok=constraints_hold(replanned, case, settings),
        modify_applied=edit_applied(plan, edited, targets, parts),
        replan_applied=edit_applied(plan, replanned, targets, parts),
    )
    failure = ""
    if edited is None:
        failure = "edit_failed"
    elif not outcome.modify_applied:
        failure = "edit_not_applied"
    elif modify_cost.api_calls >= replan_cost.api_calls:
        failure = "no_saving"
    elif outcome.modify_kept < outcome.replan_kept:
        failure = "less_preserved"
    elif outcome.replan_ok and not outcome.modify_ok:
        failure = "constraints_lost"
    passed = not failure
    tools = [f"edit · {tool} ×{n}" for tool, n in sorted(modify_cost.tools.items())]
    tools += [f"replan · {tool} ×{n}" for tool, n in sorted(replan_cost.tools.items())]
    result = EvalCaseResult(
        case_id=case.id,
        dataset=case.dataset,
        split=case.split,
        passed=passed,
        score=1.0 if passed else 0.0,
        failure_class=failure,
        input=case.input,
        output_summary=(
            f"Local edit: {modify_cost.describe()}, kept {round(outcome.modify_kept * 100)}% of other items, "
            f"edit {'applied' if outcome.modify_applied else 'not applied'}. "
            f"Full replan: {replan_cost.describe()}, kept {round(outcome.replan_kept * 100)}%, "
            f"edit {'applied' if outcome.replan_applied else 'not applied'}."
        ),
        judge_reason=(
            "Proposal TC-11: the local edit must apply the change with fewer API calls than a full "
            "replan, keep at least as many accepted items in place, and satisfy the same constraints."
        ),
        tool_summary=tools[:16],
        scores={
            "modify_tool_calls": modify_cost.tool_calls,
            "modify_llm_calls": modify_cost.llm_calls,
            "modify_tokens": modify_cost.tokens,
            "modify_latency_ms": modify_cost.latency_ms,
            "replan_tool_calls": replan_cost.tool_calls,
            "replan_llm_calls": replan_cost.llm_calls,
            "replan_tokens": replan_cost.tokens,
            "replan_latency_ms": replan_cost.latency_ms,
            "modify_preservation": outcome.modify_kept,
            "replan_preservation": outcome.replan_kept,
            "modify_constraints": 1.0 if outcome.modify_ok else 0.0,
            "replan_constraints": 1.0 if outcome.replan_ok else 0.0,
            "modify_edit_applied": 1.0 if outcome.modify_applied else 0.0,
            "replan_edit_applied": 1.0 if outcome.replan_applied else 0.0,
            "tool_calls_made": modify_cost.tool_calls + replan_cost.tool_calls,
            "tool_calls_logged": modify_cost.logged + replan_cost.logged,
        },
    )
    ledger.tokens += modify_cost.tokens + replan_cost.tokens
    return result, outcome


def _share(values: list[bool | float]) -> float:
    return round(100.0 * sum(float(value) for value in values) / len(values), 1) if values else 0.0


def _edit_metrics(outcomes: list[EditOutcome]) -> list[EvalMetric]:
    modify = [row.modify for row in outcomes]
    replan = [row.replan for row in outcomes]
    return [
        EvalMetric(
            key="edit_api_call_saving",
            label="API calls saved by the local edit",
            value=saving(sum(c.api_calls for c in modify), sum(c.api_calls for c in replan)),
        ),
        EvalMetric(
            key="edit_token_saving",
            label="Tokens saved by the local edit",
            value=saving(sum(c.tokens for c in modify), sum(c.tokens for c in replan)),
        ),
        EvalMetric(key="modify_preservation", label="Local edit · items kept in place", value=_share([row.modify_kept for row in outcomes])),
        EvalMetric(key="replan_preservation", label="Full replan · items kept in place", value=_share([row.replan_kept for row in outcomes])),
        EvalMetric(key="modify_constraints", label="Local edit · constraints met", value=_share([row.modify_ok for row in outcomes])),
        EvalMetric(key="replan_constraints", label="Full replan · constraints met", value=_share([row.replan_ok for row in outcomes])),
        EvalMetric(key="modify_edit_applied", label="Local edit · change applied", value=_share([row.modify_applied for row in outcomes])),
        EvalMetric(key="replan_edit_applied", label="Full replan · change applied", value=_share([row.replan_applied for row in outcomes])),
        *path_metrics("modify", "Local edit", modify),
        *path_metrics("replan", "Full replan", replan),
    ]
