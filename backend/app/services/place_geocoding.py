"""Optional precise place coordinates from the user's free ORS key."""

import logging

import httpx

from app.core.config import settings
from app.schemas.trip import GeocodeRequest

logger = logging.getLogger(__name__)
_cache: dict[str, tuple[float, float] | None] = {}


def _query_for(place, destination: str | None) -> str:
    return place.name if place.is_origin else f"{place.name}, {destination or ''}".strip(", ")


async def geocode_places(request: GeocodeRequest, destination: str | None) -> dict:
    """Pin each stop. Google Places when a key is set, otherwise the road geocoder, otherwise OpenStreetMap."""
    if settings.ORS_API_KEY:
        return await _geocode_ors(request, destination)
    if settings.GOOGLE_PLACES_API_KEY or settings.GOOGLE_MAPS_API_KEY:
        found = await _geocode_google(request, destination)
        if found:
            return {"provider": "google", "places": found}
    return {"provider": "nominatim", "places": await _geocode_nominatim(request, destination)}


async def _remember(query: str, point: tuple[float, float] | None) -> None:
    if len(_cache) >= 256:
        _cache.clear()
    _cache[query.casefold()] = point


async def _geocode_google(request: GeocodeRequest, destination: str | None) -> list[dict]:
    from app.services.place_search import search_places
    found = []
    for place in request.places:
        query = _query_for(place, destination)
        key = query.casefold()
        if key not in _cache:
            point = None
            try:
                rows = await search_places(query, kind="place", limit=1, destination="" if place.is_origin else (destination or ""))
                if rows and rows[0].get("lat") is not None:
                    point = (float(rows[0]["lat"]), float(rows[0]["lon"]))
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                logger.info("Place geocoding unavailable for %s: %s", place.id, type(exc).__name__)
            await _remember(query, point)
        if _cache.get(key):
            lat, lon = _cache[key]
            found.append({"id": place.id, "lat": lat, "lon": lon})
    return found


async def _geocode_nominatim(request: GeocodeRequest, destination: str | None) -> list[dict]:
    from app.services.enrichment import geocode_query
    found = []
    for place in request.places:
        query = _query_for(place, destination)
        key = query.casefold()
        if key not in _cache:
            point = None
            try:
                hit = await geocode_query(query)
                if hit:
                    point = (float(hit["lat"]), float(hit["lon"]))
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                logger.info("Place geocoding unavailable for %s: %s", place.id, type(exc).__name__)
            await _remember(query, point)
        if _cache.get(key):
            lat, lon = _cache[key]
            found.append({"id": place.id, "lat": lat, "lon": lon})
    return found


async def _geocode_ors(request: GeocodeRequest, destination: str | None) -> dict:

    found = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for place in request.places:
            query = place.name if place.is_origin else f"{place.name}, {destination or ''}".strip(", ")
            key = query.casefold()
            if key not in _cache:
                point = None
                request_succeeded = False
                try:
                    response = await client.get(
                        "https://api.openrouteservice.org/geocode/search",
                        headers={"Authorization": settings.ORS_API_KEY},
                        params={"text": query, "size": 5},
                    )
                    response.raise_for_status()
                    request_succeeded = True
                    features = response.json().get("features", [])
                    country = (place.name if place.is_origin else destination or "").split(",")[-1].strip()
                    region = (destination or "").split(",")[0].strip() if not place.is_origin else ""
                    for feature in features:
                        properties = feature.get("properties", {})
                        coordinates = feature.get("geometry", {}).get("coordinates", [])
                        if len(coordinates) < 2:
                            continue
                        if country and "," in (place.name if place.is_origin else destination or ""):
                            if properties.get("country", "").casefold() != country.casefold():
                                continue
                        if region and region.casefold() != country.casefold():
                            area = " ".join(str(properties.get(field, "")) for field in ("region", "county", "locality", "label"))
                            if region.casefold() not in area.casefold():
                                continue
                        lon, lat = coordinates[:2]
                        if -90 <= lat <= 90 and -180 <= lon <= 180:
                            point = (float(lat), float(lon))
                            break
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    logger.info("Place geocoding unavailable for %s: %s", place.id, exc)
                if request_succeeded:
                    if len(_cache) >= 256:
                        _cache.clear()
                    _cache[key] = point
            if _cache.get(key):
                lat, lon = _cache[key]
                found.append({"id": place.id, "lat": lat, "lon": lon})
    return {"provider": "openrouteservice", "places": found}
