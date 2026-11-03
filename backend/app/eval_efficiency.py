"""Cost evaluations behind the proposal's efficiency metrics.

``efficiency`` (Proposal TC-10, p4 and Appendix C1): every utterance runs twice
on the same accepted context. Once through the System 1 router (the real
``TurnService``: understand, route, then only the selected path) and once
through a forced full planning pipeline (understand, then geocode, the four
agents and the planner, whatever was asked). The pair is compared on latency,
tool calls, writer calls and tokens.

The run is offline. Providers are the deterministic sandbox; the writer is
``MeteredLLM``, whose router reply is scripted from the case's expected intent
so the run measures the cost of each path, not routing accuracy (that is the
``routing`` dataset). Tool calls are counted at the provider and compared with
the SQLite tool log (Proposal TC-12). The rig here is shared with the
``edit_vs_replan`` dataset in ``app.eval_edits``.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.application.asking import AskPath
from app.application.clarification import Clarifier
from app.application.llm_gateway import LLMGateway
from app.application.memory import MemoryService
from app.application.modification import ModifyPath
from app.application.orchestrator import TurnService
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.router import System1Router, affected_parts_for
from app.application.toolbox import Toolbox
from app.application.understanding import understand
from app.config import Settings
from app.domain.errors import PathfinderError
from app.domain.models import EvalCase, EvalCaseResult, EvalMetric, Intent, TripContext, TripPlan, new_id
from app.eval_metering import MeteredLLM, RunLedger
from app.evaluation import SandboxRegistry
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer, TracerProxy, start_trace
from app.infrastructure.providers.websearch import WebResult

SNAPSHOT = {
    "writer": "sandbox-metered (scripted replies; tokens estimated at 4 characters per token)",
    "router": "model (scripted offline from the expected intent)",
    "providers": "sandbox",
}


@dataclass
class PathCost:
    tool_calls: int = 0
    logged: int = 0
    llm_calls: int = 0
    tokens: int = 0
    latency_ms: float = 0.0
    tools: Counter = field(default_factory=Counter)

    @property
    def api_calls(self) -> int:
        return self.tool_calls + self.llm_calls

    def describe(self) -> str:
        return (
            f"{self.tool_calls} tool + {self.llm_calls} LLM calls, "
            f"{self.tokens} tokens, {round(self.latency_ms)} ms"
        )


@dataclass
class SuiteReport:
    results: list[EvalCaseResult]
    metrics: list[EvalMetric]
    snapshot: dict[str, Any]


async def run_cost_suite(dataset: str, cases: list[EvalCase], settings: Settings, ledger: RunLedger) -> SuiteReport:
    settings = settings.model_copy(
        update={
            "preplan_budget_seconds": 20,
            "agent_timeout_seconds": 10,
            "enable_flights": True,
            "langfuse_enabled": False,
            "decision_enabled": False,
            "grok_search_enabled": False,
            "trace_dir": ledger.directory / "traces",
        }
    )
    if dataset == "efficiency":
        rows = [await _efficiency_case(case, settings, ledger) for case in cases]
        return SuiteReport(
            results=[row[0] for row in rows],
            metrics=_efficiency_metrics([row[1] for row in rows], [row[2] for row in rows]),
            snapshot=SNAPSHOT,
        )
    if dataset == "edit_vs_replan":
        from app.eval_edits import run_edit_suite

        return await run_edit_suite(cases, settings, ledger)
    raise KeyError(dataset)


# ---------------------------------------------------------------------------
# Rig: one service graph on metered, instrumented sandbox providers
# ---------------------------------------------------------------------------


class SandboxWeb:
    name = "sandbox-web"
    available = True

    def __init__(self, destination: str):
        self.destination = destination

    async def search(
        self,
        *,
        query: str,
        count: int = 5,
        ori_question: str | None = None,
        retries: int = 1,
    ) -> list[WebResult]:
        slug = query.lower().replace(" ", "-")[:60]
        return [
            WebResult(
                title=f"{self.destination} travel notes",
                url=f"https://example.com/notes/{slug}",
                summary=f"Sandbox page about {query} in {self.destination}.",
                snippet=f"Visitors to {self.destination} recommend going early.",
                site_name="example.com",
            )
        ][:count]


def cost_sandbox(settings: Settings, destination: str) -> SandboxRegistry:
    """The evaluation sandbox (offline FX included) plus a sandbox web search."""
    registry = SandboxRegistry(settings, destination)
    registry.web = [SandboxWeb(destination)]
    registry.web_deep = []
    return registry


@dataclass
class Rig:
    settings: Settings
    ledger: RunLedger
    llm: MeteredLLM
    gateway: LLMGateway
    tracer_proxy: TracerProxy
    plan_path: PlanPath
    modify_path: ModifyPath
    turns: TurnService


def build_rig(settings: Settings, destination: str, ledger: RunLedger) -> Rig:
    registry = ledger.instrument(cost_sandbox(settings, destination))
    llm = MeteredLLM()
    proxy = TracerProxy()
    gateway = LLMGateway(llm, PromptLibrary(settings), proxy, settings)
    planner = Planner(gateway, settings, registry.fx)
    plan_path = PlanPath(planner=planner, registry=registry, settings=settings)
    modify_path = ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway)
    turns = TurnService(
        store=ledger.store,
        memory=MemoryService(ledger.store, settings),
        router=System1Router(gateway=gateway, settings=settings, decision_client=None, use_model=True),
        clarifier=Clarifier(gateway),
        plan_path=plan_path,
        modify_path=modify_path,
        ask_path=AskPath(gateway=gateway, registry=registry, settings=settings),
        gateway=gateway,
        settings=settings,
        tracer_proxy=proxy,
    )
    return Rig(settings, ledger, llm, gateway, proxy, plan_path, modify_path, turns)


def ignore_event(*_: Any) -> None:
    return None


def case_context(case: EvalCase, session_id: str) -> TripContext:
    start = date.today() + timedelta(days=30)
    days = case.days or 3
    return TripContext(
        session_id=session_id,
        destination=case.destination or "Kyoto",
        origin=case.origin or "Hong Kong",
        start_date=start,
        end_date=start + timedelta(days=days - 1),
        days=days,
        party_size=case.party_size or 2,
        budget=case.budget if case.budget is not None else 3000,
        currency=case.currency,
        hard_constraints=list(case.constraints),
    )


async def accepted_plan(settings: Settings, context: TripContext) -> TripPlan:
    """The plan the traveller already accepted: built offline, outside the meter."""
    registry = cost_sandbox(settings, context.destination)
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    plan_path = PlanPath(planner=Planner(gateway, settings, registry.fx), registry=registry, settings=settings)
    return await plan_path.run(
        context=context.model_copy(deep=True),
        session_id=context.session_id,
        toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="accepted"),
        tracer=NoopTracer(),
        emit=ignore_event,
        version=1,
    )


def measure(ledger: RunLedger, before: Counter, request_id: str, generations: list[dict], started: float) -> PathCost:
    made = ledger.made - before
    return PathCost(
        tool_calls=sum(made.values()),
        logged=ledger.logged(request_id),
        llm_calls=len(generations),
        tokens=sum(int(row["prompt_tokens"]) + int(row["completion_tokens"]) for row in generations),
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
        tools=ledger.logged_tools(request_id),
    )


async def router_turn(rig: Rig, session_id: str, message: str) -> tuple[PathCost, str, str]:
    """One real turn through the System 1 router. Returns (cost, path, error code)."""
    before = rig.ledger.made.copy()
    started = time.perf_counter()
    path, error, trace = "", "", {}
    async for event in rig.turns.handle_turn(session_id=session_id, message=message):
        if event.event == "route" and not path:
            path = str(event.data.get("intent", ""))
        elif event.event == "clarify":
            path = "clarify"
        elif event.event == "error":
            error = str(event.data.get("code", "error"))
        elif event.event == "trace":
            trace = event.data
    cost = measure(rig.ledger, before, trace.get("request_id", ""), trace.get("generations", []), started)
    return cost, path, error


async def full_pipeline_turn(rig: Rig, session_id: str, message: str) -> tuple[PathCost, TripPlan | None]:
    """The no-router baseline: every utterance runs understand + the full plan path."""
    store = rig.ledger.store
    before = rig.ledger.made.copy()
    request_id = new_id("req")
    tracer = start_trace(rig.settings, request_id)
    rig.tracer_proxy.bind(tracer)
    started = time.perf_counter()
    plan = None
    try:
        context = await understood_context(rig, tracer, message, store.get_context(session_id))
        store.save_context(context)
        previous = store.get_plan(session_id)
        plan = await rig.plan_path.run(
            context=context,
            session_id=session_id,
            toolbox=Toolbox(path="plan", tracer=tracer, store=store, request_id=request_id),
            tracer=tracer,
            emit=ignore_event,
            version=previous.version + 1 if previous else 1,
            trip_id=previous.trip_id if previous else None,
            change_summary="Full pipeline run",
        )
        store.save_plan(plan, plan.change_summary)
    except PathfinderError:
        plan = None
    finally:
        tracer.finish("ok")
    return measure(rig.ledger, before, request_id, tracer.generations, started), plan


async def understood_context(rig: Rig, tracer: Any, message: str, context: TripContext) -> TripContext:
    with tracer.span("understand"):
        try:
            return await understand(message, context, rig.gateway)
        except Exception:
            return context


# ---------------------------------------------------------------------------
# TC-10: router path vs forced full pipeline
# ---------------------------------------------------------------------------


async def _efficiency_case(case: EvalCase, settings: Settings, ledger: RunLedger) -> tuple[EvalCaseResult, PathCost, PathCost]:
    expected = case.expected_intent or Intent.PLAN
    follow_up = expected != Intent.PLAN
    costs: list[PathCost] = []
    path, error = "", ""
    for routed in (True, False):
        rig = build_rig(settings, case.destination or "Kyoto", ledger)
        session = ledger.store.create_session("")
        context = case_context(case, session.id)
        ledger.store.save_context(context)
        if follow_up:
            plan = await accepted_plan(settings, context)
            ledger.store.save_plan(plan, "Accepted plan")
        if routed:
            rig.llm.route = {
                "intent": expected.value,
                "confidence": 0.92,
                "need_clarification": expected == Intent.UNCLEAR,
                "affected_parts": affected_parts_for(case.input) if expected == Intent.MODIFY else [],
                "related_to_plan": follow_up,
                "reason_code": "scripted_route",
            }
            cost, path, error = await router_turn(rig, session.id, case.input)
        else:
            cost, _ = await full_pipeline_turn(rig, session.id, case.input)
        costs.append(cost)
    router, full = costs

    expected_path = "clarify" if expected == Intent.UNCLEAR else expected.value
    failure = ""
    if error:
        failure = f"router_path_error:{error}"
    elif path != expected_path:
        failure = "wrong_path"
    elif expected == Intent.PLAN and router.tool_calls != full.tool_calls:
        failure = "extra_tool_calls"
    elif expected != Intent.PLAN and router.api_calls >= full.api_calls:
        failure = "no_saving"
    passed = not failure
    tools = [f"router · {tool} ×{n}" for tool, n in sorted(router.tools.items())]
    tools += [f"full · {tool} ×{n}" for tool, n in sorted(full.tools.items())]
    result = EvalCaseResult(
        case_id=case.id,
        dataset=case.dataset,
        split=case.split,
        passed=passed,
        score=1.0 if passed else 0.0,
        failure_class=failure,
        input=case.input,
        output_summary=f"Router → {path or '?'}: {router.describe()}. Full pipeline: {full.describe()}.",
        judge_reason=(
            "Proposal TC-10: a planning request may cost one extra router call; any other "
            "intent must use fewer API calls than always running the full pipeline."
        ),
        tool_summary=tools[:16],
        scores={
            "router_tool_calls": router.tool_calls,
            "router_llm_calls": router.llm_calls,
            "router_tokens": router.tokens,
            "router_latency_ms": router.latency_ms,
            "full_tool_calls": full.tool_calls,
            "full_llm_calls": full.llm_calls,
            "full_tokens": full.tokens,
            "full_latency_ms": full.latency_ms,
            "tool_calls_made": router.tool_calls + full.tool_calls,
            "tool_calls_logged": router.logged + full.logged,
        },
    )
    ledger.tokens += router.tokens + full.tokens
    return result, router, full


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 1) if values else 0.0


def saving(ours: float, baseline: float) -> float:
    return round(100.0 * (1 - ours / baseline), 1) if baseline else 0.0


def path_metrics(prefix: str, label: str, costs: list[PathCost]) -> list[EvalMetric]:
    return [
        EvalMetric(key=f"{prefix}_tool_calls", label=f"{label} · tool calls / turn", value=mean([c.tool_calls for c in costs]), unit="calls"),
        EvalMetric(key=f"{prefix}_llm_calls", label=f"{label} · LLM calls / turn", value=mean([c.llm_calls for c in costs]), unit="calls"),
        EvalMetric(key=f"{prefix}_tokens", label=f"{label} · tokens / turn", value=mean([c.tokens for c in costs]), unit="tokens"),
        EvalMetric(key=f"{prefix}_latency", label=f"{label} · mean latency", value=mean([c.latency_ms for c in costs]), unit="ms"),
    ]


def _efficiency_metrics(router: list[PathCost], full: list[PathCost]) -> list[EvalMetric]:
    return [
        EvalMetric(
            key="router_api_call_saving",
            label="API calls saved by the router",
            value=saving(sum(c.api_calls for c in router), sum(c.api_calls for c in full)),
        ),
        EvalMetric(
            key="router_token_saving",
            label="Tokens saved by the router",
            value=saving(sum(c.tokens for c in router), sum(c.tokens for c in full)),
        ),
        *path_metrics("router", "Router path", router),
        *path_metrics("full", "Full pipeline", full),
    ]
