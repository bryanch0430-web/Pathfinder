from typing import Any, TypedDict


class PlanningState(TypedDict, total=False):
    """LangGraph state schema for the destination research and candidate planning subgraph."""

    destination: str
    duration_days: int
    origin: str | None
    places_to_visit: list[str]
    planning_preferences: dict[str, Any]

    trip_type: str | None
    planning_notes: str | None

    research_queries: list[str]
    research_results: list[dict[str, Any]]

    candidates: list[dict[str, Any]]

    # Internal routing flag for tool-call execution loop
    _tool_call_pending: bool | None


def create_initial_planning_state(
    destination: str,
    duration_days: int,
    origin: str | None = None,
    places_to_visit: list[str] | None = None,
    planning_preferences: dict[str, Any] | None = None,
) -> PlanningState:
    """Create a clean, initialized PlanningState from basic trip context."""
    return PlanningState(
        destination=destination,
        duration_days=duration_days,
        origin=origin,
        places_to_visit=places_to_visit or [],
        planning_preferences=planning_preferences or {},
        trip_type=None,
        planning_notes=None,
        research_queries=[],
        research_results=[],
        candidates=[],
        _tool_call_pending=False,
    )

