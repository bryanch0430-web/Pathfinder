"""Optional road metrics from the free openrouteservice quota."""

import logging
from math import asin, cos, radians, sin, sqrt

import httpx

from app.core.config import settings
from app.schemas.trip import RouteMetricsRequest

logger = logging.getLogger(__name__)
_cache: dict[tuple[float, ...], dict] = {}


def _decode_polyline(encoded: str) -> list[list[float]]:
    """Decode a Google route polyline into the lon/lat pairs used by the map."""
    points = []
    index = lat = lon = 0
    while index < len(encoded):
        values = []
        for _ in range(2):
            shift = result = 0
            while index < len(encoded):
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1f) << shift
                shift += 5
                if byte < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        lat += values[0]
        lon += values[1]
        points.append([lon / 1e5, lat / 1e5])
    return points


async def _google_route(client: httpx.AsyncClient, leg) -> dict | None:
    response = await client.post(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        headers={
            "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
            "X-Goog-FieldMask": "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline,routes.travelAdvisory.tollInfo",
        },
        json={
            "origin": {"location": {"latLng": {"latitude": leg.from_lat, "longitude": leg.from_lon}}},
            "destination": {"location": {"latLng": {"latitude": leg.to_lat, "longitude": leg.to_lon}}},
            "travelMode": "DRIVE",
            "extraComputations": ["TOLLS"],
        },
    )
    response.raise_for_status()
    route = response.json()["routes"][0]
    tolls = (route.get("travelAdvisory") or {}).get("tollInfo", {}).get("estimatedPrice", [])
    toll = tolls[0] if tolls else None
    amount = (int(toll.get("units", "0")) + int(toll.get("nanos", 0)) / 1e9) if toll else None
    return {
        "distance_km": round(route["distanceMeters"] / 1000, 1),
        "duration_minutes": round(float(route["duration"].rstrip("s")) / 60),
        "geometry": _decode_polyline((route.get("polyline") or {}).get("encodedPolyline", "")),
        "toll_cost": f"{amount:.2f} {toll['currencyCode']}" if toll and amount is not None else None,
    }


def _air_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


async def get_route_metrics(request: RouteMetricsRequest) -> dict:
    """Return road metrics when available; never substitute them for air distance."""
    google_routes = bool(settings.GOOGLE_MAPS_API_KEY and settings.GOOGLE_ROUTES_ENABLED)
    if not google_routes and not settings.ORS_API_KEY:
        return {"provider": "unavailable", "legs": []}

    results = []
    async with httpx.AsyncClient(timeout=12.0) as client:
        for leg in request.legs:
            # Intercontinental legs are usually flights and cannot use a road router.
            if _air_km(leg.from_lat, leg.from_lon, leg.to_lat, leg.to_lon) > 2500:
                continue
            key = tuple(round(value, 5) for value in (
                leg.from_lat, leg.from_lon, leg.to_lat, leg.to_lon,
            ))
            route = _cache.get(key)
            if route is None:
                try:
                    # Free ORS routing is preferred. Google Routes requires an
                    # explicit opt-in so a demo Places key cannot incur route calls.
                    if google_routes:
                        try:
                            route = await _google_route(client, leg)
                        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
                            logger.info("Google route unavailable for leg %s: %s", leg.id, exc)
                    if route is None and settings.ORS_API_KEY:
                        response = await client.post(
                            "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
                            headers={"Authorization": settings.ORS_API_KEY},
                            json={"coordinates": [[leg.from_lon, leg.from_lat], [leg.to_lon, leg.to_lat]]},
                        )
                        response.raise_for_status()
                        feature = response.json()["features"][0]
                        summary = feature["properties"]["summary"]
                        route = {
                            "distance_km": round(summary["distance"] / 1000, 1),
                            "duration_minutes": round(summary["duration"] / 60),
                            "geometry": feature["geometry"]["coordinates"],
                        }
                    if route is None:
                        continue
                    if len(_cache) >= 256:
                        _cache.clear()
                    _cache[key] = route
                except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
                    logger.info("Road metrics unavailable for leg %s: %s", leg.id, exc)
                    continue
            results.append({"id": leg.id, **route})
    return {"provider": "google" if google_routes else "openrouteservice", "legs": results}
