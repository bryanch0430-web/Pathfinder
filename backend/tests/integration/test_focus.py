"""Plan workspace: chat messages scoped to one part of the plan (design 4.1), end to end."""

from __future__ import annotations

import json

from backend.agents.prompts import ROUTER_FOCUS_INSTRUCTION, ROUTER_INSTRUCTION
from backend.agents.runtime.mock_handlers import blocks
from backend.container import Container
from backend.schemas.common import Route
from backend.schemas.observability import LLMCallRecord
from backend.schemas.routing import FocusKind, PlanFocus
from backend.schemas.trip_plan import TripPlan
from backend.tests.integration.conftest import kyoto_context


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
