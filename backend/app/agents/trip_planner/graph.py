from langgraph.graph import END, START, StateGraph

from app.agents.trip_planner.copilot.graph import START_ACTION as BUILD_ACTION, build_with_agent

from app.agents.trip_planner.nodes.planning_trip_node import planning_trip
from app.agents.trip_planner.nodes.extract_itinerary_node import extract_itinerary
from app.agents.trip_planner.nodes.planning_choice_node import (
    GENERATE_ACTION, begin_full_itinerary, show_planning_choice,
)
from app.agents.trip_planner.nodes.onboarding_form_node import (
    SUBMIT_ACTION,
    apply_onboarding_form,
    show_onboarding_form,
)
from app.agents.trip_planner.nodes.understand_user_msg_node import (
    understand_user_message,
)
from app.agents.trip_planner.nodes.respond_to_user_node import respond_to_user
from app.agents.trip_planner.nodes.validate_state_node import validate_state
from app.agents.trip_planner.routing import route_after_validation
from app.agents.trip_planner.state import TripPlanningState

def build_trip_planner_graph():
    """
    Build and compile the Pathfinder trip planning graph.

    Graph flow:

        START → show_onboarding_form → END
              → apply_onboarding_form → validate_state → show_planning_choice → END
              → planning_trip → extract_itinerary → END (full itinerary action)
              → understand_user_message → validate_state → planning_trip → extract_itinerary → END
              → build_with_agent → END (day-by-day co-planning; owns every turn once started)

    Validation returns incomplete trips to show_onboarding_form.
    """

    graph = StateGraph(TripPlanningState)
    graph.add_node("greet", lambda state: {
        # By first name when signed in. The time of day is the client's to say: only it knows the traveler's clock.
        "assistant_response": f"Hi{' ' + state['user_name'].split()[0] if (state.get('user_name') or '').strip() else ''}, "
                              "I'm Pathfinder. Tell me where you're thinking of going, or what kind of trip you want. "
                              "We can talk it through first; when you're ready for a draft, I'll ask three quick things "
                              "(where from, where to, how many days) and the rest is optional.",
        "ui_action": None,
    })
    graph.add_node("respond_before_form", respond_to_user)
    graph.add_node("show_onboarding_form", show_onboarding_form)
    graph.add_node("apply_onboarding_form", apply_onboarding_form)
    graph.add_node("show_planning_choice", show_planning_choice)
    graph.add_node("begin_full_itinerary", begin_full_itinerary)
    graph.add_node("build_with_agent", build_with_agent)

    # --------------------------------------------------
    # Register nodes
    # --------------------------------------------------

    graph.add_node(
        "understand_user_message",
        understand_user_message,
    )

    graph.add_node(
        "validate_state",
        validate_state,
    )

    graph.add_node(
        "planning_trip",
        planning_trip,
    )
    graph.add_node("extract_itinerary", extract_itinerary)

    # --------------------------------------------------
    # Normal edges
    # --------------------------------------------------

    graph.add_conditional_edges(
        START,
        lambda state: (
            "submit" if (state.get("ui_action") or {}).get("action") == SUBMIT_ACTION
            else "copilot" if state.get("copilot") or (
                state.get("onboarding_complete") and (state.get("ui_action") or {}).get("action") == BUILD_ACTION)
            else "generate" if state.get("onboarding_complete") and (state.get("ui_action") or {}).get("action") == GENERATE_ACTION
            else "conversation" if state.get("onboarding_complete") and state.get("planning_started")
            else "choice" if state.get("onboarding_complete")
            else "preform" if (state.get("user_message") or "").strip()
            else "greet"
        ),
        {"submit": "apply_onboarding_form", "generate": "begin_full_itinerary", "copilot": "build_with_agent",
         "conversation": "understand_user_message", "choice": "show_planning_choice",
         "preform": "understand_user_message", "greet": "greet"},
    )
    graph.add_edge("apply_onboarding_form", "validate_state")
    graph.add_edge("begin_full_itinerary", "planning_trip")

    graph.add_conditional_edges(
        "understand_user_message",
        lambda state: "form" if not state.get("onboarding_complete")
        and state.get("intent") in {"trip_information", "update_trip", "planning_request"}
        else "chat" if not state.get("onboarding_complete") else "validate",
        {"form": "show_onboarding_form", "chat": "respond_before_form", "validate": "validate_state"},
    )

    # --------------------------------------------------
    # Conditional edge
    # --------------------------------------------------

    graph.add_conditional_edges(
        "validate_state",
        route_after_validation,
        {
            "incomplete": "show_onboarding_form",
            "complete": "planning_trip",
            "choice": "show_planning_choice",
        },
    )

    # --------------------------------------------------
    # Terminal edges
    # --------------------------------------------------

    graph.add_edge("show_onboarding_form", END)
    graph.add_edge("greet", END)
    graph.add_edge("build_with_agent", END)
    graph.add_edge("respond_before_form", END)
    graph.add_edge("show_planning_choice", END)

    graph.add_edge("planning_trip", "extract_itinerary")
    graph.add_edge("extract_itinerary", END)

    # --------------------------------------------------
    # Compile
    # --------------------------------------------------

    return graph.compile()


trip_planner_graph = build_trip_planner_graph()
