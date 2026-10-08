"""Metric 4 - system efficiency.

Appendix C: "trace; usage fields, no model score. Median latency, API-call count, and tokens
against the ablation that always runs all four agents."

The same session scripts run twice: once with the default orchestrator and once with
`OrchestratorOptions(always_full_pipeline=True)` (the ablation). Each turn's numbers come from
the trace's usage fields (`TurnResult.metrics`): API calls = model calls + tool calls, tokens =
input + output. Plan turns run all four agents in both variants, so the gap shows on the modify
and quick-question turns; medians are reported overall and per route.

Latency note: offline mocks answer in microseconds and retry backoff is not slept, so latency
here measures orchestration overhead only; token and call counts are the meaningful signal until
real models are connected.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from backend.agents.orchestrator import OrchestratorOptions
from backend.eval.harness import build_rig, eval_settings
from backend.eval.models import CustomScenario, HKScenario, JapanScenario, scenario_message
from backend.eval.report import MetricReport, SummaryRow, fmt_num, fmt_rate
from backend.eval.stats import median
from backend.schemas.common import AgentName, Route
from backend.schemas.turn import TurnResult
from backend.settings import Settings


class TurnSample(BaseModel):
    scenario_id: str
    turn: int
    route: Route
    llm_calls: int
    tool_calls: int
    api_calls: int
    input_tokens: int
    output_tokens: int
    tokens: int
    latency_ms: float
    agents_run: list[AgentName] = Field(default_factory=list)
    plan_produced: bool = False

    @classmethod
    def from_result(cls, scenario_id: str, turn: int, result: TurnResult) -> TurnSample:
        m = result.metrics
        return cls(
            scenario_id=scenario_id,
            turn=turn,
            route=result.route,
            llm_calls=m.llm_calls,
            tool_calls=m.tool_calls,
            api_calls=m.llm_calls + m.tool_calls,
            input_tokens=m.input_tokens,
            output_tokens=m.output_tokens,
            tokens=m.input_tokens + m.output_tokens,
            latency_ms=m.latency_ms,
            agents_run=list(result.agents_run or m.agents_run),
            plan_produced=result.plan is not None,
        )


class VariantSummary(BaseModel):
    turns: int
    median_tokens: float | None
    median_api_calls: float | None
    median_llm_calls: float | None
    median_tool_calls: float | None
    median_latency_ms: float | None
    median_agents_run: float | None


def summarize_variant(samples: Sequence[TurnSample]) -> VariantSummary:
    return VariantSummary(
        turns=len(samples),
        median_tokens=median([s.tokens for s in samples]),
        median_api_calls=median([s.api_calls for s in samples]),
        median_llm_calls=median([s.llm_calls for s in samples]),
        median_tool_calls=median([s.tool_calls for s in samples]),
        median_latency_ms=median([s.latency_ms for s in samples]),
        median_agents_run=median([len(s.agents_run) for s in samples]),
    )


def _reduction(default: float | None, ablation: float | None) -> float | None:
    """1 - default/ablation: the share the routed system saves (negative = it costs more)."""
    if default is None or ablation is None or ablation == 0:
        return None
    return 1.0 - default / ablation


class EfficiencyScores(BaseModel):
    default: VariantSummary
    ablation: VariantSummary
    by_route: dict[str, dict[str, VariantSummary]] = Field(
        description="route (of the default run) -> {'default', 'ablation'}"
    )
    reduction: dict[str, float | None] = Field(description="1 - default/ablation of the medians")
    default_lower: dict[str, bool | None]


def summarize_efficiency(default: Sequence[TurnSample], ablation: Sequence[TurnSample]) -> EfficiencyScores:
    """Medians per variant, overall and per route. Turns are paired by (scenario, turn) and
    grouped by the route the DEFAULT run took, so both variants are compared on the same turns."""
    d_all, a_all = summarize_variant(default), summarize_variant(ablation)
    pair = {(s.scenario_id, s.turn): s for s in ablation}
    by_route: dict[str, dict[str, VariantSummary]] = {}
    for route in Route:
        d = [s for s in default if s.route is route]
        a = [pair[(s.scenario_id, s.turn)] for s in d if (s.scenario_id, s.turn) in pair]
        if d:
            by_route[route.value] = {"default": summarize_variant(d), "ablation": summarize_variant(a)}
    fields = {
        "tokens": (d_all.median_tokens, a_all.median_tokens),
        "api_calls": (d_all.median_api_calls, a_all.median_api_calls),
        "latency_ms": (d_all.median_latency_ms, a_all.median_latency_ms),
    }
    return EfficiencyScores(
        default=d_all,
        ablation=a_all,
        by_route=by_route,
        reduction={k: _reduction(d, a) for k, (d, a) in fields.items()},
        default_lower={k: (None if d is None or a is None else d < a) for k, (d, a) in fields.items()},
    )


class EfficiencyReport(MetricReport):
    metric: str = "efficiency"
    scores: EfficiencyScores
    default_turns: list[TurnSample] = Field(default_factory=list)
    ablation_turns: list[TurnSample] = Field(default_factory=list)

    def summary_row(self) -> SummaryRow:
        s = self.scores
        d, a = s.default, s.ablation
        # Tokens and API calls decide; mock latency is orchestration noise (see module doc).
        tokens, calls = s.default_lower.get("tokens"), s.default_lower.get("api_calls")
        met = None if tokens is None or calls is None else tokens and calls
        return SummaryRow(
            metric=self.metric,
            split=self.split,
            n=d.turns,
            headline=(
                f"median tokens {fmt_num(d.median_tokens)} vs {fmt_num(a.median_tokens)} "
                f"(saves {fmt_rate(s.reduction['tokens'])}); api calls {fmt_num(d.median_api_calls)} vs "
                f"{fmt_num(a.median_api_calls)}; latency {fmt_num(d.median_latency_ms, 1)} vs "
                f"{fmt_num(a.median_latency_ms, 1)} ms"
            ),
            target="default < all-four-agents ablation (tokens, api calls)",
            met=met,
        )


def session_script(scenario: CustomScenario) -> list[str]:
    """Plan, then the scenario's edit / follow-up, then one quick question."""
    start = scenario.context.start_date
    when = f" on {start.day} {start:%B}" if start else ""
    question = f"What is the weather in {scenario.city}{when}?"
    turns = [scenario_message(scenario)]
    if isinstance(scenario, JapanScenario) and scenario.edit is not None:
        turns.append(scenario.edit.message)
    if isinstance(scenario, HKScenario):
        turns.append(scenario.followup)
    turns.append(question)
    return turns


async def _run_variant(
    scenarios: Sequence[CustomScenario], settings: Settings, options: OrchestratorOptions
) -> list[TurnSample]:
    samples: list[TurnSample] = []
    for scenario in scenarios:
        async with build_rig(settings, options=options) as rig:
            session = await rig.new_session(scenario.context)
            for i, message in enumerate(session_script(scenario)):
                result = await rig.turn(session, message)
                samples.append(TurnSample.from_result(scenario.id, i, result))
    return samples


async def run_efficiency(
    scenarios: Sequence[CustomScenario],
    *,
    split_name: str = "all",
    settings: Settings | None = None,
) -> EfficiencyReport:
    settings = settings or eval_settings()
    default = await _run_variant(scenarios, settings, OrchestratorOptions())
    ablation = await _run_variant(scenarios, settings, OrchestratorOptions(always_full_pipeline=True))
    return EfficiencyReport(
        split=split_name,
        scores=summarize_efficiency(default, ablation),
        default_turns=default,
        ablation_turns=ablation,
        notes=["offline mocks: latency measures orchestration overhead only"],
    )
