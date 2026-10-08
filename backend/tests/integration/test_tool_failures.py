"""Tool failure and staleness scenarios 10-13, end to end (gateway unit tests live in
backend/tests/tools)."""

from __future__ import annotations

import json
from datetime import timedelta

from backend.agents.runtime.mock_llm import MockLLMClient, ScriptedLLMClient
from backend.container import Container
from backend.schemas.common import AgentName, Route, SectionStatus, utcnow
from backend.schemas.llm import LLMRequest, LLMResponse
from backend.schemas.tools import FailureKind, ToolOperation, ToolOutcomeStatus
from backend.tests.integration.conftest import ContainerFactory, kyoto_context
from backend.tools.providers.mock.faults import FaultKind, FaultPlan


def _forecast_call(start: str, end: str) -> str:
    return json.dumps(
        {"tool_calls": [{"operation": "forecast", "location": "Kyoto", "start_date": start, "end_date": end}]}
    )


def _weather_records(container: Container, trace_id: str) -> list[ToolOutcomeStatus]:
    return [
        r.status
        for r in container.observability.tool_calls(trace_id)
        if r.agent is AgentName.WEATHER
    ]


# ---- 10. Validation error -> structured error -> model repair (max 2), never resent unchanged ----


async def test_s10_free_text_date_is_repaired_not_resent(make_container: ContainerFactory) -> None:
    bad = json.dumps({"operation": "forecast", "location": "Kyoto", "start_date": "10 April", "end_date": "12 April"})
    agent = (
        ScriptedLLMClient(MockLLMClient())
        .script("weather.tool_call", _forecast_call("10 April", "12 April"))
        # first repair repeats the same invalid call: it must NOT be sent again
        .script("weather.tool_repair", bad)
    )  # second repair falls back to the mock, which returns ISO dates
    container = await make_container(agent_llm=agent)
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")

    statuses = _weather_records(container, result.trace_id)
    assert statuses == [ToolOutcomeStatus.VALIDATION_ERROR, ToolOutcomeStatus.OK]
    first = next(r for r in container.observability.tool_calls(result.trace_id) if r.agent is AgentName.WEATHER)
    assert first.failure is FailureKind.VALIDATION and first.attempts == 0
    assert {i.loc for i in first.issues} == {"start_date", "end_date"}
    repairs = agent.calls_for("weather.tool_repair")
    assert len(repairs) == 2
    assert "start_date" in repairs[0].messages[-1].content  # the structured error went to the model
    assert result.plan is not None
    assert result.plan.section(AgentName.WEATHER).status is SectionStatus.OK  # type: ignore[union-attr]


async def test_s10_repairs_are_bounded_then_section_unavailable(make_container: ContainerFactory) -> None:
    agent = (
        ScriptedLLMClient(MockLLMClient())
        .script("weather.tool_call", _forecast_call("next Friday", "Sunday"))
        .script(
            "weather.tool_repair",
            json.dumps({"operation": "forecast", "location": "Kyoto", "start_date": "Friday", "end_date": "Sunday"}),
            json.dumps({"operation": "forecast", "location": "Kyoto", "start_date": "April 10", "end_date": "April 12"}),
            json.dumps({"operation": "forecast", "location": "Kyoto", "start_date": "2026-04-10", "end_date": "2026-04-12"}),
        )
    )
    container = await make_container(agent_llm=agent)
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")

    assert len(agent.calls_for("weather.tool_repair")) == 2  # third scripted repair never requested
    assert _weather_records(container, result.trace_id) == [ToolOutcomeStatus.VALIDATION_ERROR] * 3
    assert result.plan is not None
    weather = result.plan.section(AgentName.WEATHER)
    assert weather is not None and weather.status is SectionStatus.UNAVAILABLE
    assert all(d.forecast is None for d in result.plan.days)


# ---- 11. Timeout / rate limit / server fault -> retry with backoff + jitter --------------------


async def test_s11_transient_faults_are_retried_within_budget(make_container: ContainerFactory) -> None:
    faults = FaultPlan().fail(ToolOperation.FORECAST, FaultKind.TIMEOUT, FaultKind.RATE_LIMIT)
    container = await make_container(fault_plan=faults)
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")

    record = next(r for r in container.observability.tool_calls(result.trace_id) if r.operation is ToolOperation.FORECAST)
    assert record.status is ToolOutcomeStatus.OK and record.attempts == 3
    assert result.plan is not None
    assert result.plan.section(AgentName.WEATHER).status is SectionStatus.OK  # type: ignore[union-attr]


# ---- 12. Still unusable -> unavailable; the model may not fill the gap ------------------------------


class FillingGapsLLM(MockLLMClient):
    """A planner that invents forecasts and a place the agents never fetched."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await super().complete(request)
        if request.purpose != "planner":
            return response
        plan = json.loads(response.content)
        fake_source = {"tool": "weather", "call_id": "tc_invented", "provider": "model", "fetched_at": utcnow().isoformat()}
        for day in plan["days"]:
            day["forecast"] = {
                "date": day["date"], "summary": "Sunny (from memory)", "temp_min_c": 10, "temp_max_c": 20,
                "precipitation_chance": 0.0, "warning_signal": None, "source": fake_source,
            }
        invented = {
            "place_id": "invented-1", "name": "Imaginary Pagoda", "category": "temple",
            "source": {**fake_source, "tool": "places"},
        }
        plan["places"].append(invented)
        plan["days"][0]["items"].append(
            {"item_id": "inv@1", "place_id": "invented-1", "title": "Imaginary Pagoda", "confirmed": False,
             "needs_reservation": False, "note": None, "start_time": None, "end_time": None}
        )
        return response.model_copy(update={"content": json.dumps(plan)})


async def test_s12_unusable_source_marked_unavailable_and_never_filled(make_container: ContainerFactory) -> None:
    faults = FaultPlan().fail_always(ToolOperation.FORECAST, FaultKind.SERVER_FAULT)
    container = await make_container(fault_plan=faults, agent_llm=FillingGapsLLM())
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")

    record = next(r for r in container.observability.tool_calls(result.trace_id) if r.operation is ToolOperation.FORECAST)
    assert record.status is ToolOutcomeStatus.UNAVAILABLE
    assert record.attempts == container.settings.tool_max_attempts
    plan = result.plan
    assert plan is not None
    weather = plan.section(AgentName.WEATHER)
    assert weather is not None and weather.status is SectionStatus.UNAVAILABLE
    assert all(d.forecast is None for d in plan.days)  # invented forecasts stripped
    assert plan.place("invented-1") is None
    assert all(i.place_id != "invented-1" for i in plan.all_items())
    events = [e.name for e in container.observability.events(result.trace_id)]
    assert "planner_ungrounded_removed" in events


async def test_s12_permanent_failure_is_not_retried(make_container: ContainerFactory) -> None:
    faults = FaultPlan().fail_always(ToolOperation.FORECAST, FaultKind.UNAVAILABLE)
    container = await make_container(fault_plan=faults)
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    record = next(r for r in container.observability.tool_calls(result.trace_id) if r.operation is ToolOperation.FORECAST)
    assert record.failure is FailureKind.PERMANENT and record.attempts == 1


# ---- 13. Timestamps, staleness, refresh of only the stale agent ------------------------------------


async def test_s13_results_timestamped_and_stale_section_refreshed_alone(container: Container) -> None:
    state = await container.orchestrator.create_session(kyoto_context())
    planned = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    plan = planned.plan
    assert plan is not None
    for record in container.observability.tool_calls(planned.trace_id):
        if record.status is ToolOutcomeStatus.OK:
            assert record.fetched_at is not None and record.fetched_at.tzinfo is not None
    assert all(p.source.fetched_at for p in plan.places)
    await container.orchestrator.confirm(state.session_id, item_ids=[i.item_id for i in plan.all_items()])

    # Age the weather section beyond its limit (6 h) while the others stay fresh.
    stored = await container.sessions.get(state.session_id)
    assert stored is not None and stored.plan is not None
    aged = utcnow() - timedelta(hours=7)
    stored.plan = stored.plan.model_copy(
        update={
            "sections": [
                s.model_copy(update={"fetched_at": aged}) if s.agent is AgentName.WEATHER else s
                for s in stored.plan.sections
            ]
        }
    )
    await container.sessions.save(stored)

    view = await container.orchestrator.get_session(state.session_id)
    assert view.plan is not None
    assert view.plan.section(AgentName.WEATHER).status is SectionStatus.STALE  # type: ignore[union-attr]

    result = await container.orchestrator.handle_turn(state.session_id, "Please re-check my plan")
    assert result.route is Route.MODIFY
    assert result.agents_run == [AgentName.WEATHER]
    assert {r.agent for r in container.observability.tool_calls(result.trace_id)} == {AgentName.WEATHER}
    new_plan = result.plan
    assert new_plan is not None
    weather = new_plan.section(AgentName.WEATHER)
    assert weather is not None and weather.status is SectionStatus.OK
    assert weather.fetched_at is not None and weather.fetched_at > aged
    assert [i.item_id for i in new_plan.all_items()] == [i.item_id for i in plan.all_items()]
    assert all(i.confirmed for i in new_plan.all_items())
