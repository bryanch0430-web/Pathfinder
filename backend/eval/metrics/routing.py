"""Metric 5 - routing accuracy (System 1 alone).

Appendix C: "gold label on 80 utterances. >= 90% on the correct path (new trip, modify, quick
question, unclear). Below the configured confidence threshold the turn must ask, not plan."

The router is scored ALONE ("A small labelled set scores the router alone"): each utterance is
routed through `TurnOrchestrator.router.route` (model decision + clarification gate) against a
session whose context is complete or not, and which holds a minimal plan when `has_plan`. The
gold label is the path the turn must take, so a plan request with missing key fields is gold
`unclear`.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from backend.eval.harness import build_rig, complete_context, eval_settings, stub_plan
from backend.eval.models import RouterLabel
from backend.eval.report import MetricReport, SummaryRow, fmt_rate
from backend.eval.stats import meets, rate
from backend.schemas.common import Route
from backend.schemas.routing import GateReason
from backend.schemas.trip import TripContext
from backend.settings import Settings

ROUTING_TARGET = 0.90


class RoutingObservation(BaseModel):
    id: str
    gold: Route
    predicted: Route
    reason: GateReason
    confidence: float | None = Field(default=None, description="None when the output was rejected")


class LabelScore(BaseModel):
    n: int
    correct: int
    accuracy: float | None


class RoutingScores(BaseModel):
    n: int
    correct: int
    accuracy: float | None
    target: float = ROUTING_TARGET
    meets_target: bool | None
    threshold: float
    below_threshold: int = Field(description="Decisions with confidence below the threshold")
    below_threshold_asked: int = Field(description="... of which the turn asked (route unclear)")
    threshold_rule_holds: bool
    per_label: dict[str, LabelScore]
    confusion: dict[str, dict[str, int]] = Field(description="gold -> predicted -> count")


def score_routing(observations: Sequence[RoutingObservation], *, threshold: float) -> RoutingScores:
    """Accuracy vs gold, per label, confusion matrix, and the threshold rule: every decision
    below the confidence threshold must have become `unclear` (ask, not plan)."""
    correct = sum(o.predicted is o.gold for o in observations)
    per_label: dict[str, LabelScore] = {}
    confusion: dict[str, dict[str, int]] = {g.value: {p.value: 0 for p in Route} for g in Route}
    for gold in Route:
        rows = [o for o in observations if o.gold is gold]
        ok = sum(o.predicted is gold for o in rows)
        per_label[gold.value] = LabelScore(n=len(rows), correct=ok, accuracy=rate(ok, len(rows)))
    for o in observations:
        confusion[o.gold.value][o.predicted.value] += 1
    below = [o for o in observations if o.confidence is not None and o.confidence < threshold]
    asked = sum(o.predicted is Route.UNCLEAR for o in below)
    accuracy = rate(correct, len(observations))
    return RoutingScores(
        n=len(observations),
        correct=correct,
        accuracy=accuracy,
        meets_target=meets(accuracy, ROUTING_TARGET),
        threshold=threshold,
        below_threshold=len(below),
        below_threshold_asked=asked,
        threshold_rule_holds=asked == len(below),
        per_label=per_label,
        confusion=confusion,
    )


class RoutingReport(MetricReport):
    metric: str = "routing"
    scores: RoutingScores
    items: list[RoutingObservation] = Field(default_factory=list)

    def summary_row(self) -> SummaryRow:
        s = self.scores
        rule = "holds" if s.threshold_rule_holds else "VIOLATED"
        return SummaryRow(
            metric=self.metric,
            split=self.split,
            n=s.n,
            headline=f"accuracy {fmt_rate(s.accuracy)}; below-threshold->ask {s.below_threshold_asked}/{s.below_threshold} ({rule})",
            target=">= 90.0% and threshold rule",
            met=None if s.meets_target is None else (s.meets_target and s.threshold_rule_holds),
        )


def routing_context(label: RouterLabel) -> TripContext:
    """Complete context (destination, dates, party, budget), or destination only."""
    full = complete_context(label.city)
    return full if label.context_complete else TripContext(destination=label.city)


async def run_routing(
    labels: Sequence[RouterLabel],
    *,
    split_name: str = "all",
    settings: Settings | None = None,
) -> RoutingReport:
    settings = settings or eval_settings()
    observations: list[RoutingObservation] = []
    async with build_rig(settings) as rig:
        orch = rig.container.orchestrator
        obs = rig.container.observability
        for label in labels:
            context = routing_context(label)
            state = await orch.create_session(context)
            if label.has_plan:
                state.plan = stub_plan(complete_context(label.city))
                await orch.sessions.save(state)
            trace = obs.start_trace(name="eval:routing", session_id=state.session_id)
            gate = await orch.router.route(state, label.utterance, trace=trace)
            obs.end_trace(trace, output=gate.route.value)
            observations.append(
                RoutingObservation(
                    id=label.id,
                    gold=label.label,
                    predicted=gate.route,
                    reason=gate.reason,
                    confidence=gate.decision.confidence if gate.decision else None,
                )
            )
    return RoutingReport(
        split=split_name,
        scores=score_routing(observations, threshold=settings.router_confidence_threshold),
        items=observations,
    )
