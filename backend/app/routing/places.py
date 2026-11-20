"""A request for restaurants or sights gets named places, not a silent turn."""

from __future__ import annotations

import re

_CHANGE = re.compile(r"\b(change|replace|swap|move|remove|delete|book)\b", re.I)
_ASK = re.compile(
    r"\b(suggest|recommend|ideas|where (?:should|can) (?:i|we)|a list of|some|any good|bunch)\b"
    r"|推薦|推荐|建議|建议|一堆|一些|俾我|给我",
    re.I,
)
_FOOD = re.compile(r"\b(restaurants?|cafes?|food|eat|lunch|dinner)\b|餐廳|餐馆|餐厅|美食", re.I)
_SIGHT = re.compile(r"\b(sights?|attractions?|places to visit|things to do)\b|景點|景点|景點", re.I)


def suggestion_kind(message: str) -> str | None:
    """restaurant or sight when the traveller is asking for names. An edit stays with the planner."""
    text = message or ""
    if _CHANGE.search(text) or not _ASK.search(text):
        return None
    if _FOOD.search(text):
        return "restaurant"
    if _SIGHT.search(text):
        return "sight"
    return None


def suggestion_reply(destination: str, kind: str, places: list[dict]) -> str:
    label = "restaurants" if kind == "restaurant" else "places"
    if not destination:
        return f"Tell me the city and I'll look up specific {label} there."
    if not places:
        return (
            f"I looked for named {label} in {destination} and nothing came back just now. "
            "Name a neighbourhood and I'll look there."
        )
    source = "Google Places" if places[0].get("source") == "google" else "OpenStreetMap"
    lines = [f"Here are specific {label} in {destination}, from {source}:"]
    for place in places:
        where = ", ".join(part.strip() for part in str(place.get("label") or "").split(",")[:2] if part.strip())
        rating = place.get("rating")
        extra = []
        if where and where.casefold() != str(place.get("name") or "").casefold():
            extra.append(where)
        if isinstance(rating, (int, float)):
            extra.append(f"{rating:.1f}")
        lines.append(f"- {place['name']}" + (f" — {', '.join(extra)}" if extra else ""))
    lines.append("Tell me which ones to put on the plan, and which day.")
    return "\n".join(lines)


def named_place_instruction(sights: list[str] | None, restaurants: list[str] | None) -> str:
    sights = [name for name in sights or [] if name]
    restaurants = [name for name in restaurants or [] if name]
    if not sights and not restaurants:
        return ""
    lines = [
        "Use these specific places. Do not write a generic temple, market, or local restaurant.",
    ]
    if sights:
        lines.append("Sights: " + "; ".join(sights) + ".")
    if restaurants:
        lines.append("Restaurants: " + "; ".join(restaurants) + ".")
    return "\n".join(lines) + "\n"
