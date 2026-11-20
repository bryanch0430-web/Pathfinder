import logging
from typing import Any

from app.agents.trip_planner.planning.state import PlanningState
from app.agents.trip_planner.planning.tools.web_search import search_web

logger = logging.getLogger(__name__)


async def tool_node(state: PlanningState) -> dict[str, Any]:
    """
    Execute requested search tools for destination research and update research_results.
    """
    logger.info("🔥 PLANNING NODE: tool_node")
    queries = state.get("research_queries", [])
    logger.info("🔥 Executing search queries: %s", queries)
    existing_results = list(state.get("research_results", []))

    # Keep track of queries already represented in results to avoid duplication
    searched_urls = {r.get("url") for r in existing_results if r.get("url")}
    new_results = []

    for query in queries:
        try:
            results = await search_web(query)
            for res in results:
                if res.get("url") not in searched_urls:
                    new_results.append(res)
                    if res.get("url"):
                        searched_urls.add(res["url"])
        except Exception as exc:
            logger.warning("Tool execution error searching for '%s': %s", query, exc)

    return {
        "research_results": existing_results + new_results,
        "_tool_call_pending": False,
    }
