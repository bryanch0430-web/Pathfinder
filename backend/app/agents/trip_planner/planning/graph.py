from langgraph.graph import END, START, StateGraph

from app.agents.trip_planner.planning.nodes.research_destination import (
    research_destination,
)
from app.agents.trip_planner.planning.nodes.tool_node import tool_node
from app.agents.trip_planner.planning.nodes.understand_trip import understand_trip
from app.agents.trip_planner.planning.routing import route_after_research
from app.agents.trip_planner.planning.state import PlanningState


def build_planning_graph():
    """
    Build and compile the Pathfinder destination research and planning subgraph.

    Graph flow:
        START
          ↓
        understand_trip
          ↓
        research_destination
          │
          ├── needs tool → tool_node
          │                  │
          │                  └────→ research_destination
          │
          └── no tool ───────────→ END
    """
    graph = StateGraph(PlanningState)

    # 1. Register nodes
    graph.add_node("understand_trip", understand_trip)
    graph.add_node("research_destination", research_destination)
    graph.add_node("tool_node", tool_node)

    # 2. Sequential edges
    graph.add_edge(START, "understand_trip")
    graph.add_edge("understand_trip", "research_destination")

    # 3. Conditional routing from research_destination
    graph.add_conditional_edges(
        "research_destination",
        route_after_research,
        {
            "tool_node": "tool_node",
            END: END,
        },
    )

    # 4. Tool node loops back to research_destination
    graph.add_edge("tool_node", "research_destination")

    return graph.compile()


planning_graph = build_planning_graph()