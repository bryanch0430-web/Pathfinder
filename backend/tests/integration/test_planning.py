"""Planning scenarios 6-9."""

from __future__ import annotations

import itertools
import json
import time

from backend.agents.plan_ops import hotel_point, locator
from backend.agents.route_order import NearestNeighbourOrderer, route_length_km
from backend.agents.runtime.mock_llm import MockLLMClient, ScriptedLLMClient
from backend.container import Container
from backend.schemas.common import AgentName, GeoPoint, Route, SectionStatus
from backend.schemas.trip_plan import ItineraryItem
from backend.schemas.tools import ToolOperation
from backend.schemas.turn import TurnErrorCode
from backend.tests.integration.conftest import ContainerFactory, kyoto_context, make_settings
from backend.tools.providers.mock.faults import FaultPlan

# ---- 6. Time budget ---------------------------------------------------------------------------------


async def test_s06_time_budget_marks_unfinished_section_unavailable_and_still_plans(
    make_container: ContainerFactory,
) -> None:
    faults = FaultPlan().set_latency(ToolOperation.FORECAST, 3.0)
    container = await make_container(
        settings=make_settings(preplanning_time_budget_s=0.5), fault_plan=faults
    )
    state = await container.orchestrator.create_session(kyoto_context())
    started = time.perf_counter()
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    elapsed = time.perf_counter() - started

    assert elapsed < 2.5  # did not wait for the 3 s forecast
    assert result.plan is not None and result.error is None
    weather = result.plan.section(AgentName.WEATHER)
    assert weather is not None and weather.status is SectionStatus.UNAVAILABLE
    assert "budget" in (weather.reason or "")  # outer budget cancel or per-call budget exhaustion
    assert all(d.forecast is None for d in result.plan.days)  # left empty, not filled in
    others = [s for s in result.plan.sections if s.agent is not AgentName.WEATHER]
    assert all(s.status is SectionStatus.OK for s in others)
    assert "Unavailable: weather" in result.reply


# ---- 7. Saved-trip retrieval with similarity cutoff -------------------------------------------------


async def _save_useful_kyoto(container: Container) -> str:
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    assert result.plan is not None
    assert await container.orchestrator.mark_useful(state.session_id, useful=True, rating=5)
    await container.writer.drain()
    hits = await container.repos.embeddings.search(
        (await container.embedder.embed(["kyoto"]))[0], model=container.embedder.model, top_k=10, min_similarity=-1
    )
    assert len(hits) == 1
    return hits[0].trip_id


async def test_s07_match_above_cutoff_is_merged_into_planner_input(container: Container) -> None:
    trip_id = await _save_useful_kyoto(container)
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    assert result.plan is not None
    assert result.plan.saved_trip_refs == [trip_id]
    planner_calls = [r for r in container.observability.llm_calls(result.trace_id) if r.purpose == "planner"]
    assert trip_id in planner_calls[0].request.messages[-1].content


async def test_s07_match_below_cutoff_is_ignored(container: Container) -> None:
    await _save_useful_kyoto(container)
    state = await container.orchestrator.create_session(
        kyoto_context(destination="Hong Kong", origin="Shenzhen", budget={"amount": 20000, "currency": "HKD"})
    )
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Hong Kong please")
    assert result.plan is not None
    assert result.plan.saved_trip_refs == []
    planner_calls = [r for r in container.observability.llm_calls(result.trace_id) if r.purpose == "planner"]
    assert '"saved_trips":[]' in planner_calls[0].request.messages[-1].content


async def test_s07_cutoff_is_configurable(make_container: ContainerFactory) -> None:
    container = await make_container(settings=make_settings(similarity_cutoff=0.9999))
    await _save_useful_kyoto(container)
    state = await container.orchestrator.create_session(kyoto_context(party_size=4))
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    assert result.plan is not None and result.plan.saved_trip_refs == []


# ---- 8. Schema failure -> bounded repair -> error, never an invalid plan ---------------------------


async def test_s08_plan_failing_schema_after_two_repairs_returns_error(make_container: ContainerFactory) -> None:
    agent = ScriptedLLMClient(MockLLMClient()).script("planner", "{not json").script(
        "planner.repair", json.dumps({"plan_id": "x"}), json.dumps({"destination": "Kyoto", "days": []})
    )
    container = await make_container(agent_llm=agent)
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")

    assert result.plan is None
    assert result.error is not None and result.error.code is TurnErrorCode.PLAN_INVALID
    assert "try again" in result.reply.lower() or "retry" in result.reply.lower()
    assert len(agent.calls_for("planner")) == 1
    assert len(agent.calls_for("planner.repair")) == container.settings.max_model_repairs == 2
    stored = await container.orchestrator.get_session(state.session_id)
    assert stored.plan is None


async def test_s08_repair_that_succeeds_returns_valid_plan(make_container: ContainerFactory) -> None:
    agent = ScriptedLLMClient(MockLLMClient()).script("planner", json.dumps({"plan_id": "x"}))
    container = await make_container(agent_llm=agent)
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    assert result.plan is not None and result.error is None
    assert len(agent.calls_for("planner.repair")) == 1
    repair = agent.calls_for("planner.repair")[0]
    assert "failed validation" in repair.messages[-1].content  # structured errors shown to the model


async def test_s08_failed_replan_keeps_previous_valid_plan(make_container: ContainerFactory) -> None:
    agent = ScriptedLLMClient(MockLLMClient())
    container = await make_container(agent_llm=agent)
    state = await container.orchestrator.create_session(kyoto_context())
    first = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    assert first.plan is not None
    agent.script("planner", "{}").script("planner.repair", "{}", "{}")
    second = await container.orchestrator.handle_turn(state.session_id, "Plan a new trip to Kyoto please")
    assert second.route is Route.PLAN
    assert second.error is not None
    assert second.plan == first.plan


# ---- 9. Daily route order without backtracking -----------------------------------------------------


def _items(n: int) -> list[ItineraryItem]:
    return [ItineraryItem(item_id=f"i{k}", place_id=f"p{k}", title=f"P{k}") for k in range(n)]


def test_s09_nearest_neighbour_orders_stops_along_a_line() -> None:
    points = {f"p{k}": GeoPoint(lat=35.0, lng=135.70 + 0.01 * k) for k in range(5)}
    shuffled = [_items(5)[k] for k in (3, 0, 4, 1, 2)]
    ordered = NearestNeighbourOrderer().order(GeoPoint(lat=35.0, lng=135.69), shuffled, points.get)
    assert [i.place_id for i in ordered] == ["p0", "p1", "p2", "p3", "p4"]


def test_s09_route_is_never_longer_than_input_order_and_near_optimal() -> None:
    points = {
        "p0": GeoPoint(lat=35.00, lng=135.70),
        "p1": GeoPoint(lat=35.05, lng=135.80),
        "p2": GeoPoint(lat=35.01, lng=135.71),
        "p3": GeoPoint(lat=35.06, lng=135.79),
    }
    start = GeoPoint(lat=34.99, lng=135.69)
    items = _items(4)
    ordered = NearestNeighbourOrderer().order(start, items, points.get)
    nn = route_length_km(start, ordered, points.get)
    assert nn <= route_length_km(start, items, points.get)
    best = min(route_length_km(start, list(p), points.get) for p in itertools.permutations(items))
    assert nn <= best * 1.25


def test_s09_unlocated_stops_are_kept_at_the_end() -> None:
    points = {"p0": GeoPoint(lat=35.0, lng=135.7)}
    ordered = NearestNeighbourOrderer().order(None, _items(2), points.get)
    assert [i.place_id for i in ordered] == ["p0", "p1"]


async def test_s09_planned_days_follow_nearest_neighbour_order_from_hotel(container: Container) -> None:
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    plan = result.plan
    assert plan is not None
    locate = locator(plan)
    for day in plan.days:
        expected = NearestNeighbourOrderer().order(hotel_point(plan), day.items, locate)
        assert [i.place_id for i in day.items] == [i.place_id for i in expected]
        times = [i.start_time for i in day.items]
        assert times == sorted(t for t in times if t is not None)
