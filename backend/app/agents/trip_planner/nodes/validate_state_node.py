from app.agents.trip_planner.state import TripPlanningState


def validate_state(state: TripPlanningState) -> dict:
    """
    Validate the current trip planning state.

    Required onboarding fields:
    - destination
    - duration_days
    - origin (text or geolocation coordinates)

    This node is deterministic and does not use an LLM.

    It only evaluates the current state and returns:
    - missing_fields
    - onboarding_complete
    """

    missing_fields: list[str] = []

    destination = state.get("destination")
    duration_days = state.get("duration_days")
    origin = state.get("origin")
    origin_latitude = state.get("origin_latitude")
    origin_longitude = state.get("origin_longitude")

    # Destination is required.
    if not destination or not str(destination).strip():
        missing_fields.append("destination")

    # Duration is required (must be positive integer).
    if duration_days is None or duration_days <= 0:
        missing_fields.append("duration_days")

    # Origin is required (either text or valid coordinates).
    has_origin_text = bool(origin and str(origin).strip() and str(origin).strip().lower() not in {"none", "null"})
    has_origin_coords = origin_latitude is not None and origin_longitude is not None
    if not (has_origin_text or has_origin_coords):
        missing_fields.append("origin")

    return {
        "missing_fields": missing_fields,
        "onboarding_complete": len(missing_fields) == 0,
    }