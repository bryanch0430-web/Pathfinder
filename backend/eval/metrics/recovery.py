"""Metric 2 - error recovery on the Hong Kong disruption scenarios.

Appendix C: "Hong Kong disruption trace; code on the trace. Score 1 if the injected failure is
detected, a tool seeks a replacement, and prior constraints hold; else 0. Target >= 70% of
held-out failures."

Per scenario: plan (turn 1), inject the failure into the scenario's FaultPlan (typhoon signal,
MTR / transit delay, venue closure), age the plan's sections by `age_hours` (time passing), then
send the follow-up (turn 2). Scored by code on turn 2:
  * detected: the plan records a Disruption of the injected kind on the injected date;
  * replacement sought: the agent responsible for the replacement made a successful search call
    (places/web search for attractions, ticket search for tickets) in the follow-up trace;
  * prior constraints hold: every constraint check that passed before the failure still passes.
`resolved` (the invalid item is gone) is reported as a diagnostic but is not part of the score.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, Field

from backend.eval.harness import build_rig, eval_settings, inject_disruption
from backend.eval.metrics.constraints import QueryConstraints, evaluate_plan
from backend.eval.models import HKScenario
from backend.eval.report import MetricReport, SummaryRow, fmt_rate
from backend.eval.stats import meets, rate
from backend.schemas.common import AgentName, Route
from backend.schemas.observability import ToolCallRecord
from backend.schemas.tools import ToolOperation, ToolOutcomeStatus
from backend.schemas.trip_plan import DisruptionKind, TripPlan
from backend.settings import Settings
from backend.tools.providers.mock.faults import FaultPlan

RECOVERY_TARGET = 0.70

REPLACEMENT_OPERATIONS: dict[AgentName, frozenset[ToolOperation]] = {
    AgentName.ATTRACTION: frozenset({ToolOperation.PLACES_SEARCH, ToolOperation.WEB_SEARCH}),
    AgentName.HOTEL: frozenset({ToolOperation.PLACES_SEARCH}),
    AgentName.TICKET: frozenset({ToolOperation.TICKET_SEARCH}),
    AgentName.WEATHER: frozenset({ToolOperation.FORECAST}),
}


class RecoveryObservation(BaseModel):
    scenario_id: str
    kind: DisruptionKind
    on: date
    target_ids: list[str] = Field(default_factory=list)
    expected_agent: AgentName
    followup_route: Route
    plan_before: TripPlan
    plan_after: TripPlan | None
    followup_records: list[ToolCallRecord] = Field(default_factory=list)
    constraints_before: QueryConstraints
    constraints_after: QueryConstraints


class RecoveryScore(BaseModel):
    scenario_id: str
    kind: DisruptionKind
    injected: bool = True
    detected: bool
    replacement_sought: bool
    prior_constraints_hold: bool
    broken_constraints: list[str] = Field(default_factory=list)
    resolved: bool
    followup_route: Route | None = None
    score: int = Field(ge=0, le=1)


def _resolved(obs: RecoveryObservation) -> bool:
    plan = obs.plan_after
    if plan is None:
        return False
    day = plan.day(obs.on)
    if obs.kind is DisruptionKind.TRANSIT_DELAY:
        return not any(t.status != "scheduled" and t.depart_at.date() == obs.on for t in plan.tickets)
    if day is None:
        return True
    if obs.kind is DisruptionKind.VENUE_CLOSED:
        return not any(i.place_id in obs.target_ids for i in day.items)
    return not any((p := plan.place(i.place_id)) is not None and p.indoor is False for i in day.items)


def score_recovery(obs: RecoveryObservation) -> RecoveryScore:
    """Pure: 1 iff detected AND a replacement was sought AND prior constraints still hold."""
    plan = obs.plan_after
    detected = plan is not None and any(
        d.kind is obs.kind and (d.date is None or d.date == obs.on) for d in plan.disruptions
    )
    wanted = REPLACEMENT_OPERATIONS[obs.expected_agent]
    sought = any(
        r.agent is obs.expected_agent and r.status is ToolOutcomeStatus.OK and r.operation in wanted
        for r in obs.followup_records
    )
    after_ok = obs.constraints_after.passed_names() if plan is not None else set()
    broken = sorted(obs.constraints_before.passed_names() - after_ok)
    hold = plan is not None and not broken
    return RecoveryScore(
        scenario_id=obs.scenario_id,
        kind=obs.kind,
        detected=detected,
        replacement_sought=sought,
        prior_constraints_hold=hold,
        broken_constraints=broken,
        resolved=_resolved(obs),
        followup_route=obs.followup_route,
        score=int(detected and sought and hold),
    )


def not_injected(scenario: HKScenario) -> RecoveryScore:
    """The scenario could not be disrupted (no plan, or nothing scheduled to break): scored 0."""
    return RecoveryScore(
        scenario_id=scenario.id,
        kind=scenario.disruption.kind,
        injected=False,
        detected=False,
        replacement_sought=False,
        prior_constraints_hold=False,
        resolved=False,
        score=0,
    )


class KindRecovery(BaseModel):
    n: int
    recovered: int
    rate: float | None


class RecoveryScores(BaseModel):
    n: int
    recovered: int
    rate: float | None
    target: float = RECOVERY_TARGET
    meets_target: bool | None
    per_kind: dict[str, KindRecovery]
    detected_rate: float | None
    sought_rate: float | None
    constraints_hold_rate: float | None
    resolved_rate: float | None


def summarize_recovery(scores: Sequence[RecoveryScore]) -> RecoveryScores:
    n = len(scores)
    recovered = sum(s.score for s in scores)
    per_kind: dict[str, KindRecovery] = {}
    for kind in DisruptionKind:
        rows = [s for s in scores if s.kind is kind]
        ok = sum(s.score for s in rows)
        per_kind[kind.value] = KindRecovery(n=len(rows), recovered=ok, rate=rate(ok, len(rows)))
    value = rate(recovered, n)
    return RecoveryScores(
        n=n,
        recovered=recovered,
        rate=value,
        meets_target=meets(value, RECOVERY_TARGET),
        per_kind=per_kind,
        detected_rate=rate(sum(s.detected for s in scores), n),
        sought_rate=rate(sum(s.replacement_sought for s in scores), n),
        constraints_hold_rate=rate(sum(s.prior_constraints_hold for s in scores), n),
        resolved_rate=rate(sum(s.resolved for s in scores), n),
    )


class RecoveryReport(MetricReport):
    metric: str = "recovery"
    scores: RecoveryScores
    items: list[RecoveryScore] = Field(default_factory=list)

    def summary_row(self) -> SummaryRow:
        s = self.scores
        kinds = ", ".join(f"{k} {fmt_rate(v.rate)}" for k, v in s.per_kind.items())
        return SummaryRow(
            metric=self.metric,
            split=self.split,
            n=s.n,
            headline=f"recovered {s.recovered}/{s.n} = {fmt_rate(s.rate)} ({kinds})",
            target=">= 70.0% (held-out)",
            met=s.meets_target,
        )


async def observe_scenario(scenario: HKScenario, settings: Settings) -> RecoveryObservation | None:
    faults = FaultPlan()
    async with build_rig(settings, faults=faults) as rig:
        session = await rig.new_session(scenario.context)
        first = await rig.turn(session, scenario.initial_message)
        if first.plan is None:
            return None
        judge = rig.judge
        before = await evaluate_plan(
            scenario.id, first.plan, await rig.context_of(session), judge=judge, tool_log=rig.session_tool_log(session)
        )
        injected = inject_disruption(faults, scenario, first.plan)
        if injected is None:
            return None
        await rig.age_sections(session, scenario.age_hours)
        second = await rig.turn(session, scenario.followup)
        plan_after = (await rig.container.orchestrator.get_session(session)).plan
        after = await evaluate_plan(
            scenario.id, plan_after, await rig.context_of(session), judge=judge, tool_log=rig.session_tool_log(session)
        )
        return RecoveryObservation(
            scenario_id=scenario.id,
            kind=injected.kind,
            on=injected.on,
            target_ids=list(injected.target_ids),
            expected_agent=scenario.expected_agent,
            followup_route=second.route,
            plan_before=first.plan,
            plan_after=plan_after,
            followup_records=rig.tool_log([second.trace_id]),
            constraints_before=before,
            constraints_after=after,
        )


async def run_recovery(
    scenarios: Sequence[HKScenario],
    *,
    split_name: str = "all",
    settings: Settings | None = None,
) -> RecoveryReport:
    settings = settings or eval_settings()
    items: list[RecoveryScore] = []
    for scenario in scenarios:
        observation = await observe_scenario(scenario, settings)
        items.append(score_recovery(observation) if observation else not_injected(scenario))
    return RecoveryReport(split=split_name, scores=summarize_recovery(items), items=items)
