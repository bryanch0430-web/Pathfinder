"""The full draft uses the same named places as Add to day.

A title such as "Temples & traditional experiences" cannot be pinned on a map, so it is
replaced with a located listing for that day's city. A flight or a train stays. A real
venue name stays, including one a swap just wrote onto the plan.
"""

from __future__ import annotations

import re

_KEEP = re.compile(r"\b(fly|flight|train|shinkansen|airport|rail|bus|transit|arrival|depart|transfer|travel to)\b", re.I)
_HARD_VAGUE = re.compile(
    r"wander|crawl|stroll|check-?in|overnight|free window|your pick|\be\.g\b|\bor\b",
    re.I,
)
_SOFT_VAGUE = re.compile(r"\b(experience|experiences|afternoon|browsing|packing)\b", re.I)
_FOOD = re.compile(r"\b(breakfast|lunch|dinner|food|eat)\b", re.I)
_STAY = re.compile(r"\b(hotel|stay|overnight|check-?in)\b", re.I)
# Words that name a kind of outing, not a venue. A title made only of these cannot be mapped.
_GENERIC = {
    "a", "an", "the", "and", "or", "of", "in", "to", "for", "with", "on", "at", "your", "you",
    "temple", "temples", "shrine", "shrines", "museum", "museums", "gallery", "galleries",
    "garden", "gardens", "market", "markets", "park", "parks", "art", "traditional", "modern",
    "shopping", "experience", "experiences", "afternoon", "morning", "evening", "lunch", "dinner",
    "breakfast", "food", "hotel", "stay", "area", "district", "culture", "cultural", "activity",
    "activities", "sightseeing", "browse", "browsing", "packing", "rest", "free", "window", "pick",
    "major", "smaller", "final", "second", "another", "local", "street", "night", "day", "some",
    "more", "good", "best", "what", "proposed", "something", "other", "options", "option",
    "japanese", "omakase", "restaurant", "cafe", "bar", "beef", "sushi",
}


def is_vague(name: str) -> bool:
    """True when the title is a theme, not a venue a map can pin."""
    text = (name or "").strip()
    if not text or _KEEP.search(text):
        return False
    if _HARD_VAGUE.search(text):
        return True
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'+.-]*", text)
    proper = [word for word in words if word.casefold() not in _GENERIC and len(word) > 1]
    if not proper:
        return True
    return bool(_SOFT_VAGUE.search(text) and len(proper) <= 1)


def arrival_city(name: str) -> str | None:
    """The city a journey ends in: "Return to Tokyo" -> Tokyo. None for a venue."""
    text = (name or "").strip()
    if not re.search(r"\b(return|back|travel|head|transfer|train|shinkansen|flight|fly|bus|rail)\b", text, re.I):
        return None
    match = re.search(r"\bto\s+([A-Za-z][\w'.-]+(?:\s+[A-Za-z][\w'.-]+)?)\s*$", text, re.I)
    if not match:
        return None
    city = match.group(1).strip()
    return None if city.casefold() in _GENERIC else city


def names_a_vehicle(name: str) -> bool:
    return bool(re.search(r"\b(train|shinkansen|flight|fly|bus|rail|ferry)\b", name or "", re.I))


def plan_cities(days: list[dict]) -> list[str]:
    """Day bases plus any city a journey travels to, so that city has a hotel listing."""
    found = []
    for day in days or []:
        if day.get("base"):
            found.append(str(day["base"]))
        for item in day.get("items") or []:
            city = arrival_city(str(item.get("name") or ""))
            if city:
                found.append(city)
    return found


def _bucket(name: str, category: str) -> str:
    if category in {"food", "nightlife"} or _FOOD.search(name):
        return "food"
    if category == "stay" or _STAY.search(name):
        return "stay"
    return "see"


def _pool(listings: list[dict]) -> dict[str, list[dict]]:
    pools: dict[str, list[dict]] = {"see": [], "food": [], "stay": []}
    for item in listings:
        name = str(item.get("name") or "").strip()
        tab = item.get("tab") or ""
        if not name or tab not in pools:
            continue
        pools[tab].append(item)
    return pools


def _known(pools: dict[str, list[dict]], name: str) -> bool:
    folded = name.casefold()
    return any(str(item.get("name") or "").casefold() == folded for rows in pools.values() for item in rows)


def _apply(item: dict, listing: dict) -> dict:
    return {
        **item,
        "name": listing["name"],
        "source_url": listing.get("source_url"),
        "tip": listing.get("blurb") or item.get("tip"),
        "hours": listing.get("hours"),
    }


def fill_vague_stops(days: list[dict], listings_by_city: dict[str, list[dict]], *, only_unmappable: bool = False) -> list[dict]:
    """Replace titles a map cannot pin. A flight, a train, and a real venue name stay.

    On a first draft, meals and stays also have to be listings. A later edit only replaces
    titles that are still not venues, so a swap's named restaurant is left alone.
    """
    used: set[str] = set()
    cursors = {city.casefold(): _pool(rows) for city, rows in listings_by_city.items()}

    def take(city: str, bucket: str) -> dict | None:
        pool = cursors.get(city.casefold(), {}).get(bucket) or []
        while pool:
            listing = pool.pop(0)
            name = str(listing.get("name") or "")
            if name and name.casefold() not in used:
                used.add(name.casefold())
                return listing
        return None

    filled = []
    for day in days:
        city = str(day.get("base") or "")
        pools = cursors.get(city.casefold(), {})
        items = []
        arrived = ""
        for item in day.get("items") or []:
            name = str(item.get("name") or "")
            city_to = arrival_city(name)
            if city_to:
                arrived = city_to
                if names_a_vehicle(name):
                    used.add(name.casefold())
                    items.append(item)
                continue
            bucket = _bucket(name, str(item.get("category") or ""))
            vague = is_vague(name)
            if bucket in {"food", "stay"} and not only_unmappable and not vague:
                if _known(pools, name):
                    used.add(name.casefold())
                    items.append(item)
                    continue
                listing = take(city, bucket)
                if listing:
                    items.append(_apply(item, listing))
                continue
            if vague:
                listing = take(city, bucket if bucket in {"food", "stay", "see"} else "see")
                if listing:
                    items.append(_apply(item, listing))
                continue
            used.add(name.casefold())
            items.append(item)
        if arrived:
            has_stay = any(
                _bucket(str(item.get("name") or ""), str(item.get("category") or "")) == "stay"
                and not is_vague(str(item.get("name") or ""))
                for item in items
            )
            if not has_stay:
                hotel = take(arrived, "stay")
                if hotel:
                    items.append({**_apply({"category": "stay", "name": hotel["name"]}, hotel), "category": "stay"})
            city = arrived
        filled.append({**day, "base": city, "items": items})
    return filled


def schedule_text(days: list[dict] | None) -> str:
    """The studio day list, with each listing's source, for the next chat turn."""
    lines = ["STUDIO SCHEDULE (this is the plan on screen; a place listed here is already on the trip):"]
    for day in days or []:
        lines.append(f"Day {day.get('day')} — {day.get('base') or ''}".rstrip())
        for item in day.get("items") or []:
            bits = [str(item.get("name") or "")]
            if item.get("start"):
                clock = str(item["start"])
                if item.get("end"):
                    clock += f"–{item['end']}"
                bits.append(clock)
            if item.get("hours"):
                bits.append(f"hours: {item['hours']}")
            if item.get("tip"):
                bits.append(str(item["tip"])[:160])
            if item.get("source_url"):
                bits.append(f"source: {item['source_url']}")
            lines.append("- " + " · ".join(bit for bit in bits if bit))
    return "\n".join(lines)


def stop_facts(days: list[dict] | None, name: str | None) -> str:
    """What the traveler pinned. Empty when nothing is pinned."""
    chosen = (name or "").strip()
    if not chosen:
        return ""
    for day in days or []:
        for item in day.get("items") or []:
            title = str(item.get("name") or "")
            if chosen.casefold() not in title.casefold():
                continue
            parts = [f"The traveler pinned {title} on day {day.get('day')} in {day.get('base') or 'the trip'}."]
            if item.get("tip"):
                parts.append(str(item["tip"]))
            if item.get("hours"):
                parts.append(f"Listed hours: {item['hours']}.")
            if item.get("source_url"):
                parts.append(f"Source: {item['source_url']}.")
            parts.append("Answer about this stop. It is already on the plan.")
            return " ".join(parts)
    return f"The traveler pinned {chosen}. It is not on the studio schedule."


async def listings_for_cities(cities: list[str]) -> dict[str, list[dict]]:
    """Google Places for food, stay, and sights, then the guide listings for that city."""
    from app.routing.tools import draft_lookups_enabled
    if not draft_lookups_enabled():
        return {}
    from app.routing.tools import hotel_listings
    from app.services.around_here import get_around_here
    from app.services.enrichment import geocode_query
    from app.services.place_search import city_point, google_in_city, merge_places
    found: dict[str, list[dict]] = {}
    for city in dict.fromkeys(c.strip() for c in cities if c and c.strip()):
        try:
            google = await google_in_city(city)
        except Exception:
            google = []
        try:
            stays = await hotel_listings(city)
        except Exception:
            stays = []
        wiki: list[dict] = []
        try:
            point = await city_point(city) or await geocode_query(city)
            if point:
                around = await get_around_here(float(point["lat"]), float(point["lon"]))
                wiki = (around or {}).get("items") or []
        except Exception:
            wiki = []
        rows = merge_places(google + stays, wiki)
        if rows:
            found[city] = rows
    return found
