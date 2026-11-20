import json
import logging
import re
from typing import Any

from app.agents.trip_planner.planning.state import PlanningState
from app.services.llm.service import llm_service

logger = logging.getLogger(__name__)

UNDERSTAND_TRIP_SYSTEM_INSTRUCTION = """
You are Pathfinder's high-level trip analysis engine.

Your job is to understand the basic trip parameters and establish a high-level
understanding of the voyage structure before destination research begins.

You are given:
- Destination (e.g. "Japan", "Paris", "Switzerland")
- Trip Duration (in days, e.g. 10)
- Departure Origin (e.g. "Delhi", "London", or null)

Based on these parameters, determine:
1. "trip_type": A high-level categorization of the journey. Common types:
   - "single_city" (e.g., 3-4 days in a single city like Paris or Tokyo)
   - "multi_city" (e.g., 7-14 days exploring several key hubs in a country)
   - "regional_tour" (e.g., 10+ days across a broader geographic region)
   - "getaway" (e.g., 1-2 days weekend trip)
   - "extended_exploration" (e.g., 15+ days)

2. "planning_notes": A concise 1-2 sentence strategic observation regarding how
   the duration, destination, and origin influence candidate selection and pace.

Output format:
Return ONLY a valid JSON object with the following structure:
{
  "trip_type": "multi_city",
  "planning_notes": "A 10-day trip to Japan allows for 2-3 major hubs with smooth high-speed transit connections."
}

Do NOT generate a detailed itinerary.
Do NOT search for places or activities yet.
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


async def understand_trip(state: PlanningState) -> dict[str, Any]:
    """
    Analyze basic trip context to infer trip_type and high-level planning notes.
    """
    logger.info("🔥 PLANNING NODE: understand_trip")
    destination = state.get("destination", "Unknown")
    duration_days = state.get("duration_days", 1)
    origin = state.get("origin")
    places = state.get("places_to_visit") or []
    preferences = state.get("planning_preferences") or {}
    origin_text = f"Origin: {origin}\n" if origin else "Origin: Not specified\n"

    prompt = f"""
TRIP CONTEXT:
Destination: {destination}
Duration: {duration_days} days
{origin_text}
Traveler's requested places: {", ".join(places) or "none specified"}
Travel pace: {preferences.get("pace", "balanced")}
Traveler interests: {", ".join(preferences.get("interests") or []) or "not specified"}
Traveler wants to avoid: {", ".join(preferences.get("avoid") or []) or "not specified"}
Analyze this trip context and return the structured JSON object with "trip_type" and "planning_notes".
"""

    trip_type = None
    planning_notes = None

    try:
        raw_response = await llm_service.generate(
            prompt=prompt,
            system_instruction=UNDERSTAND_TRIP_SYSTEM_INSTRUCTION,
            temperature=0.2,
        )
        parsed = _extract_json(raw_response)
        if parsed and isinstance(parsed, dict):
            trip_type = parsed.get("trip_type")
            planning_notes = parsed.get("planning_notes")
    except Exception as exc:
        logger.warning("Understand-trip LLM call failed: %s", exc)

    # Deterministic fallback if LLM returned nothing or failed
    if not trip_type:
        if duration_days <= 3:
            trip_type = "single_city"
        elif duration_days <= 10:
            trip_type = "multi_city"
        else:
            trip_type = "regional_tour"

    if not planning_notes:
        origin_clause = f" from {origin}" if origin else ""
        planning_notes = (
            f"A {duration_days}-day trip to {destination}{origin_clause} is well-suited for a "
            f"{trip_type.replace('_', ' ')} itinerary balancing travel pace and key highlights."
        )

    return {
        "trip_type": trip_type,
        "planning_notes": planning_notes,
    }
