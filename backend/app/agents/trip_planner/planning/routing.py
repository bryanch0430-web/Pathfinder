import logging
from langgraph.graph import END

from app.agents.trip_planner.planning.state import PlanningState

logger = logging.getLogger(__name__)


def route_after_research(state: PlanningState) -> str:
    """
    Conditionally route after research_destination.

    Returns:
        - "tool_node": If the LLM requested an external search tool call.
        - END: If candidate destination research is complete.
    """
    if state.get("_tool_call_pending") is True and len(state.get("research_queries", [])) > 0:
        route = "tool_node"
    else:
        route = END

    logger.info("🔥 PLANNING ROUTE: %s", route)
    return route
