from app.agents.trip_planner.state import TripPlanningState


def route_after_validation(state: TripPlanningState) -> str:
    """
    Determine the next node after validating the current trip state.

    Returns:
        "complete"   -> required onboarding information is available.
        "incomplete" -> one or more required fields are missing.
    """

    if state.get("onboarding_complete"):
        return "complete" if state.get("planning_started") else "choice"

    return "incomplete"
