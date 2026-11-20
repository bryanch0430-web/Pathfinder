import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from app.agents.trip_planner.graph import trip_planner_graph
from app.agents.trip_planner.nodes.validate_state_node import validate_state
from app.services.llm.base import LLMResult


# ==============================================================================
# Automated Pytest Suite
# ==============================================================================

def test_validate_state_missing_all():
    """Verify validation node identifies missing destination, duration, and origin."""
    state = {
        "trip_id": "test-1",
        "destination": None,
        "duration_days": None,
        "origin": None,
    }
    result = validate_state(state)
    assert result["onboarding_complete"] is False
    assert "destination" in result["missing_fields"]
    assert "duration_days" in result["missing_fields"]
    assert "origin" in result["missing_fields"]


def test_validate_state_partial_destination():
    """Verify validation node flags duration and origin when only destination is provided."""
    state = {
        "trip_id": "test-2",
        "destination": "Paris",
        "duration_days": None,
        "origin": None,
    }
    result = validate_state(state)
    assert result["onboarding_complete"] is False
    assert set(result["missing_fields"]) == {"duration_days", "origin"}


def test_validate_state_missing_origin():
    """Verify validation node flags origin as missing when destination and duration exist."""
    state = {
        "trip_id": "test-3",
        "destination": "Japan",
        "duration_days": 10,
        "origin": None,
    }
    result = validate_state(state)
    assert result["onboarding_complete"] is False
    assert result["missing_fields"] == ["origin"]


def test_validate_state_invalid_duration():
    """Verify validation node treats non-positive duration as missing."""
    state = {
        "trip_id": "test-invalid-dur",
        "destination": "Tokyo",
        "duration_days": 0,
        "origin": "Delhi",
    }
    result = validate_state(state)
    assert result["onboarding_complete"] is False
    assert "duration_days" in result["missing_fields"]


def test_validate_state_complete():
    """Verify validation node succeeds only when destination, duration, and origin exist."""
    state = {
        "trip_id": "test-4",
        "destination": "Tokyo",
        "duration_days": 5,
        "origin": "Delhi",
    }
    result = validate_state(state)
    assert result["onboarding_complete"] is True
    assert result["missing_fields"] == []


def test_validate_state_complete_with_coordinates():
    """Verify validation node accepts geolocation coordinates as valid origin."""
    state = {
        "trip_id": "test-5",
        "destination": "Kyoto",
        "duration_days": 7,
        "origin": None,
        "origin_latitude": 35.6762,
        "origin_longitude": 139.6503,
    }
    result = validate_state(state)
    assert result["onboarding_complete"] is True
    assert result["missing_fields"] == []


@pytest.mark.asyncio
async def test_graph_greets_before_asking_for_details_without_llm():
    state = {
        "trip_id": "new", "user_message": "", "origin": None,
        "destination": None, "duration_days": None, "places_to_visit": [],
        "onboarding_complete": False,
    }
    with patch("app.services.llm.service.llm_service.generate", side_effect=AssertionError("LLM called")):
        result = await trip_planner_graph.ainvoke(state)
    assert result.get("ui_action") is None
    assert "Pathfinder" in result["assistant_response"]


@pytest.mark.asyncio
async def test_pre_form_travel_question_stays_in_chat():
    state = {
        "trip_id": "new", "user_message": "Would spring be a good time to visit Japan?",
        "origin": None, "destination": None, "duration_days": None,
        "onboarding_complete": False,
    }
    understood = '{"intent":"trip_question","destination":"Japan","duration_days":null,"origin":null,"user_name":null}'
    with patch("app.services.llm.service.llm_service.generate", new_callable=AsyncMock, return_value=understood), patch(
        "app.services.llm.service.llm_service.generate_with_tools", new_callable=AsyncMock,
        return_value=LLMResult(text="Spring can be lovely; dates and crowds matter.", tool_calls=[]),
    ):
        result = await trip_planner_graph.ainvoke(state)
    assert result.get("ui_action") is None
    assert "Spring can be lovely" in result["assistant_response"]
    assert result["destination"] == "Japan"


@pytest.mark.asyncio
async def test_form_submission_asks_for_mode_before_first_draft():
    state = {
        "trip_id": "new", "user_message": "", "onboarding_complete": False,
        "ui_action": {
            "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi",
            "destination": "Japan", "places_to_visit": ["Tokyo", "Kyoto"],
            "duration_days": 8,
            "planning_preferences": {"pace": "relaxed", "interests": ["Food"], "avoid": ["Early starts"]},
        },
    }
    with patch("app.services.llm.service.llm_service.generate", side_effect=AssertionError("LLM called")):
        choice = await trip_planner_graph.ainvoke(state)
    assert choice["onboarding_complete"] is True
    assert choice["places_to_visit"] == ["Tokyo", "Kyoto"]
    assert choice["planning_preferences"]["pace"] == "relaxed"
    assert choice["ui_action"]["action"] == "SHOW_PLANNING_CHOICE"
    assert not choice.get("itinerary_graph")

    planning = {"trip_type": "multi_city", "planning_notes": "Keep a calm pace.",
                "research_queries": [], "research_results": [],
                "candidates": [{"name": "Tokyo", "type": "city", "reason": "Requested"}]}
    with (patch("app.agents.trip_planner.nodes.planning_trip_node.planning_graph.ainvoke", return_value=planning),
          patch("app.services.llm.service.llm_service.generate", return_value="First draft") as generate):
        result = await trip_planner_graph.ainvoke({
            **choice, "ui_action": {"action": "GENERATE_FULL_ITINERARY"},
        })
    assert result["onboarding_complete"] is True
    assert result["places_to_visit"] == ["Tokyo", "Kyoto"]
    assert result["assistant_response"] == "First draft"
    assert "Kyoto" in generate.await_args_list[0].kwargs["prompt"]
    assert "Early starts" in generate.await_args_list[0].kwargs["prompt"]
    assert result["itinerary_graph"]["nodes"][0]["name"] == "Delhi"
