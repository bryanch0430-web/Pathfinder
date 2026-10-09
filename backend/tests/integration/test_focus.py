"""Plan workspace: chat messages scoped to one part of the plan (design 4.1), end to end."""

from __future__ import annotations

import json
from datetime import timedelta

from backend.agents.prompts import ROUTER_FOCUS_INSTRUCTION, ROUTER_INSTRUCTION
from backend.agents.runtime.mock_handlers import blocks
from backend.agents.runtime.mock_llm import ScriptedLLMClient
from backend.container import Container
from backend.schemas.common import AgentName, Route, SectionStatus, utcnow
from backend.schemas.observability import LLMCallRecord
from backend.schemas.routing import FocusKind, PlanFocus
from backend.schemas.trip_plan import TripPlan
from backend.tests.integration.conftest import ContainerFactory, kyoto_context


async def _planned(container: Container) -> tuple[str, TripPlan]:
    state = await container.orchestrator.create_session(kyoto_context())
    result = await container.orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
    assert result.plan is not None
    return state.session_id, result.plan


def _call(container: Container, trace_id: str, purpose: str) -> LLMCallRecord:
    return next(r for r in container.observability.llm_calls(trace_id) if r.purpose == purpose)


# ---- prompting: the focus is fenced data, never part of the user's text ---------------------------


async def test_focus_reaches_the_router_and_ask_as_a_fenced_block(container: Container) -> None:
    session_id, plan = await _planned(container)
    target = plan.days[0].items[1]
    message = "What time do we get there?"

    result = await container.orchestrator.handle_turn(
        session_id, message, focus=PlanFocus(kind=FocusKind.ITEM, id=target.item_id)
    )

    router_call = _call(container, result.trace_id, "router")
    user_turn = router_call.request.messages[1].content
    assert user_turn.startswith(ROUTER_FOCUS_INSTRUCTION)
    assert f'<untrusted_data label="user_message">\n{message}\n</untrusted_data>' in user_turn
    focus_block = json.loads(blocks(router_call.request)["focus"])
    assert focus_block["kind"] == "item" and focus_block["id"] == target.item_id
    assert focus_block["title"] == target.title
    assert "focus" in blocks(_call(container, result.trace_id, "ask").request)
    events = [e.name for e in container.observability.events(result.trace_id)]
    assert "focus" in events


async def test_without_a_focus_the_prompts_are_unchanged(container: Container) -> None:
    session_id, _ = await _planned(container)
    result = await container.orchestrator.handle_turn(session_id, "What time do we get there?")
    router_call = _call(container, result.trace_id, "router")
    assert router_call.request.messages[1].content.startswith(ROUTER_INSTRUCTION + "\n\n")
    assert "focus" not in blocks(router_call.request)
    assert "focus" not in blocks(_call(container, result.trace_id, "ask").request)


# ---- routing: a quick question with a focus stays a quick question -------------------------------


async def test_focus_on_a_quick_question_still_routes_to_ask(container: Container) -> None:
    session_id, plan = await _planned(container)
    target = plan.days[0].items[1]

    result = await container.orchestrator.handle_turn(
        session_id, "What time do we get there?", focus=PlanFocus(kind=FocusKind.ITEM, id=target.item_id)
    )

    assert result.route is Route.ASK
    assert result.agents_run == [] and container.observability.tool_calls(result.trace_id) == []
    assert result.answer is not None and target.title in result.answer.text
    assert result.plan == plan  # a question changes nothing


# ---- modify scope: one stop -------------------------------------------------------------------------


async def test_focused_item_swap_changes_only_that_stop(container: Container) -> None:
    session_id, plan = await _planned(container)
    target = plan.days[0].items[1]  # Fushimi Inari Taisha, 12:30-14:30, free entry

    result = await container.orchestrator.handle_turn(
        session_id, "Swap this for an aquarium", focus=PlanFocus(kind=FocusKind.ITEM, id=target.item_id)
    )

    assert result.route is Route.MODIFY
    assert result.agents_run == [AgentName.ATTRACTION]
    assert {r.agent for r in container.observability.tool_calls(result.trace_id)} == {AgentName.ATTRACTION}
    new_plan = result.plan
    assert new_plan is not None and new_plan.version == plan.version + 1
    swapped = new_plan.days[0].items[1]
    place = new_plan.place(swapped.place_id)
    assert place is not None and place.category == "aquarium"
    assert (swapped.start_time, swapped.end_time) == (target.start_time, target.end_time)
    assert [i for i in new_plan.all_items() if i.item_id != swapped.item_id] == [
        i for i in plan.all_items() if i.item_id != target.item_id
    ]  # every other stop is byte-identical
    assert new_plan.hotel == plan.hotel and new_plan.tickets == plan.tickets
    assert new_plan.place(target.place_id) is None  # the old place is no longer referenced
    assert new_plan.cost is not None and plan.cost is not None and place.price is not None
    assert new_plan.cost.attractions == plan.cost.attractions + place.price.amount * plan.party_size


async def test_a_locked_focused_stop_is_left_alone(container: Container) -> None:
    session_id, plan = await _planned(container)
    target = plan.days[0].items[1]
    await container.orchestrator.confirm(session_id, item_ids=[target.item_id])
    locked = (await container.orchestrator.get_session(session_id)).plan

    result = await container.orchestrator.handle_turn(
        session_id, "Swap this for an aquarium", focus=PlanFocus(kind=FocusKind.ITEM, id=target.item_id)
    )

    assert result.route is Route.MODIFY and result.agents_run == []
    assert container.observability.tool_calls(result.trace_id) == []
    assert "modify.extract" not in [r.purpose for r in container.observability.llm_calls(result.trace_id)]
    assert result.plan == locked
    assert "unlock it first" in result.reply


async def test_router_parts_outside_the_focus_are_not_run(make_container: ContainerFactory) -> None:
    router = ScriptedLLMClient()
    container = await make_container(router_llm=router)
    session_id, plan = await _planned(container)
    target = plan.days[0].items[1]
    router.script(
        "router",
        json.dumps(
            {"route": "modify", "confidence": 0.95, "clarity": 0.9, "needs_clarification": False,
             "affected_parts": ["hotel", "weather"]}
        ),
    )

    result = await container.orchestrator.handle_turn(
        session_id, "Swap this for an aquarium", focus=PlanFocus(kind=FocusKind.ITEM, id=target.item_id)
    )

    assert result.agents_run == [AgentName.ATTRACTION]  # the focus mapping wins over disjoint parts
    assert result.plan is not None and result.plan.hotel == plan.hotel


# ---- modify scope: the hotel -------------------------------------------------------------------------


async def test_focused_hotel_swap_reruns_only_the_hotel_agent(container: Container) -> None:
    session_id, plan = await _planned(container)
    assert plan.hotel is not None
    # An aged weather section is normally refreshed by any modify turn; a focused turn leaves it.
    stored = await container.sessions.get(session_id)
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

    result = await container.orchestrator.handle_turn(
        session_id, "Swap this for a different one", focus=PlanFocus(kind=FocusKind.HOTEL, id=plan.hotel.hotel.hotel_id)
    )

    assert result.route is Route.MODIFY
    assert result.agents_run == [AgentName.HOTEL]
    assert {r.agent for r in container.observability.tool_calls(result.trace_id)} == {AgentName.HOTEL}
    new_plan = result.plan
    assert new_plan is not None and new_plan.hotel is not None
    assert new_plan.hotel.hotel.hotel_id != plan.hotel.hotel.hotel_id
    assert new_plan.days == plan.days and new_plan.tickets == plan.tickets
    weather = new_plan.section(AgentName.WEATHER)
    assert weather is not None and weather.status is SectionStatus.STALE
