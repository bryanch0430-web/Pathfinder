"""Optional precise place coordinates from the user's free ORS key."""

import logging

import httpx

from app.core.config import settings
from app.schemas.trip import GeocodeRequest

logger = logging.getLogger(__name__)
_cache: dict[str, tuple[float, float] | None] = {}


async def geocode_places(request: GeocodeRequest, destination: str | None) -> dict:
    if not settings.ORS_API_KEY:
        return {"provider": "unavailable", "places": []}

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
