import json
import logging
import re
from typing import Any

from app.agents.trip_planner.planning.state import PlanningState
from app.services.llm.service import llm_service

logger = logging.getLogger(__name__)

RESEARCH_DESTINATION_SYSTEM_INSTRUCTION = """
You are Pathfinder's destination research and candidate curation engine.

Your role is to research the destination using search evidence when helpful,
analyze the findings, and curate the top travel candidate destinations (e.g. cities, regions, hubs).

Trip Context includes:
- Destination (e.g. "Japan", "Switzerland", "Italy")
- Duration in days (e.g. 10)
- Departure Origin (e.g. "Delhi", "London", or null)
- Trip Type (e.g. "multi_city", "single_city")
- Planning Notes
- Available Research Evidence (from web searches)

Instructions:
1. If no research results are present and you need external information to discover the best
   destinations, set "needs_search" to true and provide 1 to 3 focused search queries.
2. If research results are already provided (or if you already have enough information):
   - Set "needs_search" to false.
   - Synthesize the research evidence into 2 to 5 high-value candidate destinations.
   - Each candidate must have:
     * "name": The city or region name (e.g. "Tokyo", "Kyoto", "Osaka")
     * "type": "city", "region", or "hub"
     * "reason": 1-2 sentence evidence-backed justification for why this candidate fits the trip.

Do NOT invent unsupported facts.
Do NOT generate a detailed day-by-day itinerary yet.

Output format (Return ONLY valid JSON):
{
  "needs_search": false,
  "search_queries": [],
  "candidates": [
    {
      "name": "Tokyo",
      "type": "city",
      "reason": "Dynamic capital hub offering historic shrines, modern tech, and vibrant culinary scene."
    },
    {
      "name": "Kyoto",
      "type": "city",
      "reason": "Cultural and historical heart of Japan, ideal for traditional temples and gardens."
    }
  ]
}
"""


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract JSON object from LLM response string."""
    cleaned = text.strip()
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


async def research_destination(state: PlanningState) -> dict[str, Any]:
    """
    Research the destination, evaluate search evidence, and populate candidate travel destinations.
    """
    logger.info("🔥 PLANNING NODE: research_destination")
    destination = state.get("destination", "Unknown")
    duration_days = state.get("duration_days", 1)
    origin = state.get("origin")
    places = state.get("places_to_visit") or []
    preferences = state.get("planning_preferences") or {}
    trip_type = state.get("trip_type", "trip")
    planning_notes = state.get("planning_notes", "")
    existing_queries = state.get("research_queries", [])
    research_results = state.get("research_results", [])
    existing_candidates = state.get("candidates", [])

    # Format research results for prompt
    if research_results:
        evidence_lines = []
        for i, res in enumerate(research_results[:6], 1):
            evidence_lines.append(f"[{i}] {res.get('title', '')}: {res.get('content', '')}")
        research_context = "AVAILABLE RESEARCH EVIDENCE:\n" + "\n".join(evidence_lines)
    else:
        research_context = "AVAILABLE RESEARCH EVIDENCE: None yet."

    origin_clause = f"Origin: {origin}\n" if origin else "Origin: Not specified\n"

    prompt = f"""
TRIP CONTEXT:
Destination: {destination}
Duration: {duration_days} days
{origin_clause}Trip Type: {trip_type}
Traveler's requested places (include where practical): {", ".join(places) or "none specified"}
Travel pace: {preferences.get("pace", "balanced")}
Traveler interests: {", ".join(preferences.get("interests") or []) or "not specified"}
Traveler wants to avoid: {", ".join(preferences.get("avoid") or []) or "not specified"}
Planning Notes: {planning_notes}

{research_context}

Evaluate the trip and available research evidence.
Return the structured JSON object.
"""

    parsed = None
    try:
        raw_response = await llm_service.generate(
            prompt=prompt,
            system_instruction=RESEARCH_DESTINATION_SYSTEM_INSTRUCTION,
            temperature=0.3,
        )
        parsed = _extract_json(raw_response)
    except Exception as exc:
        logger.warning("Research-destination LLM call failed: %s", exc)

    # If already performed search or search results exist, we avoid infinite search loop
    has_searched = len(research_results) > 0 or len(existing_queries) >= 3

    if parsed and isinstance(parsed, dict):
        needs_search = bool(parsed.get("needs_search", False)) and not has_searched
        new_queries = parsed.get("search_queries") or []
        candidates = parsed.get("candidates") or []

        if needs_search and new_queries:
            # Tool call requested
            combined_queries = list(dict.fromkeys(existing_queries + new_queries))
            logger.info("🔥 research_destination requesting tool_node with queries: %s", combined_queries)
            return {
                "research_queries": combined_queries,
                "_tool_call_pending": True,
            }

        if candidates:
            formatted_candidates = []
            for c in candidates:
                if isinstance(c, dict) and c.get("name"):
                    formatted_candidates.append({
                        "name": str(c["name"]).strip(),
                        "type": str(c.get("type", "city")).strip(),
                        "reason": str(c.get("reason", "")).strip(),
                    })

            for place in places:
                if not any(candidate["name"].casefold() == place.casefold() for candidate in formatted_candidates):
                    formatted_candidates.append({
                        "name": place,
                        "type": "city",
                        "reason": "Requested by the traveler; assess whether it fits the route and available days.",
                    })

            logger.info("🔥 research_destination produced %d candidates", len(formatted_candidates))
            return {
                "candidates": formatted_candidates,
                "_tool_call_pending": False,
            }

    # Fallback if no candidates generated by LLM
    if not existing_candidates:
        fallback_candidates = [
            {
                "name": destination,
                "type": "city" if "city" in trip_type else "region",
                "reason": f"Primary destination for your {duration_days}-day expedition.",
            }
        ]
        fallback_candidates.extend(
            {"name": place, "type": "city", "reason": "Requested by the traveler."}
            for place in places if place.casefold() != destination.casefold()
        )
        return {
            "candidates": fallback_candidates,
            "_tool_call_pending": False,
        }

    return {
        "_tool_call_pending": False,
    }

