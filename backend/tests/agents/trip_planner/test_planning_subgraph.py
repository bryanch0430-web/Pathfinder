import json
from unittest.mock import AsyncMock, patch
import pytest

from app.agents.trip_planner.nodes.planning_trip_node import planning_trip
from app.agents.trip_planner.planning.graph import planning_graph
from app.agents.trip_planner.planning.nodes.research_destination import (
    research_destination,
)
from app.agents.trip_planner.planning.nodes.tool_node import tool_node
from app.agents.trip_planner.planning.nodes.understand_trip import understand_trip
from app.agents.trip_planner.planning.routing import route_after_research
from app.agents.trip_planner.planning.tools.web_search import search_web
from app.agents.trip_planner.planning.state import (
    PlanningState,
    create_initial_planning_state,
)


# ==============================================================================
# 1. State Mapping Tests
# ==============================================================================

def test_create_initial_planning_state():
    """Verify PlanningState is initialized with required fields and empty planning collections."""
    state = create_initial_planning_state(
        destination="Japan",
        duration_days=10,
        origin="Delhi",
    )
    assert state["destination"] == "Japan"
    assert state["duration_days"] == 10
    assert state["origin"] == "Delhi"
    assert state["trip_type"] is None
    assert state["planning_notes"] is None
    assert state["research_queries"] == []
    assert state["research_results"] == []
    assert state["candidates"] == []
    assert "_tool_call_pending" in state
    assert state["_tool_call_pending"] is False
    # Subgraph state does NOT contain assistant_response
    assert "assistant_response" not in state


# ==============================================================================
# 2. Node Unit Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_understand_trip_node_success():
    """Verify understand_trip parses structured LLM output for trip_type and planning_notes."""
    state = create_initial_planning_state(
        destination="Japan",
        duration_days=10,
        origin="Delhi",
    )
    mock_llm_json = json.dumps({
        "trip_type": "multi_city",
        "planning_notes": "A 10-day trip to Japan allows for 2-3 major hubs connected by Shinkansen.",
    })

    with patch("app.services.llm.service.llm_service.generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_llm_json
        updates = await understand_trip(state)

    assert updates["trip_type"] == "multi_city"
    assert "Shinkansen" in updates["planning_notes"]


@pytest.mark.asyncio
async def test_understand_trip_node_fallback():
    """Verify understand_trip falls back to deterministic logic when LLM fails."""
    state = create_initial_planning_state(
        destination="Paris",
        duration_days=3,
        origin="London",
    )

    with patch("app.services.llm.service.llm_service.generate", side_effect=RuntimeError("API error")):
        updates = await understand_trip(state)

    assert updates["trip_type"] == "single_city"
    assert "Paris" in updates["planning_notes"]


@pytest.mark.asyncio
async def test_research_destination_generates_queries_when_search_needed():
    """Verify research_destination requests tool execution when research is needed."""
    state: PlanningState = {
        "destination": "Switzerland",
        "duration_days": 7,
        "origin": "Mumbai",
        "trip_type": "regional_tour",
        "planning_notes": "Alpine scenic routes and mountain hubs.",
        "research_queries": [],
        "research_results": [],
        "candidates": [],
        "_tool_call_pending": False,
    }

    mock_llm_json = json.dumps({
        "needs_search": True,
        "search_queries": [
            "top places to visit in Switzerland 7 days",
            "Switzerland scenic alpine hubs",
        ],
        "candidates": [],
    })

    with patch("app.services.llm.service.llm_service.generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_llm_json
        updates = await research_destination(state)

    assert updates["_tool_call_pending"] is True
    assert len(updates["research_queries"]) == 2
    assert "top places to visit in Switzerland 7 days" in updates["research_queries"]


@pytest.mark.asyncio
async def test_tool_node_executes_queries():
    """Verify tool_node executes search_web for queries and updates research_results."""
    state: PlanningState = {
        "destination": "Switzerland",
        "duration_days": 7,
        "origin": "Mumbai",
        "trip_type": "regional_tour",
        "planning_notes": "",
        "research_queries": ["best places in Switzerland"],
        "research_results": [],
        "candidates": [],
        "_tool_call_pending": True,
    }

    mock_search_results = [
        {
            "title": "Top Swiss Destinations",
            "url": "https://example.com/swiss",
            "content": "Zermatt, Interlaken, and Lucerne are the highest rated hubs.",
            "source": "web",
        }
    ]

    with patch("app.agents.trip_planner.planning.nodes.tool_node.search_web", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = mock_search_results
        updates = await tool_node(state)

    assert updates["_tool_call_pending"] is False
    assert len(updates["research_results"]) == 1
    assert updates["research_results"][0]["title"] == "Top Swiss Destinations"


@pytest.mark.asyncio
async def test_research_destination_populates_candidates_from_evidence():
    """Verify research_destination analyzes search results and populates candidates without assistant_response."""
    state: PlanningState = {
        "destination": "Japan",
        "duration_days": 10,
        "origin": "Delhi",
        "trip_type": "multi_city",
        "planning_notes": "10-day trip.",
        "research_queries": ["Japan top travel hubs"],
        "research_results": [
            {
                "title": "Top Cities in Japan",
                "url": "https://example.com/japan",
                "content": "Tokyo and Kyoto form the golden route of Japan travel.",
                "source": "web",
            }
        ],
        "candidates": [],
        "_tool_call_pending": False,
    }

    mock_llm_json = json.dumps({
        "needs_search": False,
        "search_queries": [],
        "candidates": [
            {
                "name": "Tokyo",
                "type": "city",
                "reason": "Vibrant metropolis with historic shrines and modern entertainment.",
            },
            {
                "name": "Kyoto",
                "type": "city",
                "reason": "Cultural heart of Japan with traditional temples and bamboo groves.",
            },
        ],
    })

    with patch("app.services.llm.service.llm_service.generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_llm_json
        updates = await research_destination(state)

    assert updates["_tool_call_pending"] is False
    assert len(updates["candidates"]) == 2
    assert updates["candidates"][0]["name"] == "Tokyo"
    assert updates["candidates"][1]["name"] == "Kyoto"
    assert "assistant_response" not in updates


# ==============================================================================
# 3. Routing Unit Tests
# ==============================================================================

def test_route_after_research_to_tool_node():
    """Verify route_after_research routes to tool_node when tool call is pending."""
    state: PlanningState = {
        "destination": "Japan",
        "duration_days": 10,
        "origin": "Delhi",
        "research_queries": ["Japan travel guide"],
        "research_results": [],
        "candidates": [],
        "_tool_call_pending": True,
    }
    assert route_after_research(state) == "tool_node"


def test_route_after_research_to_end():
    """Verify route_after_research routes to END when no tool call is pending."""
    state: PlanningState = {
        "destination": "Japan",
        "duration_days": 10,
        "origin": "Delhi",
        "research_queries": [],
        "research_results": [],
        "candidates": [{"name": "Tokyo", "type": "city", "reason": "Capital"}],
        "_tool_call_pending": False,
    }
    assert route_after_research(state) == "__end__"


# ==============================================================================
# 4. Planning Subgraph End-to-End Tests (Test 1 from instructions)
# ==============================================================================

@pytest.mark.asyncio
async def test_planning_subgraph_no_tool_direct_flow():
    """Verify planning subgraph produces planning data only and NO assistant_response."""
    initial_state = create_initial_planning_state(
        destination="Rome",
        duration_days=4,
        origin="London",
    )

    understand_json = json.dumps({
        "trip_type": "single_city",
        "planning_notes": "4 days in Rome focuses on ancient ruins and Vatican highlights.",
    })
    research_json = json.dumps({
        "needs_search": False,
        "search_queries": [],
        "candidates": [
            {
                "name": "Rome",
                "type": "city",
                "reason": "Historic capital packed with iconic monuments and culinary delights.",
            }
        ],
    })

    with patch("app.services.llm.service.llm_service.generate", side_effect=[understand_json, research_json]):
        final_state = await planning_graph.ainvoke(initial_state)

    assert final_state["trip_type"] == "single_city"
    assert "4 days in Rome" in final_state["planning_notes"]
    assert len(final_state["candidates"]) == 1
    assert final_state["candidates"][0]["name"] == "Rome"
    assert "assistant_response" not in final_state


@pytest.mark.asyncio
async def test_planning_subgraph_with_tool_loop():
    """Verify planning subgraph executes full tool loop and produces structured planning data."""
    initial_state = create_initial_planning_state(
        destination="Japan",
        duration_days=10,
        origin="Delhi",
    )

    understand_json = json.dumps({
        "trip_type": "multi_city",
        "planning_notes": "10-day trip.",
    })
    # Step 1 of research: requests tool search
    research_step1_json = json.dumps({
        "needs_search": True,
        "search_queries": ["best 10 day Japan itinerary destinations"],
        "candidates": [],
    })
    # Step 2 of research (after tool node): analyzes results and outputs candidates
    research_step2_json = json.dumps({
        "needs_search": False,
        "search_queries": [],
        "candidates": [
            {"name": "Tokyo", "type": "city", "reason": "Capital"},
            {"name": "Kyoto", "type": "city", "reason": "Culture"},
            {"name": "Osaka", "type": "city", "reason": "Food"},
        ],
    })

    mock_search_results = [
        {
            "title": "Japan Golden Route",
            "url": "https://example.com/japan-golden-route",
            "content": "Tokyo, Kyoto, and Osaka form the premier multi-city route.",
            "source": "web",
        }
    ]

    with (
        patch("app.services.llm.service.llm_service.generate", side_effect=[understand_json, research_step1_json, research_step2_json]),
        patch("app.agents.trip_planner.planning.nodes.tool_node.search_web", new_callable=AsyncMock, return_value=mock_search_results),
    ):
        final_state = await planning_graph.ainvoke(initial_state)

    assert final_state["trip_type"] == "multi_city"
    assert len(final_state["research_results"]) == 1
    assert len(final_state["candidates"]) == 3
    assert final_state["candidates"][0]["name"] == "Tokyo"
    assert "assistant_response" not in final_state


# ==============================================================================
# 5. Parent Graph Adapter Tests (Section 26 Tests)
# ==============================================================================

@pytest.mark.asyncio
async def test_parent_planning_trip_node_adapter_mapping_and_plan_generation():
    """Verify planning_trip adapter maps subgraph planning data and generates initial plan from research."""
    parent_state = {
        "trip_id": "test-parent-1",
        "user_id": "user-uuid-123",
        "guest_id": "guest-uuid-456",
        "user_name": "Rohit",
        "user_message": "Ready to plan",
        "destination": "Japan",
        "duration_days": 10,
        "origin": "Kolkata",
        "planning_preferences": {"pace": "relaxed", "interests": ["Food"], "avoid": ["Early starts"]},
        "onboarding_complete": True,
        "missing_fields": [],
        "assistant_response": "",
    }

    mock_initial_plan = (
        "Here is your 10-day Japan itinerary from Kolkata! "
        "Days 1-4: Tokyo (Metropolis) | Days 5-7: Kyoto (Culture) | Days 8-10: Osaka. "
        "Let's refine the activities together!"
    )
    mock_planning_output = {
        "destination": "Japan",
        "duration_days": 10,
        "origin": "Kolkata",
        "trip_type": "multi_city",
        "planning_notes": "10-day journey with 2-3 hubs.",
        "research_queries": ["Japan top travel hubs"],
        "research_results": [{"title": "Japan Hubs", "content": "Tokyo, Kyoto"}],
        "candidates": [
            {"name": "Tokyo", "type": "city", "reason": "Metropolis"},
            {"name": "Kyoto", "type": "city", "reason": "Culture"},
        ],
        "_tool_call_pending": False,
    }

    with (
        patch("app.services.llm.service.llm_service.generate", new_callable=AsyncMock) as mock_llm_generate,
        patch("app.agents.trip_planner.nodes.planning_trip_node.planning_graph.ainvoke", new_callable=AsyncMock) as mock_invoke,
    ):
        mock_llm_generate.return_value = mock_initial_plan
        mock_invoke.return_value = mock_planning_output

        result = await planning_trip(parent_state)

        # 1. Verify LLM plan generation prompt was called
        mock_llm_generate.assert_awaited_once()
        call_kwargs = mock_llm_generate.call_args[1]
        assert "Tokyo" in call_kwargs["prompt"]
        assert "Kyoto" in call_kwargs["prompt"]
        assert "Early starts" in call_kwargs["prompt"]

        # 2. Verify planning_graph.ainvoke was called exactly once with clean state boundary
        mock_invoke.assert_awaited_once()
        invoked_state = mock_invoke.call_args[0][0]
        assert invoked_state["destination"] == "Japan"
        assert invoked_state["duration_days"] == 10
        assert invoked_state["origin"] == "Kolkata"
        assert invoked_state["planning_preferences"]["pace"] == "relaxed"
        assert "user_id" not in invoked_state
        assert "guest_id" not in invoked_state
        assert "user_message" not in invoked_state
        assert "missing_fields" not in invoked_state
        assert "onboarding_complete" not in invoked_state

        # 3. Verify assistant_response is the generated initial plan
        assert result["assistant_response"] == mock_initial_plan

        # 4. Verify all planning data fields are mapped back to parent state
        assert result["planning_notes"] == "10-day journey with 2-3 hubs."
        assert result["research_queries"] == ["Japan top travel hubs"]
        assert len(result["research_results"]) == 1
        assert len(result["candidates"]) == 2
        assert result["candidates"][0]["name"] == "Tokyo"


@pytest.mark.asyncio
async def test_parent_planning_trip_node_required_fields():
    """Verify missing required fields do not silently default to fake values."""
    invalid_state = {
        "trip_id": "test-invalid-req",
        "destination": None,
        "duration_days": None,
        "origin": None,
    }

    with pytest.raises((KeyError, ValueError)):
        await planning_trip(invalid_state)


@pytest.mark.asyncio
async def test_parent_planning_trip_node_subgraph_failure_visibility():
    """Verify planning subgraph failure is visible during development and not silently masked."""
    parent_state = {
        "trip_id": "test-parent-fail",
        "user_id": None,
        "guest_id": "guest-uuid",
        "user_name": "Rohit",
        "user_message": "Ready to plan",
        "destination": "Japan",
        "duration_days": 10,
        "origin": "Kolkata",
        "onboarding_complete": True,
        "missing_fields": [],
        "assistant_response": "",
    }

    with patch("app.agents.trip_planner.nodes.planning_trip_node.planning_graph.ainvoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.side_effect = RuntimeError("Planning subgraph failed during execution")

        with pytest.raises(RuntimeError, match="Planning subgraph failed"):
            await planning_trip(parent_state)


@pytest.mark.asyncio
async def test_planning_trip_empty_plan_generation_raises_error():
    """Verify empty plan generation output raises explicit error instead of returning blank."""
    parent_state = {
        "destination": "Japan",
        "duration_days": 10,
        "origin": "Kolkata",
        "user_name": "Rohit",
    }
    mock_planning_output = {
        "destination": "Japan",
        "duration_days": 10,
        "origin": "Kolkata",
        "candidates": [{"name": "Tokyo", "type": "city", "reason": "Capital"}],
    }
    with (
        patch("app.agents.trip_planner.nodes.planning_trip_node.planning_graph.ainvoke", new_callable=AsyncMock, return_value=mock_planning_output),
        patch("app.services.llm.service.llm_service.generate", new_callable=AsyncMock, return_value="   "),
    ):
        with pytest.raises(RuntimeError, match="Initial plan generation returned an empty response"):
            await planning_trip(parent_state)


# ==============================================================================
# 6. Web Search Tool Unit Tests (Tavily & Fallback)
# ==============================================================================

@pytest.mark.asyncio
async def test_search_web_tavily_success():
    """Verify search_web queries Tavily endpoint and formats structured results."""
    mock_tavily_response = {
        "results": [
            {
                "title": "Tokyo City Guide",
                "url": "https://example.com/tokyo",
                "content": "Tokyo offers vibrant nightlife, historic shrines, and world-class dining.",
                "score": 0.98,
            }
        ]
    }

    class MockHttpxResponse:
        status_code = 200
        def json(self):
            return mock_tavily_response

    with (
        patch("app.agents.trip_planner.planning.tools.web_search.settings.TAVILY_API_KEY", "test-key"),
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post,
    ):
        mock_post.return_value = MockHttpxResponse()
        results = await search_web("Tokyo travel guide", max_results=3)

    assert len(results) == 1
    assert results[0]["title"] == "Tokyo City Guide"
    assert results[0]["source"] == "tavily"
    assert "https://example.com/tokyo" in results[0]["url"]


@pytest.mark.asyncio
async def test_search_web_empty_query():
    """Verify search_web returns an empty list for empty query."""
    results = await search_web("   ")
    assert results == []


# ==============================================================================
# 7. Additional Focused Boundary & Smoke Tests (Diagnose & Fix Verification)
# ==============================================================================

@pytest.mark.asyncio
async def test_planning_trip_exact_representative_mock_boundary():
    """
    Focused boundary test verifying planning_trip maps representative PlanningState
    back to parent state without altering planning result data.
    """
    parent_state = {
        "destination": "Paris",
        "duration_days": 10,
        "origin": "Kolkata, India",
        "user_name": "Rohit",
        "user_message": "Let's plan Paris",
        "onboarding_complete": True,
    }

    mock_planning_data = {
        "destination": "Paris",
        "duration_days": 10,
        "origin": "Kolkata, India",
        "trip_type": "single_city",
        "planning_notes": "10-day trip in Paris and surrounding Île-de-France.",
        "research_queries": ["best places to visit in Paris"],
        "research_results": [
            {"title": "Paris Guide", "content": "Eiffel Tower and Louvre are iconic."}
        ],
        "candidates": [
            {
                "name": "Eiffel Tower",
                "type": "landmark",
                "reason": "Iconic Paris attraction",
            }
        ],
        "_tool_call_pending": False,
    }
    mock_plan = "Here is your 10-day Paris itinerary featuring the Eiffel Tower!"

    with (
        patch("app.services.llm.service.llm_service.generate", new_callable=AsyncMock) as mock_trans,
        patch("app.agents.trip_planner.nodes.planning_trip_node.planning_graph.ainvoke", new_callable=AsyncMock) as mock_inv,
    ):
        mock_trans.return_value = mock_plan
        mock_inv.return_value = mock_planning_data

        result = await planning_trip(parent_state)

    assert result["assistant_response"] == mock_plan
    assert result["planning_notes"] == "10-day trip in Paris and surrounding Île-de-France."
    assert result["research_queries"] == ["best places to visit in Paris"]
    assert len(result["research_results"]) == 1
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["name"] == "Eiffel Tower"


@pytest.mark.asyncio
async def test_shared_planning_execution_invokes_subgraph_once():
    """Verify execute_planning_subgraph invokes planning_graph.ainvoke exactly once with clean state."""
    from app.agents.trip_planner.nodes.planning_trip_node import execute_planning_subgraph

    mock_result = {
        "destination": "Kyoto",
        "duration_days": 5,
        "origin": "Tokyo",
        "candidates": [{"name": "Arashiyama", "type": "hub", "reason": "Bamboo forest"}],
    }

    with patch("app.agents.trip_planner.nodes.planning_trip_node.planning_graph.ainvoke", new_callable=AsyncMock) as mock_inv:
        mock_inv.return_value = mock_result
        res = await execute_planning_subgraph("Kyoto", 5, "Tokyo")

    mock_inv.assert_awaited_once()
    inv_input = mock_inv.call_args[0][0]
    assert inv_input["destination"] == "Kyoto"
    assert inv_input["duration_days"] == 5
    assert inv_input["origin"] == "Tokyo"
    assert res == mock_result


def test_build_plan_generation_prompt_incorporates_research_and_candidates():
    """Verify build_plan_generation_prompt structures planning context with research findings."""
    from app.agents.trip_planner.nodes.planning_trip_node import build_plan_generation_prompt

    planning_data = {
        "trip_type": "multi_city",
        "planning_notes": "Balanced pace across Swiss cantons.",
        "research_queries": ["Switzerland 12 days"],
        "research_results": [{"title": "Swiss Rail", "content": "Fast scenic trains"}],
        "candidates": [{"name": "Zermatt", "type": "city", "reason": "Matterhorn view"}],
    }

    prompt = build_plan_generation_prompt(
        planning_result=planning_data,
        destination="Switzerland",
        duration_days=12,
        origin="Kolkata",
        user_name="Rohit",
    )

    assert "Switzerland" in prompt
    assert "12 days" in prompt
    assert "Kolkata" in prompt
    assert "Rohit" in prompt
    assert "Zermatt" in prompt
    assert "Matterhorn view" in prompt
    assert "Swiss Rail" in prompt



