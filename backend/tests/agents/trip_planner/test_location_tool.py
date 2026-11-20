"""
Unit and integration tests for get_user_location tool and its binding to respond_to_user node.
"""
from unittest.mock import AsyncMock, patch
import pytest

from app.agents.trip_planner.graph import trip_planner_graph
from app.agents.trip_planner.nodes.respond_to_user_node import respond_to_user
from app.agents.trip_planner.nodes.understand_user_msg_node import understand_user_message
from app.agents.trip_planner.state import TripPlanningState
from app.agents.trip_planner.tools.location_tool import get_user_location
from app.services.llm.base import LLMResult, ToolCall


# ==============================================================================
# 1. Tool Output Unit Test
# ==============================================================================

def test_get_user_location_tool_structure():
    """Verify get_user_location returns structured UI action without fabricating coordinates."""
    tool_output = get_user_location()
    assert isinstance(tool_output, dict)
    assert tool_output["type"] == "request_user_location"
    assert tool_output["action"] == "use_current_location"
    assert "latitude" not in tool_output
    assert "longitude" not in tool_output


# ==============================================================================
# 2. respond_to_user Node Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_respond_to_user_executes_location_tool():
    """Verify respond_to_user captures tool call and returns structured ui_action."""
    state: TripPlanningState = {
        "trip_id": "test-trip-1",
        "destination": "Japan",
        "duration_days": 10,
        "origin": None,
        "missing_fields": ["origin"],
        "user_message": "use my current location",
        "user_name": "Explorer",
    }

    mock_llm_result = LLMResult(
        text="I'll help you use your device location for departure.",
        tool_calls=[ToolCall(name="get_user_location", args={})],
    )

    with patch("app.services.llm.service.llm_service.generate_with_tools", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_llm_result
        output = await respond_to_user(state)

    assert "ui_action" in output
    assert output["ui_action"]["type"] == "request_user_location"
    assert output["ui_action"]["action"] == "use_current_location"
    assert len(output.get("tool_calls", [])) == 1
    assert output["tool_calls"][0]["name"] == "get_user_location"
    assert "device location" in output["assistant_response"]


@pytest.mark.asyncio
async def test_respond_to_user_fallback_calls_tool_for_location_phrases():
    """Verify fallback executes get_user_location if LLM raises exception on location message."""
    state: TripPlanningState = {
        "trip_id": "test-trip-2",
        "destination": "France",
        "duration_days": 7,
        "origin": None,
        "missing_fields": ["origin"],
        "user_message": "use where I am now",
        "user_name": "Traveler",
    }

    with patch("app.services.llm.service.llm_service.generate_with_tools", side_effect=RuntimeError("LLM offline")):
        output = await respond_to_user(state)

    assert "ui_action" in output
    assert output["ui_action"]["action"] == "use_current_location"
    assert "current location" in output["assistant_response"]


@pytest.mark.asyncio
async def test_respond_to_user_normal_conversation_no_tool():
    """Verify ordinary conversation produces normal text response without ui_action."""
    state: TripPlanningState = {
        "trip_id": "test-trip-3",
        "destination": "Japan",
        "duration_days": 10,
        "origin": None,
        "missing_fields": ["origin"],
        "user_message": "Is Japan expensive in autumn?",
        "user_name": "Rohit",
    }

    mock_llm_result = LLMResult(
        text="Autumn in Japan has wonderful foliage! Where will you be flying out from?",
        tool_calls=[],
    )

    with patch("app.services.llm.service.llm_service.generate_with_tools", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_llm_result
        output = await respond_to_user(state)

    assert "ui_action" not in output
    assert output.get("tool_calls") is None or output.get("tool_calls") == []
    assert "Autumn in Japan" in output["assistant_response"]


# ==============================================================================
# 3. Understand Message & Origin Separation Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_understand_user_message_current_location_leaves_origin_none():
    """Verify understand_user_message leaves origin=None when user requests current location."""
    state: TripPlanningState = {
        "user_message": "use my current location",
        "destination": "Japan",
        "duration_days": 10,
        "origin": None,
    }

    updates = await understand_user_message(state)
    assert updates.get("origin") is None or updates.get("origin") != "current location"


@pytest.mark.asyncio
async def test_understand_user_message_manual_origin_extracts_correctly():
    """Verify manual origin (e.g. Delhi) is cleanly extracted without tool trigger."""
    state: TripPlanningState = {
        "user_message": "I am travelling from Delhi",
        "destination": "Japan",
        "duration_days": 10,
        "origin": None,
    }

    updates = await understand_user_message(state)
    assert updates.get("origin") == "Delhi"


# ==============================================================================
# 4. LangGraph End-to-End Test
# ==============================================================================

@pytest.mark.asyncio
async def test_incomplete_graph_uses_form_without_location_tool():
    state: TripPlanningState = {
        "trip_id": "test-e2e-location", "user_message": "use my current location",
        "destination": "Japan", "duration_days": 10, "origin": None,
        "onboarding_complete": False,
    }
    result = await trip_planner_graph.ainvoke(state)
    assert result["onboarding_complete"] is False
    assert result["ui_action"]["action"] == "SHOW_ONBOARDING_FORM"
    assert "origin" in result["missing_fields"]
