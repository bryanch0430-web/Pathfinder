"""The plan path runs the pre-planning audit only when enable_preplan_audit is on.

The sandbox setup of evaluation._planning_case (SandboxRegistry + PlanPath), with a
MeteredLLM writer so every LLM purpose the path uses is recorded.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from app.application import planning
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import AgentName, TripContext, TripPlan
from app.eval_checks import RecordingToolbox, ToolLog
from app.eval_metering import MeteredLLM
from app.evaluation import SandboxRegistry
from app.infrastructure.observability import NoopTracer

DESTINATION = "Kyoto"


def _plan(settings: Settings, *, audit: bool = False, replies: dict[str, str] | None = None, capture=None):
    settings = settings.model_copy(
        update={"preplan_budget_seconds": 20, "agent_timeout_seconds": 10, "enable_preplan_audit": audit}
    )
    start = date.today() + timedelta(days=30)
    registry = SandboxRegistry(settings, DESTINATION, start=start)
    llm = MeteredLLM(replies=replies)
    gateway = LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)
    plan_path = PlanPath(planner=Planner(gateway, settings, registry.fx), registry=registry, settings=settings)
    if capture is not None:
        original = plan_path.run_agents

        async def run_agents(**kwargs):
            results = await original(**kwargs)
            capture.update(results)
            return results

        plan_path.run_agents = run_agents  # type: ignore[method-assign]
    context = TripContext(
        session_id="audit", destination=DESTINATION, origin="Hong Kong", start_date=start,
        end_date=start + timedelta(days=2), days=3, party_size=1, budget=3000, preferences=[],
    )
    events: list[tuple[str, dict]] = []
    plan = asyncio.run(
        plan_path.run(
            context=context, session_id="audit", toolbox=RecordingToolbox(ToolLog(), path="plan", request_id="audit"),
            tracer=NoopTracer(), emit=lambda kind, data: events.append((kind, data)), version=1,
        )
    )
    phases = [data.get("phase") for kind, data in events if kind == "progress"]
    return plan, llm, phases


def test_flag_off_no_audit_call(settings: Settings) -> None:
    plan, llm, phases = _plan(settings)
    assert isinstance(plan, TripPlan)
    assert "preplan.audit" not in llm.purposes
    assert "audit" not in phases


def test_flag_on_one_audit_call(settings: Settings) -> None:
    plan, llm, phases = _plan(settings, audit=True)
    assert isinstance(plan, TripPlan)
    assert llm.purposes.count("preplan.audit") == 1
    ordered = [phase for phase in phases if phase in ("agents", "audit", "planner")]
    assert ordered == ["agents", "audit", "planner"]


def test_flag_on_only_adds_the_audit_call(settings: Settings) -> None:
    """An empty audit report changes nothing else: same other LLM purposes as flag off."""
    _, off, _ = _plan(settings)
    _, on, _ = _plan(settings, audit=True)
    assert [p for p in on.purposes if p != "preplan.audit"] == off.purposes


def test_audit_failure_still_plans(settings: Settings) -> None:
    plan, llm, phases = _plan(settings, audit=True, replies={"preplan.audit": "not json"})
    assert isinstance(plan, TripPlan)
    assert "preplan.audit" in llm.purposes
    assert phases[-1] == "ready"


def _places(results) -> dict[str, str]:
    """Attraction candidate id -> name, as the auditor and planner read them."""
    result = results.get(AgentName.ATTRACTION.value)
    rows = (result.payload or {}).get("places", []) if result else []
    return {str(row["id"]): str(row.get("name")) for row in rows if row.get("id")}


def _plan_names(plan: TripPlan) -> set[str]:
    return {item.name for day in plan.days for item in day.items}


def test_block_finding_reaches_the_planner(settings: Settings) -> None:
    results: dict = {}
    baseline, _, _ = _plan(settings, capture=results)
    places = _places(results)
    used = [pid for pid, name in places.items() if name in _plan_names(baseline)]
    assert used, "the sandbox plan should use at least one attraction candidate"
    assert len(places) >= 2, "a block is only applied while one candidate remains"
    target = used[0]
    reply = json.dumps({"findings": [{
        "agent": "attraction", "item_id": target, "issue": "suspect_fact", "severity": "block",
        "detail": "closed permanently", "follow_up": None,
    }]})
    plan, llm, _ = _plan(settings, audit=True, replies={"preplan.audit": reply})
    assert llm.purposes.count("preplan.audit") == 1
    assert places[target] not in _plan_names(plan)


def test_canary_leak_from_audit_propagates(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    async def leak(**_):
        raise CanaryLeak("leak")

    monkeypatch.setattr(planning, "audit_results", leak)
    with pytest.raises(CanaryLeak):
        _plan(settings, audit=True)


def test_audit_bug_fails_open(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence in depth: an unexpected error escaping the audit calls never stops the trip."""
    async def broken(*_, **__):
        raise RuntimeError("bug")

    monkeypatch.setattr(planning, "run_follow_ups", broken)
    plan, _, phases = _plan(settings, audit=True)
    assert isinstance(plan, TripPlan)
    assert phases[-1] == "ready"
