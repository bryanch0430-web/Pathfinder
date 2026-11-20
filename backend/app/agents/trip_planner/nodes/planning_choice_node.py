"""Deterministic handoff between journey details and a planning workflow."""

from app.agents.trip_planner.state import TripPlanningState

CHOICE_ACTION = "SHOW_PLANNING_CHOICE"
GENERATE_ACTION = "GENERATE_FULL_ITINERARY"


def show_planning_choice(state: TripPlanningState) -> dict:
    return {
        "assistant_response": "Your journey details are saved. How would you like to build your itinerary?",
        "ui_action": {
            "action": CHOICE_ACTION,
            "options": ["full_itinerary", "build_with_agent"],
        },
    }


def begin_full_itinerary(_: TripPlanningState) -> dict:
    """Consume the UI action so the generated plan owns the assistant payload."""
    return {"ui_action": None}
