"""Metric 6 - edit-path efficiency.

Appendix C: "paired modify traces: local edit vs full replan of the same accepted plan. Median
tokens, tool calls, latency lower on the modify path; constraint satisfaction equal or higher;
accepted items outside the edit preserved."

For each Japan scenario with an edit: plan once, accept the plan (confirm every item, ticket and
the hotel), clone the session, then run the edit ROUTED on one copy and as
`TurnMode.FULL_REPLAN` on the other. Both copies start from the identical accepted plan.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from backend.agents.orchestrator import TurnMode
from backend.eval.harness import build_rig, eval_settings
from backend.eval.metrics.constraints import QueryConstraints, evaluate_plan
from backend.eval.metrics.efficiency import TurnSample
from backend.eval.models import JapanScenario
from backend.eval.report import MetricReport, SummaryRow, fmt_num, fmt_rate, names_match
from backend.eval.stats import median, rate
from backend.schemas.common import AgentName
from backend.schemas.trip_plan import TripPlan
from backend.settings import Settings


class PreservationResult(BaseModel):
    accepted: int = Field(description="Accepted (confirmed) items, tickets and hotel before the edit")
    outside_edit: int = Field(description="... of which the edit did not ask to change")
    preserved: int
    missing: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.preserved == self.outside_edit


def check_preservation(
    before: TripPlan,
    after: TripPlan | None,
    *,
    removes: Sequence[str] = (),
    replaces: Sequence[str] = (),
) -> PreservationResult:
    """Accepted items outside the edit must survive it. An itinerary item is preserved when the
    same place is still scheduled on the same trip day (day index, so a date shift that keeps
    the days counts); the hotel and tickets when the same ids are still selected. Items whose
    place the edit removes, and the hotel / tickets when the edit replaces them, are exempt."""
    accepted: list[str] = []
    outside: list[tuple[str, str]] = []  # (kind:key, label)
    for index, day in enumerate(before.days):
        for item in day.items:
            if not item.confirmed:
                continue
            accepted.append(item.item_id)
            place = before.place(item.place_id)
            name = place.name if place else item.title
            if any(names_match(r, name) for r in removes):
                continue
            outside.append((f"item:{index}:{item.place_id}", item.item_id))
    if before.hotel is not None and before.hotel.confirmed:
        accepted.append(before.hotel.hotel.hotel_id)
        if "hotel" not in replaces:
            outside.append((f"hotel:{before.hotel.hotel.hotel_id}", before.hotel.hotel.hotel_id))
    for ticket in before.tickets:
        if ticket.confirmed:
            accepted.append(ticket.ticket_id)
            if "tickets" not in replaces:
                outside.append((f"ticket:{ticket.ticket_id}", ticket.ticket_id))
    present: set[str] = set()
    if after is not None:
        for index, day in enumerate(after.days):
            present |= {f"item:{index}:{i.place_id}" for i in day.items}
        if after.hotel is not None:
            present.add(f"hotel:{after.hotel.hotel.hotel_id}")
        present |= {f"ticket:{t.ticket_id}" for t in after.tickets}
    missing = [label for key, label in outside if key not in present]
    return PreservationResult(
        accepted=len(accepted), outside_edit=len(outside), preserved=len(outside) - len(missing), missing=missing
    )


class EditPairSample(BaseModel):
    scenario_id: str
    edit_message: str
    expected_agents: list[AgentName]
    modify: TurnSample
    full: TurnSample
    modify_constraints: QueryConstraints
    full_constraints: QueryConstraints
    modify_preservation: PreservationResult
    full_preservation: PreservationResult

    @property
    def agents_match(self) -> bool:
        """The modify path ran at least the agents the edit needs and not all four."""
        ran = set(self.modify.agents_run)
        return set(self.expected_agents) <= ran and len(ran) < 4


class PathSummary(BaseModel):
    median_tokens: float | None
    median_tool_calls: float | None
    median_latency_ms: float | None
    mean_constraint_micro: float | None
    preservation_rate: float | None = Field(description="Pairs where every accepted item outside the edit survived")


def _summary(samples: Sequence[TurnSample], constraints: Sequence[QueryConstraints], preserved: Sequence[bool]) -> PathSummary:
    micros = [c.micro for c in constraints if c.micro is not None]
    return PathSummary(
        median_tokens=median([s.tokens for s in samples]),
        median_tool_calls=median([s.tool_calls for s in samples]),
        median_latency_ms=median([s.latency_ms for s in samples]),
        mean_constraint_micro=sum(micros) / len(micros) if micros else None,
        preservation_rate=rate(sum(preserved), len(preserved)),
    )


def _lower(a: float | None, b: float | None) -> bool | None:
    return None if a is None or b is None else a < b


class EditPathScores(BaseModel):
    pairs: int
    modify: PathSummary
    full_replan: PathSummary
    modify_lower: dict[str, bool | None]
    constraints_equal_or_higher: bool | None = Field(description="Mean micro: modify >= full replan")
    pairs_constraints_equal_or_higher: float | None
    modify_preserves_all: bool | None
    agent_match_rate: float | None


def summarize_edit_path(pairs: Sequence[EditPairSample]) -> EditPathScores:
    mod = _summary(
        [p.modify for p in pairs], [p.modify_constraints for p in pairs], [p.modify_preservation.ok for p in pairs]
    )
    full = _summary([p.full for p in pairs], [p.full_constraints for p in pairs], [p.full_preservation.ok for p in pairs])
    per_pair = [
        (p.modify_constraints.micro or 0.0) >= (p.full_constraints.micro or 0.0) - 1e-9 for p in pairs
    ]
    cons = (
        None
        if mod.mean_constraint_micro is None or full.mean_constraint_micro is None
        else mod.mean_constraint_micro >= full.mean_constraint_micro - 1e-9
    )
    return EditPathScores(
        pairs=len(pairs),
        modify=mod,
        full_replan=full,
        modify_lower={
            "tokens": _lower(mod.median_tokens, full.median_tokens),
            "tool_calls": _lower(mod.median_tool_calls, full.median_tool_calls),
            "latency_ms": _lower(mod.median_latency_ms, full.median_latency_ms),
        },
        constraints_equal_or_higher=cons,
        pairs_constraints_equal_or_higher=rate(sum(per_pair), len(per_pair)),
        modify_preserves_all=None if not pairs else all(p.modify_preservation.ok for p in pairs),
        agent_match_rate=rate(sum(p.agents_match for p in pairs), len(pairs)),
    )


class EditPathReport(MetricReport):
    metric: str = "edit_path"
    scores: EditPathScores
    items: list[EditPairSample] = Field(default_factory=list)

    def summary_row(self) -> SummaryRow:
        s = self.scores
        lower = s.modify_lower
        verdicts = [lower["tokens"], lower["tool_calls"], s.constraints_equal_or_higher, s.modify_preserves_all]
        met = None if any(v is None for v in verdicts) else all(bool(v) for v in verdicts)
        return SummaryRow(
            metric=self.metric,
            split=self.split,
            n=s.pairs,
            headline=(
                f"tokens {fmt_num(s.modify.median_tokens)} vs {fmt_num(s.full_replan.median_tokens)}; "
                f"tool calls {fmt_num(s.modify.median_tool_calls)} vs {fmt_num(s.full_replan.median_tool_calls)}; "
                f"constraints {fmt_rate(s.modify.mean_constraint_micro)} vs {fmt_rate(s.full_replan.mean_constraint_micro)}; "
                f"preserved {fmt_rate(s.modify.preservation_rate)}"
            ),
            target="modify lower; constraints >=; accepted items preserved",
            met=met,
        )


async def run_edit_path(
    scenarios: Sequence[JapanScenario],
    *,
    split_name: str = "all",
    settings: Settings | None = None,
) -> EditPathReport:
    pairs: list[EditPairSample] = []
    for scenario in scenarios:
        if scenario.edit is None:
            continue
        async with build_rig(settings or eval_settings()) as rig:
            routed = await rig.new_session(scenario.context)
            first = await rig.turn(routed, scenario.message)
            if first.plan is None:
                continue
            accepted = await rig.accept_plan(routed)
            if accepted is None:
                continue
            replan = await rig.clone_session(routed)
            mod = await rig.turn(routed, scenario.edit.message, mode=TurnMode.ROUTED)
            full = await rig.turn(replan, scenario.edit.message, mode=TurnMode.FULL_REPLAN)
            judge = rig.judge
            mod_c = await evaluate_plan(
                scenario.id, mod.plan, await rig.context_of(routed), judge=judge, tool_log=rig.session_tool_log(routed)
            )
            full_c = await evaluate_plan(
                scenario.id, full.plan, await rig.context_of(replan), judge=judge, tool_log=rig.session_tool_log(replan)
            )
            edit = scenario.edit
            pairs.append(
                EditPairSample(
                    scenario_id=scenario.id,
                    edit_message=edit.message,
                    expected_agents=edit.expected_agents,
                    modify=TurnSample.from_result(scenario.id, 1, mod),
                    full=TurnSample.from_result(scenario.id, 1, full),
                    modify_constraints=mod_c,
                    full_constraints=full_c,
                    modify_preservation=check_preservation(
                        accepted, mod.plan, removes=edit.removes, replaces=edit.replaces
                    ),
                    full_preservation=check_preservation(
                        accepted, full.plan, removes=edit.removes, replaces=edit.replaces
                    ),
                )
            )
    return EditPathReport(split=split_name, scores=summarize_edit_path(pairs), items=pairs)
