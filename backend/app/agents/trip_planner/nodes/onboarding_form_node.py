from app.agents.trip_planner.state import TripPlanningState
from app.agents.trip_planner.nodes.planning_trip_node import off_earth_places
from app.agents.trip_planner.nodes.validate_state_node import validate_state


FORM_ACTION = "SHOW_ONBOARDING_FORM"
SUBMIT_ACTION = "SUBMIT_TRIP_ONBOARDING"
NEEDED = {"destination": "where you're going", "duration_days": "how many days",
          "origin": "where you're travelling from"}


def _filled(brief: dict, state: TripPlanningState) -> dict:
    """Chat facts fill only the fields the trip does not already have."""
    prefs = dict(state.get("planning_preferences") or {})
    places = list(state.get("places_to_visit") or [])
    for place in brief.get("places_to_visit") or []:
        if place.casefold() not in {item.casefold() for item in places}:
            places.append(place)
    for key in ("interests", "avoid"):
        current = list(prefs.get(key) or [])
        for item in brief.get(key) or []:
            if item.casefold() not in {kept.casefold() for kept in current}:
                current.append(item)
        if current:
            prefs[key] = current[:6]
    for key in ("adults", "children", "comfort", "pace", "travel_mode"):
        if brief.get(key) is not None and not prefs.get(key):
            prefs[key] = brief[key]
    days = state.get("duration_days") or brief.get("duration_days")
    # "10-day" is a length, not a party of 10.
    if prefs.get("adults") == days and brief.get("adults") is None and brief.get("duration_days") == days:
        prefs.pop("adults", None)
    amount = state.get("budget_amount") or brief.get("budget_amount")
    currency = brief.get("currency") or state.get("budget_currency") or state.get("currency")
    return {
        "origin": state.get("origin") or brief.get("origin") or "",
        "destination": state.get("destination") or brief.get("destination") or "",
        "places_to_visit": places,
        "duration_days": days,
        "planning_preferences": prefs,
        "budget_amount": amount,
        "currency": currency,
    }


def show_onboarding_form(state: TripPlanningState) -> dict:
    """Ask the client to render the onboarding form without calling an LLM."""
    from app.agents.trip_planner.brief import read_brief
    brief = read_brief(state.get("user_message") or "")
    values = _filled(brief, state)
    known = []
    if values.get("destination"):
        known.append(str(values["destination"]))
    if values.get("duration_days"):
        known.append(f'{values["duration_days"]} days')
    summary = " for ".join(known) if known else "your trip"
    validation = validate_state(state)
    # Ask only for what chat hasn't told us yet; the form takes it one small step at a time.
    needed = [NEEDED[field] for field in validation["missing_fields"]]
    ask = (f"I just need {', '.join(needed[:-1])}{' and ' if needed[:-1] else ''}{needed[-1]}."
           if needed else "Check the basics below.")
    places = off_earth_places(state.get("origin"), state.get("destination"), *(state.get("places_to_visit") or []))
    shown = places[0] if len(places) == 1 else (", ".join(places[:-1]) + " and " + places[-1]) if places else ""
    disclaimer = (
        f" {shown} {'is' if len(places) == 1 else 'are'} not on Earth. I'll still draft this trip as a proof of concept, "
        "and any hotels, flights, or weather in it are imagined."
        if places else ""
    )
    return {
        **validation,
        # The form is the gate: even when chat already supplied every field, the trip
        # stays in onboarding until the traveler submits it (otherwise the client hides it).
        "onboarding_complete": False,
        "assistant_response": f"I can start a draft for {summary}.{disclaimer} {ask} Everything after that is optional, so skip it "
                              "whenever you like. You'll see the brief before I plan anything.",
        "ui_action": {
            "action": FORM_ACTION,
            "fields": ["origin", "destination", "places_to_visit", "duration_days", "planning_preferences"],
            "values": values,
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
