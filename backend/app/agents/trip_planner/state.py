from typing import Any, TypedDict


class TripPlanningState(TypedDict, total=False):
    """LangGraph state schema for conversational trip planning and onboarding."""

    trip_id: str

    user_id: str | None
    guest_id: str | None
    user_name: str | None

    user_message: str

    destination: str | None
    places_to_visit: list[str]
    planning_preferences: dict[str, Any]
    duration_days: int | None
    origin: str | None
    origin_latitude: float | None
    origin_longitude: float | None

    intent: str | None
    onboarding_complete: bool
    planning_started: bool
    missing_fields: list[str]

    assistant_response: str
    ui_action: dict[str, Any] | None
    tool_calls: list[dict[str, Any]] | None

    # Planning output fields populated by planning subgraph
    planning_notes: str | None
    research_queries: list[str] | None
    research_results: list[dict[str, Any]] | None
    candidates: list[dict[str, Any]] | None
    itinerary_graph: dict[str, Any] | None
    day_plan: list[dict[str, Any]] | None

    # Build-with-agent mode (see copilot/engine.py for the shape)
    copilot: dict[str, Any] | None
    currency: str | None
    budget_target: float | None
    focus_day: int | None
    budget_amount: float | None  # a budget stated in chat, with its currency when clear
    budget_currency: str | None
    recent: list[dict[str, Any]] | None  # the last exchange, for resolving "it" and "yes"
    known_places: list[str] | None  # a revision's previous day-plan names, reused by the extractor
    changes: list[dict[str, Any]] | None  # this turn's receipt: what actually changed in the plan
