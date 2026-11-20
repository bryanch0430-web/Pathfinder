from app.agents.trip_planner.state import TripPlanningState
from app.agents.trip_planner.nodes.validate_state_node import validate_state


FORM_ACTION = "SHOW_ONBOARDING_FORM"
SUBMIT_ACTION = "SUBMIT_TRIP_ONBOARDING"
NEEDED = {"destination": "where you're going", "duration_days": "how many days",
          "origin": "where you're travelling from"}


def show_onboarding_form(state: TripPlanningState) -> dict:
    """Ask the client to render the onboarding form without calling an LLM."""
    known = []
    if state.get("destination"):
        known.append(str(state["destination"]))
    if state.get("duration_days"):
        known.append(f'{state["duration_days"]} days')
    summary = " for ".join(known) if known else "your trip"
    validation = validate_state(state)
    # Ask only for what chat hasn't told us yet; the form takes it one small step at a time.
    needed = [NEEDED[field] for field in validation["missing_fields"]]
    ask = (f"I just need {', '.join(needed[:-1])}{' and ' if needed[:-1] else ''}{needed[-1]}."
           if needed else "Check the basics below.")
    return {
        **validation,
        # The form is the gate: even when chat already supplied every field, the trip
        # stays in onboarding until the traveler submits it (otherwise the client hides it).
        "onboarding_complete": False,
        "assistant_response": f"I can start a draft for {summary}. {ask} Everything after that is optional, so skip it "
                              "whenever you like. You'll see the brief before I plan anything.",
        "ui_action": {
            "action": FORM_ACTION,
            "fields": ["origin", "destination", "places_to_visit", "duration_days", "planning_preferences"],
            "values": {
                "origin": state.get("origin") or "",
                "destination": state.get("destination") or "",
                "places_to_visit": state.get("places_to_visit") or [],
                "duration_days": state.get("duration_days"),
                "planning_preferences": state.get("planning_preferences") or {},
            },
        },
    }


def apply_onboarding_form(state: TripPlanningState) -> dict:
    """Copy a validated form submission into graph state."""
    action = state.get("ui_action") or {}
    if action.get("action") != SUBMIT_ACTION:
        return {}
    return {
        "origin": action["origin"],
        "destination": action["destination"],
        "places_to_visit": action["places_to_visit"],
        "planning_preferences": action.get("planning_preferences") or {},
        "duration_days": action["duration_days"],
        "ui_action": None,
    }
