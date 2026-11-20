"""Small, trip-scoped adapter for Google Places Nearby Search (New)."""

import logging
import asyncio
from datetime import date
from pathlib import Path
import sqlite3
from time import monotonic
from math import asin, cos, radians, sin, sqrt

import httpx

from app.core.config import settings
from app.schemas.trip import NearbyPlacesRequest

logger = logging.getLogger(__name__)
_nearby_cache: dict[tuple[float, float], tuple[float, dict]] = {}
_budget_lock = asyncio.Lock()
BUDGET_DB_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "google_places_budget.sqlite3"
MAX_GOOGLE_NEARBY_CALLS_PER_DAY = 12
MAX_GOOGLE_NEARBY_CALLS_PER_TRIP_PER_DAY = 4


def _consume_daily_budget(trip_id: str) -> bool:
    """Atomically reserve one lookup across restarts and worker processes."""
    connection = None
    try:
        BUDGET_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(BUDGET_DB_PATH, timeout=3)
        with connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS nearby_usage (
                day TEXT NOT NULL, trip_id TEXT NOT NULL, calls INTEGER NOT NULL,
                PRIMARY KEY (day, trip_id)
            )""")
            connection.execute("BEGIN IMMEDIATE")
            today = date.today().isoformat()
            total = connection.execute(
                "SELECT COALESCE(SUM(calls), 0) FROM nearby_usage WHERE day = ?", (today,),
            ).fetchone()[0]
            trip_calls = connection.execute(
                "SELECT calls FROM nearby_usage WHERE day = ? AND trip_id = ?", (today, trip_id),
            ).fetchone()
            if total >= MAX_GOOGLE_NEARBY_CALLS_PER_DAY or (
                trip_calls and trip_calls[0] >= MAX_GOOGLE_NEARBY_CALLS_PER_TRIP_PER_DAY
            ):
                return False
            connection.execute("""INSERT INTO nearby_usage (day, trip_id, calls) VALUES (?, ?, 1)
                ON CONFLICT(day, trip_id) DO UPDATE SET calls = calls + 1""", (today, trip_id))
            connection.execute("DELETE FROM nearby_usage WHERE day < ?", (today,))
            return True
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Google nearby budget could not be checked; declining lookup: %s", exc)
        return False
    finally:
        if connection is not None:
            connection.close()


def _distance_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    lat_delta, lon_delta = radians(lat_b - lat_a), radians(lon_b - lon_a)
    arc = sin(lat_delta / 2) ** 2 + cos(radians(lat_a)) * cos(radians(lat_b)) * sin(lon_delta / 2) ** 2
    return round(6371 * 2 * asin(sqrt(arc)), 1)


async def get_nearby_places(request: NearbyPlacesRequest, trip_id: str = "anonymous") -> dict:
    if not settings.GOOGLE_MAPS_API_KEY:
        return {"provider": "unavailable", "places": []}

    key = (round(request.lat, 4), round(request.lon, 4))
    async with _budget_lock:
        cached = _nearby_cache.get(key)
        if cached and cached[0] > monotonic():
            return cached[1]
        _nearby_cache.pop(key, None)
        if not await asyncio.to_thread(_consume_daily_budget, trip_id):
            return {"provider": "limit_reached", "places": []}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://places.googleapis.com/v1/places:searchNearby",
                headers={
                    "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
                    "X-Goog-FieldMask": "places.id,places.displayName,places.location,places.primaryTypeDisplayName,places.googleMapsUri",
                },
                json={
                    "includedTypes": ["tourist_attraction", "museum", "park"],
                    "maxResultCount": 5,
                    "rankPreference": "POPULARITY",
                    "languageCode": "en",
                    "locationRestriction": {"circle": {
                        "center": {"latitude": request.lat, "longitude": request.lon},
                        "radius": 5000.0,
                    }},
                },
            )
            response.raise_for_status()
            raw = response.json().get("places", [])
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.info("Google nearby places unavailable: %s", exc)
        return {"provider": "unavailable", "places": []}

    places = []
    for place in raw:
        location = place.get("location") or {}
        lat, lon = location.get("latitude"), location.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        places.append({
            "id": place.get("id"),
            "name": (place.get("displayName") or {}).get("text", ""),
            "category": (place.get("primaryTypeDisplayName") or {}).get("text", "Place"),
            "lat": lat,
            "lon": lon,
            "distance_km": _distance_km(request.lat, request.lon, lat, lon),
            "maps_url": place.get("googleMapsUri"),
        })
    result = {"provider": "google", "places": places}
    if len(_nearby_cache) >= 128:
        _nearby_cache.clear()
    _nearby_cache[key] = (monotonic() + 3600, result)
    return result
