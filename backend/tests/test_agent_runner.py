"""How the runner treats an agent the pre-planning budget cut off.

An agent that stops when cancelled is cut off and keeps what it found; one that shrugs the
cancellation off and finishes inside the grace period has simply finished; one that cannot say
what it found, or whose snapshot is broken, is unavailable for that section and the plan goes on.
"""

from __future__ import annotations

import asyncio
import logging
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
