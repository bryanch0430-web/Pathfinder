"""Proposal design behaviour DB-03 (Section 4, p2).

DB-03: the four pre-planning agents run together inside a time budget; an agent
that overruns is cancelled, its section is marked unavailable and the plan is
still returned.

Offline and deterministic: sandbox providers, fake agents that wait on each
other instead of the clock, and a budget of a few milliseconds.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.application import planning
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import BUDGET_EXCEEDED, PlanPath
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    EvalCase,
    FailureKind,
    GeoLocation,
    GeoPoint,
)
from app.eval_efficiency import case_context, cost_sandbox
from app.evaluation import BASE
from app.infrastructure.observability import NoopTracer, TraceRecorder
from app.infrastructure.store import SQLiteStore

REQUEST_ID = "req_db03"
KYOTO = BASE["Kyoto"]


class Gate:
    """Opens once every agent has started. Agents run one after another would
    block on it until the budget ran out, so passing it proves they run together."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.started: list[str] = []
        self.opened = asyncio.Event()

    async def arrive(self, name: str) -> None:
        self.started.append(name)
        if len(self.started) == self.size:
            self.opened.set()
        await self.opened.wait()


class NeverAnswers:
    """A hotel source that starts, reaches the gate and never returns."""

    name = "slow-hotels"
    available = True

    def __init__(self, gate: Gate) -> None:
        self.gate = gate
        self.cancelled = False

    async def search(self, **_: Any) -> list:
        try:
            await self.gate.arrive("hotel")
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return []


def _source(name: str) -> list[dict[str, Any]]:
    return [{"title": name, "url": f"https://example.com/{name.lower().replace(' ', '-')}", "source": "test"}]


def _ready_result(agent: AgentName) -> AgentResult:
    if agent is AgentName.ATTRACTION:
        places = [
            {
                "id": f"p{index + 1}",
                "name": f"Stop {index + 1}",
                "category": "Attraction",
                "description": "A verified stop.",
                "geo": {"lat": KYOTO[0] + index * 0.01, "lng": KYOTO[1] + index * 0.01},
                "rating": 4.5,
                "source_refs": _source(f"Stop {index + 1}"),
            }
            for index in range(6)
        ]
        return AgentResult(agent=agent, status=AgentStatus.OK, payload={"places": places, "meals": []})
    if agent is AgentName.WEATHER:
        return AgentResult(agent=agent, status=AgentStatus.OK, payload={"days": []})
    return AgentResult(agent=agent, status=AgentStatus.OK, payload={"flights": []})


def _prompt_agents(monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    """Attraction, weather and ticket answer as soon as all four have started; the
    hotel agent is the real one, held up by its source."""

    def factory(agent: AgentName):
        async def run(request: Any) -> AgentResult:
            await gate.arrive(agent.value)
            return _ready_result(agent)

        return lambda registry, settings: SimpleNamespace(run=run)

    factories = {agent: factory(agent) for agent in (AgentName.ATTRACTION, AgentName.WEATHER, AgentName.TICKET)}
    factories[AgentName.HOTEL] = planning.HotelAgent
    monkeypatch.setattr(planning, "AGENT_FACTORIES", factories)


def _budget(settings: Settings) -> Settings:
    # The per-call deadline is far away, so only the pre-planning budget can stop the hotel agent.
    return settings.model_copy(update={"preplan_budget_seconds": 0.01, "agent_timeout_seconds": 30})


def _context():
    return case_context(EvalCase(id="db03", dataset="design", input="", destination="Kyoto", days=3), "s")


def _others() -> list[asyncio.Task]:
    return [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


def test_db03_agents_start_together_and_an_overrun_section_is_marked_unavailable(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _budget(settings)
    store = SQLiteStore(settings.database_path)
    tracer = TraceRecorder(REQUEST_ID, settings)

    async def scenario() -> None:
        gate = Gate(4)
        registry = cost_sandbox(settings, "Kyoto")
        slow = NeverAnswers(gate)
        registry.hotels = [slow]
        _prompt_agents(monkeypatch, gate)
        context = _context()
        events: list[tuple[str, dict]] = []
        results = await PlanPath(planner=None, registry=registry, settings=settings).run_agents(
            context=context,
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=KYOTO[0], lng=KYOTO[1])),
            date_range=context.date_range(),
            toolbox=Toolbox(path="plan", tracer=tracer, store=store, request_id=REQUEST_ID),
            emit=lambda name, data: events.append((name, data)),
        )
        # All four started before any could finish.
        assert sorted(gate.started) == ["attraction", "hotel", "ticket", "weather"]
        for name in ("attraction", "weather", "ticket"):
            assert results[name].status is AgentStatus.OK
        hotel = results["hotel"]
        assert hotel.status is AgentStatus.UNAVAILABLE
        assert hotel.payload == {}
        assert [(gap.section, gap.kind) for gap in hotel.failures] == [("hotels", FailureKind.TIMEOUT)]
        assert hotel.failures[0].retryable
        done = {data["agent"]: data for name, data in events if name == "agent" and data["phase"] == "done"}
        assert done["hotel"]["status"] == "unavailable"
        # The slow call was cancelled and nothing is left running.
        assert slow.cancelled
        assert _others() == []

    asyncio.run(scenario())

    rows = [row for row in store.tool_calls(REQUEST_ID) if row["tool"] == "hotels.search"]
    assert [(row["status"], row["error_class"]) for row in rows] == [("timeout", "timeout")]
    spans = [span for span in tracer.spans if span.name == "tool.hotels.search"]
    assert [(span.attrs["status"], span.attrs["reason"]) for span in spans] == [("timeout", BUDGET_EXCEEDED)]


def test_db03_the_planner_still_returns_a_plan_when_an_agent_overruns(
    settings: Settings, offline_gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _budget(settings)
    store = SQLiteStore(settings.database_path)

    async def scenario():
        gate = Gate(4)
        registry = cost_sandbox(settings, "Kyoto")
        registry.hotels = [NeverAnswers(gate)]
        _prompt_agents(monkeypatch, gate)
        path = PlanPath(planner=Planner(offline_gateway, settings, registry.fx), registry=registry, settings=settings)
        plan = await path.run(
            context=_context(),
            session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=store, request_id=REQUEST_ID),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
        assert _others() == []
        return plan

    plan = asyncio.run(scenario())
    assert len(plan.days) == 3
    attractions = [item for _, item in plan.iter_items() if item.type.value == "attraction"]
    assert attractions and all(item.geo is not None and item.source_refs for item in attractions)
    assert plan.hotels == []
    hotel_gaps = [gap for gap in plan.data_quality.gaps if gap.section == "hotels"]
    # One gap for the section, saying why: not a second "returned no usable data" one.
    assert [(gap.kind, gap.message) for gap in hotel_gaps] == [
        (FailureKind.TIMEOUT, "agent exceeded the pre-planning time budget")
    ]


def test_db03_an_agent_that_ignores_cancellation_cannot_hold_the_plan(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _budget(settings)
    monkeypatch.setattr(planning, "CANCEL_GRACE_SECONDS", 0.01)

    async def scenario() -> None:
        gate = Gate(4)
        release = asyncio.Event()

        async def stubborn(request: Any) -> AgentResult:
            await gate.arrive("weather")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()  # swallows the cancellation and keeps going
            return _ready_result(AgentName.WEATHER)

        def prompt(agent: AgentName):
            async def run(request: Any) -> AgentResult:
                await gate.arrive(agent.value)
                return _ready_result(agent)

            return lambda registry, settings: SimpleNamespace(run=run)

        monkeypatch.setattr(
            planning,
            "AGENT_FACTORIES",
            {
                AgentName.ATTRACTION: prompt(AgentName.ATTRACTION),
                AgentName.HOTEL: prompt(AgentName.HOTEL),
                AgentName.TICKET: prompt(AgentName.TICKET),
                AgentName.WEATHER: lambda registry, settings: SimpleNamespace(run=stubborn),
            },
        )
        context = _context()
        results = await PlanPath(planner=None, registry=cost_sandbox(settings, "Kyoto"), settings=settings).run_agents(
            context=context,
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=KYOTO[0], lng=KYOTO[1])),
            date_range=context.date_range(),
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id=REQUEST_ID),
            emit=lambda *_: None,
        )
        assert results["weather"].status is AgentStatus.UNAVAILABLE
        assert results["weather"].failures[0].section == "weather"
        assert all(results[name].status is AgentStatus.OK for name in ("attraction", "hotel", "ticket"))
        # The late agent is still running; let it finish so the loop closes cleanly.
        release.set()
        await asyncio.gather(*_others())

    asyncio.run(scenario())
