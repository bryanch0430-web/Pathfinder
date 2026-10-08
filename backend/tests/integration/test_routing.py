"""Routing scenarios 1-5, end to end through the orchestrator."""

from __future__ import annotations

import json
import time

from backend.agents.runtime.mock_llm import ScriptedLLMClient
from backend.container import Container
from backend.schemas.common import ALL_AGENTS, AgentName, Route, SectionStatus
from backend.schemas.routing import GateReason
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import TripPlan
from backend.schemas.tools import ToolOperation
from backend.tests.integration.conftest import ContainerFactory, kyoto_context, make_settings
from backend.tools.providers.mock.faults import FaultPlan


async def _planned(container: Container) -> tuple[str, TripPlan]:
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    assert result.plan is not None
    return state.session_id, result.plan


def _purposes(container: Container, trace_id: str) -> list[str]:
    return [r.purpose for r in container.observability.llm_calls(trace_id)]


# ---- 1. New trip --------------------------------------------------------------------------------


async def test_s01_new_trip_runs_four_agents_then_planner(container: Container) -> None:
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")

    assert result.route is Route.PLAN
    assert result.gate.reason is GateReason.ACCEPTED
    assert result.gate.decision is not None
    assert result.gate.decision.confidence >= container.settings.router_confidence_threshold
    assert result.agents_run == list(ALL_AGENTS)
    assert _purposes(container, result.trace_id).count("planner") == 1
    assert result.plan is not None and result.error is None
    TripPlan.model_validate(result.plan.model_dump())  # schema check passes
    assert {s.agent for s in result.plan.sections} == set(ALL_AGENTS)
    assert all(s.status is SectionStatus.OK for s in result.plan.sections)
    assert result.plan.hotel is not None and result.plan.tickets and result.plan.all_items()


async def test_s01_agents_run_concurrently(make_container: ContainerFactory) -> None:
    faults = FaultPlan()
    for op in (ToolOperation.WEB_SEARCH, ToolOperation.FORECAST, ToolOperation.PLACES_SEARCH, ToolOperation.TICKET_SEARCH):
        faults.set_latency(op, 0.3)
    container = await make_container(fault_plan=faults)
    state = await container.orchestrator.create_session(kyoto_context())
    started = time.perf_counter()
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    elapsed = time.perf_counter() - started
    assert result.plan is not None
    # Sequentially the slow calls alone take >= 1.8 s (6 x 0.3 s); in parallel about 0.6 s.
    assert elapsed < 1.5


# ---- 2. Modify -----------------------------------------------------------------------------------


async def test_s02_swap_hotel_reruns_only_hotel_agent_and_preserves_confirmed(container: Container) -> None:
    session_id, plan = await _planned(container)
    day1_ids = [i.item_id for i in plan.days[0].items]
    await container.orchestrator.confirm(session_id, item_ids=day1_ids, ticket_ids=[t.ticket_id for t in plan.tickets])
    confirmed_before = (await container.orchestrator.get_session(session_id)).plan
    assert confirmed_before is not None

    result = await container.orchestrator.handle_turn(session_id, "Can you swap the hotel for a different one?")

    assert result.route is Route.MODIFY
    assert result.agents_run == [AgentName.HOTEL]
    tool_agents = {r.agent for r in container.observability.tool_calls(result.trace_id)}
    assert tool_agents == {AgentName.HOTEL}
    assert "planner" not in _purposes(container, result.trace_id)  # no full pipeline
    new_plan = result.plan
    assert new_plan is not None and new_plan.hotel is not None and plan.hotel is not None
    assert new_plan.hotel.hotel.hotel_id != plan.hotel.hotel.hotel_id
    assert new_plan.version == confirmed_before.version + 1
    assert new_plan.days[0].items == confirmed_before.days[0].items  # confirmed items untouched
    assert new_plan.tickets == confirmed_before.tickets
    assert all(t.confirmed for t in new_plan.tickets)
    assert new_plan.cost is not None  # re-checked after merge


async def test_s02_cheaper_hotel_swap_never_picks_a_pricier_hotel(container: Container) -> None:
    session_id, _ = await _planned(container)
    pricier = await container.orchestrator.handle_turn(session_id, "Swap the hotel for a luxury one")
    assert pricier.plan is not None and pricier.plan.hotel is not None
    before = pricier.plan.hotel.hotel

    result = await container.orchestrator.handle_turn(session_id, "Swap the hotel for something cheaper")

    assert result.route is Route.MODIFY and result.agents_run == [AgentName.HOTEL]
    assert result.plan is not None and result.plan.hotel is not None
    after = result.plan.hotel.hotel
    assert after.hotel_id != before.hotel_id
    assert after.nightly_price.currency == before.nightly_price.currency
    assert after.nightly_price.amount < before.nightly_price.amount


async def test_s02_cheaper_hotel_swap_keeps_the_cheapest_hotel(container: Container) -> None:
    session_id, plan = await _planned(container)
    assert plan.hotel is not None
    prices = [plan.hotel.hotel.nightly_price.amount]
    for _ in range(4):  # keep asking: the price may only go down, then the hotel is kept
        result = await container.orchestrator.handle_turn(session_id, "Swap the hotel for something cheaper")
        assert result.plan is not None and result.plan.hotel is not None
        prices.append(result.plan.hotel.hotel.nightly_price.amount)
    assert prices == sorted(prices, reverse=True)
    assert prices[-1] == prices[-2]
    assert "no cheaper hotel" in result.reply


async def test_s02_change_dates_reruns_date_bound_agents_and_moves_confirmed_items(container: Container) -> None:
    session_id, plan = await _planned(container)
    first_day_ids = [i.item_id for i in plan.days[0].items]
    await container.orchestrator.confirm(session_id, item_ids=first_day_ids)

    result = await container.orchestrator.handle_turn(session_id, "Please change the dates to 14-16 April")

    assert result.route is Route.MODIFY
    assert set(result.agents_run) == {AgentName.WEATHER, AgentName.HOTEL, AgentName.TICKET}
    new_plan = result.plan
    assert new_plan is not None
    assert (new_plan.start_date.isoformat(), new_plan.end_date.isoformat()) == ("2026-04-14", "2026-04-16")
    assert [i.item_id for i in new_plan.days[0].items if i.confirmed] == first_day_ids
    assert all(d.forecast is not None and d.forecast.date == d.date for d in new_plan.days)
    assert new_plan.hotel is not None and new_plan.hotel.check_in.isoformat() == "2026-04-14"
    assert {t.depart_at.date().isoformat() for t in new_plan.tickets} <= {"2026-04-14", "2026-04-16"}
    assert not [v for v in new_plan.violations if v.check.value == "dates"]


async def test_s02_change_budget_reruns_hotel_and_rechecks_budget(container: Container) -> None:
    session_id, _ = await _planned(container)
    result = await container.orchestrator.handle_turn(session_id, "Cut the budget to 100000 yen please")
    assert result.route is Route.MODIFY
    assert AgentName.HOTEL in result.agents_run
    assert AgentName.ATTRACTION not in result.agents_run
    assert result.plan is not None and result.plan.budget is not None
    assert result.plan.budget.amount == 100_000
    assert result.plan.cost is not None
    over = result.plan.cost.total > 100_000
    assert over == any(v.check.value == "budget" for v in result.plan.violations)


# ---- 3. Quick question ---------------------------------------------------------------------------


async def test_s03_question_answered_from_plan_without_tool_call(container: Container) -> None:
    session_id, _ = await _planned(container)
    result = await container.orchestrator.handle_turn(session_id, "What is the weather in Kyoto on 11 April?")
    assert result.route is Route.ASK
    assert result.answer is not None and result.answer.from_plan and result.answer.related_to_plan
    assert container.observability.tool_calls(result.trace_id) == []
    assert _purposes(container, result.trace_id) == ["router", "ask"]  # one model call on the path
    assert result.agents_run == []


async def test_s03_question_not_in_plan_makes_exactly_one_tool_call(container: Container) -> None:
    state = await container.orchestrator.create_session(TripContext())
    result = await container.orchestrator.handle_turn(state.session_id, "What is the weather in Kyoto on 12 April?")
    assert result.route is Route.ASK
    calls = container.observability.tool_calls(result.trace_id)
    assert len(calls) == 1 and calls[0].operation is ToolOperation.FORECAST
    assert _purposes(container, result.trace_id).count("ask") == 1
    assert result.answer is not None and result.answer.tool_called and not result.answer.from_plan
    assert result.answer.sources and result.answer.sources[0].call_id == calls[0].call_id


async def test_s03_unrelated_question_is_flagged_as_not_part_of_plan(container: Container) -> None:
    session_id, _ = await _planned(container)
    result = await container.orchestrator.handle_turn(session_id, "What is the weather in Osaka on 20 May?")
    assert result.answer is not None
    assert result.answer.related_to_plan is False
    assert len(container.observability.tool_calls(result.trace_id)) == 1
    assert "not part of your current plan" in result.answer.text


# ---- 4. Unclear / below threshold ------------------------------------------------------------------


async def test_s04_unclear_message_gets_clarification_and_no_planning(container: Container) -> None:
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "hmm")
    assert result.route is Route.UNCLEAR
    assert result.clarification is not None and result.clarification.text
    assert result.plan is None and result.agents_run == []
    assert container.observability.tool_calls(result.trace_id) == []


async def test_s04_below_threshold_asks_instead_of_planning(make_container: ContainerFactory) -> None:
    router = ScriptedLLMClient().script(
        "router",
        json.dumps({"route": "plan", "confidence": 0.62, "clarity": 0.6, "needs_clarification": False, "affected_parts": []}),
    )
    container = await make_container(router_llm=router)
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "maybe a trip somewhere")
    assert result.route is Route.UNCLEAR
    assert result.gate.reason is GateReason.BELOW_THRESHOLD
    assert result.plan is None and result.clarification is not None
    assert container.observability.tool_calls(result.trace_id) == []
    assert "planner" not in _purposes(container, result.trace_id)


async def test_s04_threshold_comes_from_settings(make_container: ContainerFactory) -> None:
    router = ScriptedLLMClient().script(
        "router",
        json.dumps({"route": "plan", "confidence": 0.62, "clarity": 0.6, "needs_clarification": False, "affected_parts": []}),
    )
    container = await make_container(settings=make_settings(router_confidence_threshold=0.6), router_llm=router)
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "maybe a trip")
    assert result.route is Route.PLAN


# ---- 5. Missing key variables ----------------------------------------------------------------------


async def test_s05_missing_dates_party_budget_are_not_guessed(container: Container) -> None:
    state = await container.orchestrator.create_session(TripContext(destination="Kyoto"))
    result = await container.orchestrator.handle_turn(state.session_id, "Plan a trip to Kyoto for me")
    assert result.route is Route.UNCLEAR
    assert result.gate.reason is GateReason.MISSING_FIELDS
    assert result.gate.missing_fields == ["dates", "party_size", "budget"]
    assert result.clarification is not None
    text = result.clarification.text.lower()
    assert "date" in text and "people" in text and "budget" in text
    assert result.plan is None
    assert container.observability.tool_calls(result.trace_id) == []
