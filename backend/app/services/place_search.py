"""Named restaurants and sights.

Google Places (New) text search is used when a key is set. OpenStreetMap is the
fallback, and it does not include hours or whether a place takes reservations.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_MASKS = (
    "places.displayName,places.formattedAddress,places.location,places.rating,places.googleMapsUri,places.websiteUri,places.regularOpeningHours,places.reservable,places.editorialSummary,places.priceRange,places.primaryType,places.types",
    "places.displayName,places.formattedAddress,places.location,places.rating,places.googleMapsUri",
)
_TYPES = {
    "restaurant": "restaurant",
    "cafe": "cafe",
    "sight": "tourist_attraction",
    "lodging": "lodging",
}
_CITY_SEARCHES = (
    ("restaurant", "restaurant", "food", "eat", 8),
    ("cafe", "cafe", "food", "drink", 4),
    ("lodging", "lodging", "stay", "sleep", 8),
    ("tourist attraction", "sight", "see", "see", 8),
)
_city_cache: dict[str, list[dict]] = {}
_nearby_cache: dict[str, list[dict]] = {}


def display_name(row: dict) -> str:
    name = str(row.get("name") or "").strip()
    short = ", ".join(part.strip() for part in str(row.get("label") or "").split(",")[:2] if part.strip())
    if short and short.casefold() != name.casefold():
        return f"{name} — {short}"
    return name


async def search_places(
    query: str, *, kind: str = "place", limit: int = 6, destination: str = "",
    lat: float | None = None, lon: float | None = None, radius: float = 5000,
    fallback: bool = True,
) -> list[dict]:
    """Real venues for a suggestion, a draft, or a swap. Empty when every source misses."""
    text = (query or "").strip()
    if destination and destination.casefold() not in text.casefold():
        text = f"{text} in {destination}"
    if not text:
        return []
    rows = await _google_text(text, kind, limit, lat, lon, radius)
    if rows or not fallback:
        return rows[:limit]
    from app.services.enrichment import search_named
    named = await search_named(text, limit)
    for row in named:
        row.setdefault("source", "openstreetmap")
    return named


async def draft_place_names(destination: str) -> tuple[list[str], list[str]]:
    """Specific sight and restaurant names for the plan writer."""
    if not (destination or "").strip():
        return [], []
    sights = await search_places(f"tourist attraction in {destination}", kind="sight", limit=6, destination=destination)
    restaurants = await search_places(f"restaurant in {destination}", kind="restaurant", limit=6, destination=destination)
    return [display_name(row) for row in sights], [display_name(row) for row in restaurants]


def _price(value: object) -> str | None:
    if not isinstance(value, dict):
        return None

    def amount(part: object) -> tuple[str, str] | None:
        if not isinstance(part, dict) or part.get("units") in (None, ""):
            return None
        return str(part.get("currencyCode") or ""), str(part["units"])

    start, end = amount(value.get("startPrice")), amount(value.get("endPrice"))
    if start is None:
        return None
    prefix = f"{start[0]} " if start[0] else ""
    if end is None:
        return f"{prefix}{start[1]}+"
    return f"{prefix}{start[1]}-{end[1]}"


def _cuisine(primary: object, types: object) -> str | None:
    kinds = [str(primary or "")] + [str(item) for item in types or [] if item]
    generic = {"restaurant", "food", "meal_takeaway", "meal_delivery", "lodging", "hotel"}
    for kind in kinds:
        if not kind or kind in generic:
            continue
        if kind.endswith("_restaurant"):
            words = kind[: -len("_restaurant")].replace("_", " ").strip()
            return words[:1].upper() + words[1:] if words else None
    return None


def as_around(row: dict, tab: str, kind: str, origin: tuple[float, float] | None = None) -> dict:
    """One Google (or map-search) row in the same shape as a Trip details listing."""
    hours = row.get("hours")
    if isinstance(hours, list):
        hours = "; ".join(str(part) for part in hours if part)
    elif hours is not None:
        hours = str(hours)
    bits = [str(row.get("cuisine") or "").strip(), str(row.get("blurb") or "").strip()]
    if isinstance(row.get("rating"), (int, float)):
        bits.append(f"Rated {row['rating']} on Google")
    blurb = ". ".join(bit for bit in bits if bit) or None
    lat, lon = row.get("lat"), row.get("lon")
    distance = None
    if origin and isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        from math import asin, cos, radians, sin, sqrt
        dlat = radians(float(lat) - origin[0])
        dlon = radians(float(lon) - origin[1])
        arc = sin(dlat / 2) ** 2 + cos(radians(origin[0])) * cos(radians(float(lat))) * sin(dlon / 2) ** 2
        distance = round(2 * 6371 * asin(sqrt(arc)), 2)
    name = str(row.get("name") or "").strip()
    return {
        "id": f"gp:{name.casefold()}",
        "name": name,
        "alt": None,
        "kind": kind,
        "tab": tab,
        "blurb": blurb,
        "price": row.get("price"),
        "hours": hours or None,
        "address": row.get("label"),
        "website": row.get("website") or None,
        "lat": float(lat) if isinstance(lat, (int, float)) else None,
        "lon": float(lon) if isinstance(lon, (int, float)) else None,
        "distance_km": distance,
        "image": None,
        "image_credit": None,
        "source": "Google Places",
        "source_url": row.get("maps_url") or row.get("website") or None,
        "wikipedia_url": None,
    }


def merge_places(primary: list[dict], extra: list[dict], per_tab: int = 12) -> list[dict]:
    """Google rows first, then guide listings, one name each, capped per tab."""
    seen: set[str] = set()
    ordered = []
    for item in primary + extra:
        name = str(item.get("name") or "").strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    counts: dict[str, int] = {}
    kept = []
    for item in ordered:
        tab = str(item.get("tab") or "see")
        counts[tab] = counts.get(tab, 0) + 1
        if tab == "landmarks" or counts[tab] <= per_tab:
            kept.append(item)
    return kept


async def google_in_city(city: str) -> list[dict]:
    """Restaurants, cafes, lodging, and sights for a city, from Google Places."""
    name = (city or "").strip()
    if not name:
        return []
    cached = _city_cache.get(name.casefold())
    if cached is not None:
        return cached
    found: list[dict] = []
    for words, kind, tab, item_kind, limit in _CITY_SEARCHES:
        rows = await search_places(f"best {words} in {name}", kind=kind, limit=limit, destination=name, fallback=False)
        if not rows:
            rows = await _serp_maps(f"best {words} in {name}")
        found.extend(as_around(row, tab, item_kind) for row in rows)
    if found:
        _city_cache[name.casefold()] = found
    return found


async def google_nearby(lat: float, lon: float) -> list[dict]:
    """The same four searches, biased to the stop the traveler is looking at."""
    key = f"{lat:.2f},{lon:.2f}"
    cached = _nearby_cache.get(key)
    if cached is not None:
        return cached
    found: list[dict] = []
    for words, kind, tab, item_kind, limit in _CITY_SEARCHES:
        rows = await search_places(words, kind=kind, limit=limit, lat=lat, lon=lon, radius=5000, fallback=False)
        found.extend(as_around(row, tab, item_kind, (lat, lon)) for row in rows)
    if found:
        _nearby_cache[key] = found
    return found


async def city_point(city: str) -> dict | None:
    """A point in the city from Google Places, so the guide lookup is not a country centroid."""
    rows = await search_places(city, kind="place", limit=1, fallback=False)
    if not rows:
        return None
    return {"lat": rows[0]["lat"], "lon": rows[0]["lon"]}


async def youtube_clip(query: str) -> str | None:
    """One video for a place the traveler just added. Empty when the key is off or the search misses."""
    key = (settings.YOUTUBE_API_KEY or "").strip()
    if not key or not settings.ENABLE_YOUTUBE:
        return None
    text = (query or "").strip()
    if not text:
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={"part": "snippet", "type": "video", "maxResults": 1, "q": text[:120], "key": key},
            )
            response.raise_for_status()
            items = response.json().get("items") or []
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.info("YouTube lookup unavailable: %s", type(exc).__name__)
        return None
    if not items or not isinstance(items[0], dict):
        return None
    video_id = str((items[0].get("id") or {}).get("videoId") or "")
    title = str((items[0].get("snippet") or {}).get("title") or "").strip()
    if not video_id:
        return None
    url = f"https://www.youtube.com/watch?v={video_id}"
    return f"{title} — {url}" if title else url


async def _serp_maps(query: str) -> list[dict]:
    """Map results when Places returns nothing for that search. Titles the source actually returned."""
    from app.routing.tools import _serp
    data = await _serp({"engine": "google_maps", "q": query[:180], "hl": "en", "type": "search"})
    found = []
    for row in (data or {}).get("local_results") or []:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        gps = row.get("gps_coordinates") if isinstance(row.get("gps_coordinates"), dict) else {}
        lat, lon = gps.get("latitude"), gps.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        hours = row.get("hours") if isinstance(row.get("hours"), str) else None
        found.append({
            "name": str(row["title"]),
            "lat": float(lat),
            "lon": float(lon),
            "label": str(row.get("address") or row["title"]),
            "rating": float(row["rating"]) if isinstance(row.get("rating"), (int, float)) else None,
            "hours": hours,
            "maps_url": row.get("link") or "",
            "website": "",
            "blurb": str(row.get("description") or row.get("type") or ""),
            "price": row.get("price") if isinstance(row.get("price"), str) else None,
            "source": "google",
        })
    return found


async def _google_text(
    text: str, kind: str, limit: int,
    lat: float | None = None, lon: float | None = None, radius: float = 5000,
) -> list[dict]:
    key = settings.GOOGLE_PLACES_API_KEY or settings.GOOGLE_MAPS_API_KEY
    if not key:
        return []
    payload = {
        "textQuery": text,
        "languageCode": "en",
        "maxResultCount": min(max(limit, 1), 20),
    }
    included = _TYPES.get(kind)
    if included:
        payload["includedType"] = included
    if lat is not None and lon is not None:
        payload["locationBias"] = {
            "circle": {
                "center": {"latitude": float(lat), "longitude": float(lon)},
                "radius": float(min(max(radius, 1.0), 50000.0)),
            }
        }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for mask in _MASKS:
                response = await client.post(
                    "https://places.googleapis.com/v1/places:searchText",
                    headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": mask, "Content-Type": "application/json"},
                    json=payload,
                )
                if response.status_code == 400 and mask != _MASKS[-1]:
                    continue
                response.raise_for_status()
                raw = response.json().get("places") or []
                break
            else:
                raw = []
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.info("Google place search unavailable: %s", type(exc).__name__)
        return []
    rows = []
    for place in raw:
        location = place.get("location") or {}
        lat, lon = location.get("latitude"), location.get("longitude")
        name = str((place.get("displayName") or {}).get("text") or "").strip()
        if not name or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        hours = ((place.get("regularOpeningHours") or {}).get("weekdayDescriptions")) or []
        reservable = place.get("reservable")
        rating = place.get("rating")
        summary = (place.get("editorialSummary") or {}).get("text") if isinstance(place.get("editorialSummary"), dict) else ""
        rows.append({
            "name": name,
            "lat": float(lat),
            "lon": float(lon),
            "label": str(place.get("formattedAddress") or name),
            "rating": float(rating) if isinstance(rating, (int, float)) else None,
            "reservable": reservable if isinstance(reservable, bool) else None,
            "hours": [str(hour) for hour in hours[:7]],
            "maps_url": place.get("googleMapsUri") or "",
            "website": place.get("websiteUri") or "",
            "blurb": str(summary or "").strip(),
            "price": _price(place.get("priceRange")),
            "cuisine": _cuisine(place.get("primaryType"), place.get("types")),
            "source": "google",
        })
        if len(rows) >= limit:
            break
    return rows
