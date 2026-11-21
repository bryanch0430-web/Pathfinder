"""How a chosen guide leans the plan.

The picker copy lives in frontend/src/components/guide/guides.ts. This module is
what the planner reads. The traveler's own pace, interests, avoids, requested
places, budget, comfort, and travel mode still win when they conflict.
"""

from __future__ import annotations

GUIDES: dict[str, dict[str, object]] = {
    "tetra": {
        "name": "Tetra-san",
        "search": "quiet neighborhoods uncrowded efficient route",
        "like": ("quiet", "calm", "local", "park", "garden"),
        "dislike": ("crowded", "touristy"),
        "plan": (
            "Plan a calm, efficient day. Prefer fewer stops and short transfers between them. "
            "Choose quieter venues over the busiest queue. Leave open time instead of filling every hour."
        ),
    },
    "hexa": {
        "name": "Hexa-san",
        "search": "famous landmarks classic sights must see",
        "like": ("museum", "history", "landmark", "touristy", "viewpoint"),
        "dislike": (),
        "plan": (
            "Plan the well-known sights. Prefer named landmarks, museums, and classic viewpoints "
            "that a first-time visitor expects, and say in a few words why each one is famous."
        ),
    },
    "octa": {
        "name": "Octa-san",
        "search": "where to eat best restaurants cafes markets",
        "like": ("food", "cafe", "market", "restaurant", "dining", "street"),
        "dislike": (),
        "plan": (
            "Build the day around meals. Breakfast, lunch, and dinner are named restaurants, cafes, or markets, "
            "not generic meal slots. Sightseeing fits around those tables, in the same neighborhood when possible."
        ),
    },
    "dode": {
        "name": "Dode-san",
        "search": "budget itinerary free things cheap eats",
        "like": ("free", "budget", "local", "market"),
        "dislike": (),
        "plan": (
            "Keep the plan predictable and inside the stated budget and comfort level. "
            "Prefer free or low-cost stops. Do not add a splurge. When a price is not in the research, say it is unknown "
            "instead of guessing."
        ),
    },
    "icosa": {
        "name": "Icosa-san",
        "search": "hidden gems local neighborhoods not touristy",
        "like": ("hidden", "local", "quiet", "neighborhood"),
        "dislike": ("touristy", "crowded"),
        "plan": (
            "Prefer smaller local venues and lesser-known neighborhoods over the most famous postcard stop, "
            "when a real named place is available. Still include a requested famous place. Do not invent a secret spot."
        ),
    },
    "sphere": {
        "name": "Sphere-san",
        "search": "nightlife late dinner evening things to do",
        "like": ("nightlife", "bar", "night", "evening"),
        "dislike": (),
        "plan": (
            "Plan a late day. Do not schedule a stop before 10:00 unless the traveler asked for an early start. "
            "Put dinner and one evening stop after 19:00. Mornings stay light."
        ),
    },
}

_GUARD = (
    "The traveler's stated pace, interests, avoid list, requested places, budget, comfort, "
    "and travel mode win when they conflict with this leaning."
)


def guide_row(preferences: dict | None) -> dict | None:
    prefs = preferences or {}
    if prefs.get("guide_shapes_plan") is False:
        return None
    guide_id = str(prefs.get("guide") or "").strip().lower()
    row = GUIDES.get(guide_id)
    return row if isinstance(row, dict) else None


def guide_leaning(preferences: dict | None) -> str:
    """A prompt block, or empty when no known guide was chosen."""
    row = guide_row(preferences)
    if row is None:
        return ""
    return f"GUIDE LEANING ({row['name']}): {row['plan']} {_GUARD}\n"


def search_hint(guide_id: str | None) -> str:
    row = GUIDES.get(str(guide_id or "").strip().lower())
    if not isinstance(row, dict):
        return ""
    return str(row["search"])


def score_terms(guide_id: str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    row = GUIDES.get(str(guide_id or "").strip().lower())
    if not isinstance(row, dict):
        return (), ()
    return tuple(row["like"]), tuple(row["dislike"])
