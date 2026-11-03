"""How the runner treats an agent the pre-planning budget cut off.

An agent that stops when cancelled is cut off and keeps what it found; one that shrugs the
cancellation off and finishes inside the grace period has simply finished; one that cannot say
what it found, or whose snapshot is broken, is unavailable for that section and the plan goes on.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.application import planning
from app.application.planning import PlanPath
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
from app.infrastructure.observability import NoopTracer

KYOTO = BASE["Kyoto"]
MEAL = {"id": "m1", "name": "Nishiki Ramen", "geo": {"lat": 35.005, "lng": 135.764}}


def _context():
    return case_context(EvalCase(id="runner", dataset="design", input="", destination="Kyoto", days=3), "s")


def _complete() -> AgentResult:
    return AgentResult(
        agent=AgentName.ATTRACTION,
        status=AgentStatus.OK,
        payload={"places": [], "meals": [MEAL]},
        notes=["1 attractions, 1 dining candidates"],
    )


def _partial() -> AgentResult:
    return AgentResult(agent=AgentName.ATTRACTION, status=AgentStatus.PARTIAL, payload={"places": []}, partial=True)


def _run_attraction(settings: Settings, agent: Any, *, budget: float = 0.01) -> AgentResult:
    settings = settings.model_copy(update={"preplan_budget_seconds": budget, "agent_timeout_seconds": 30})
    planning_agents = {AgentName.ATTRACTION: lambda registry, settings: agent}

    async def scenario() -> AgentResult:
        context = _context()
        results = await PlanPath(planner=None, registry=cost_sandbox(settings, "Kyoto"), settings=settings).run_agents(
            context=context,
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=KYOTO[0], lng=KYOTO[1])),
            date_range=context.date_range(),
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_runner"),
            emit=lambda *_: None,
            names={AgentName.ATTRACTION},
        )
        return results["attraction"]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(planning, "AGENT_FACTORIES", planning_agents)
        return asyncio.run(scenario())


class _ShrugsOffTheCancel:
    """Waits for a reply that never comes; when cancelled it carries on and finishes at once."""

    def __init__(self, *, then: Any) -> None:
        self.then = then

    async def run(self, request: Any) -> AgentResult:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        if isinstance(self.then, BaseException):
            raise self.then
        return self.then

    def partial_result(self, progress: list[dict[str, Any]], *, centre: Any, elapsed_ms: int, context: Any = None) -> AgentResult:
        return _partial()


def test_an_agent_that_finishes_inside_the_grace_period_keeps_its_complete_result(settings: Settings) -> None:
    result = _run_attraction(settings, _ShrugsOffTheCancel(then=_complete()))

    assert result.status is AgentStatus.OK and not result.partial
    assert result.payload["meals"] == [MEAL]
    assert result.failures == []


def test_an_agent_that_fails_inside_the_grace_period_is_reported_as_failed_not_as_cut_off(settings: Settings) -> None:
    result = _run_attraction(settings, _ShrugsOffTheCancel(then=RuntimeError("boom <html>secret key abc</html>")))

    assert result.status is AgentStatus.UNAVAILABLE
    assert [(gap.section, gap.kind) for gap in result.failures] == [("attractions", FailureKind.SERVER_ERROR)]
    assert "secret" not in result.failures[0].message


def test_an_agent_that_stops_when_cancelled_is_still_cut_off(settings: Settings) -> None:
    class Stops:
        async def run(self, request: Any) -> AgentResult:
            await asyncio.Event().wait()
            return _complete()

        def partial_result(self, progress: list[dict[str, Any]], *, centre: Any, elapsed_ms: int, context: Any = None) -> AgentResult:
            return _partial()

    result = _run_attraction(settings, Stops())

    assert result.status is AgentStatus.PARTIAL and result.partial


# -- a snapshot that cannot be built does not fail the turn -------------------------------------


class _BrokenSnapshot(_ShrugsOffTheCancel):
    """An agent that cannot say what it found: building its partial result raises."""

    def partial_result(self, progress: list[dict[str, Any]], *, centre: Any, elapsed_ms: int, context: Any = None) -> AgentResult:
        raise ValueError("a row the agent wrote is malformed: <html>secret key abc</html>")


class _NeverFinishes:
    """Waits for a reply that never comes and stops when cancelled."""

    def __init__(self, partial: Any) -> None:
        self.partial = partial

    async def run(self, request: Any) -> AgentResult:
        await asyncio.Event().wait()
        return _complete()

    def partial_result(self, progress: list[dict[str, Any]], *, centre: Any, elapsed_ms: int, context: Any = None) -> AgentResult:
        raise self.partial


def test_a_snapshot_that_cannot_be_built_leaves_the_section_unavailable_and_the_turn_going(settings: Settings) -> None:
    result = _run_attraction(settings, _NeverFinishes(ValueError("a row the agent wrote is malformed: <html>secret key abc</html>")))

    assert result.status is AgentStatus.UNAVAILABLE and not result.partial
    assert [(gap.section, gap.kind, gap.message) for gap in result.failures] == [
        ("attractions", FailureKind.TIMEOUT, "agent exceeded the pre-planning time budget")
    ]
    assert "secret" not in result.model_dump_json()


def test_a_snapshot_that_cannot_be_built_is_in_the_trace_not_in_the_plan(settings: Settings) -> None:
    from app.infrastructure.observability import TraceRecorder

    tracer = TraceRecorder("req_snapshot", settings)
    settings = settings.model_copy(update={"preplan_budget_seconds": 0.01, "agent_timeout_seconds": 30})

    async def scenario() -> None:
        context = _context()
        agent = _NeverFinishes(ValueError("malformed row: <html>secret key abc</html>"))
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(planning, "AGENT_FACTORIES", {AgentName.ATTRACTION: lambda registry, settings: agent})
            await PlanPath(planner=None, registry=cost_sandbox(settings, "Kyoto"), settings=settings).run_agents(
                context=context,
                destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=KYOTO[0], lng=KYOTO[1])),
                date_range=context.date_range(),
                toolbox=Toolbox(path="plan", tracer=tracer, store=None, request_id="req_snapshot"),
                emit=lambda *_: None,
                names={AgentName.ATTRACTION},
            )

    asyncio.run(scenario())

    spans = [span for span in tracer.spans if span.name == "agent.attraction"]
    assert [(span.attrs.get("status"), span.attrs.get("error_class")) for span in spans] == [("error", "ValueError")]
    assert "secret key abc" in str(spans[0].attrs.get("error"))


def test_a_malformed_row_in_a_real_agents_progress_does_not_escape_the_runner(settings: Settings) -> None:
    from app.application.agents import AttractionAgent
    from app.domain.models import PlaceCandidate, SourceRef

    class _Guide:
        name = "guide"
        available = True

        async def search(self, **kwargs: Any) -> list[PlaceCandidate]:
            ref = SourceRef(title="guide", url="https://example.com/guide", source="guide")
            return [PlaceCandidate(name="Kinkaku-ji", geo=GeoPoint(lat=35.04, lng=135.73), source_refs=[ref])] if kwargs["category"] == "attraction" else []

    class _NotesNeverAnswer:
        name = "notes"
        available = True

        async def search(self, query: str, *, limit: int = 5, window_months: int = 24) -> list[Any]:
            await asyncio.Event().wait()
            return []

    class _Malformed(AttractionAgent):
        @classmethod
        def _keep(cls, request: Any, places: list[PlaceCandidate]) -> None:
            if request.progress is not None:
                request.progress.append({"name": None})

    registry = SimpleNamespace(places=[_Guide()], notes=[_NotesNeverAnswer()], geocoder=None, web=[], web_deep=[])
    settings = settings.model_copy(update={"preplan_budget_seconds": 0.6, "agent_timeout_seconds": 30})

    async def scenario() -> AgentResult:
        context = _context()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(planning, "AGENT_FACTORIES", {AgentName.ATTRACTION: _Malformed})
            results = await PlanPath(planner=None, registry=registry, settings=settings).run_agents(
                context=context,
                destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=KYOTO[0], lng=KYOTO[1])),
                date_range=context.date_range(),
                toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_malformed"),
                emit=lambda *_: None,
                names={AgentName.ATTRACTION},
            )
        return results["attraction"]

    result = asyncio.run(scenario())

    assert result.status is AgentStatus.UNAVAILABLE
    assert [gap.kind for gap in result.failures] == [FailureKind.TIMEOUT]


# -- the progress an agent leaves is bounded ----------------------------------------------------


def test_the_progress_an_agent_records_is_bounded() -> None:
    from app.application.agents import MAX_PROGRESS_ROWS, AgentContext, AttractionAgent

    progress: list[dict[str, Any]] = []
    request = AgentContext(
        context=_context(),
        destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=KYOTO[0], lng=KYOTO[1])),
        date_range=_context().date_range(),
        request_id="r",
        toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="r"),
        settings=Settings(_env_file=None, environment="test"),
        progress=progress,
    )

    for _ in range(10):
        AttractionAgent._record(request, [{"name": "x"}] * (MAX_PROGRESS_ROWS // 2))

    assert len(progress) == MAX_PROGRESS_ROWS
