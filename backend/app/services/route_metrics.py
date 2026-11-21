"""Optional road metrics from the free openrouteservice quota."""

import asyncio
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


def route_mode(km: float) -> str:
    """The same split the router uses: walk nearby, transit across a city, drive beyond that."""
    if km < 2.5:
        return "WALK"
    if km < 35:
        return "TRANSIT"
    return "DRIVE"


async def _google_route(client: httpx.AsyncClient, leg, mode: str) -> dict | None:
    key = settings.GOOGLE_MAPS_API_KEY or settings.GOOGLE_PLACES_API_KEY
    if not key:
        return None
    payload = {
        "origin": {"location": {"latLng": {"latitude": leg.from_lat, "longitude": leg.from_lon}}},
        "destination": {"location": {"latLng": {"latitude": leg.to_lat, "longitude": leg.to_lon}}},
        "travelMode": mode,
        "languageCode": "en",
        "units": "METRIC",
    }
    if mode == "DRIVE":
        payload["extraComputations"] = ["TOLLS"]
    if mode == "TRANSIT":
        from datetime import datetime, timezone
        payload["departureTime"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    response = await client.post(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        headers={
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline,routes.travelAdvisory.tollInfo",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    routes = response.json().get("routes") or []
    if not routes:
        if mode == "TRANSIT":
            road = await _google_route(client, leg, "DRIVE")
            if road:
                road["mode"] = "TRANSIT"
                return road
        return None
    route = routes[0]
    tolls = (route.get("travelAdvisory") or {}).get("tollInfo", {}).get("estimatedPrice", [])
    toll = tolls[0] if tolls else None
    amount = (int(toll.get("units", "0")) + int(toll.get("nanos", 0)) / 1e9) if toll else None
    geometry = _decode_polyline((route.get("polyline") or {}).get("encodedPolyline", ""))
    if len(geometry) < 2:
        return None
    return {
        "distance_km": round((route.get("distanceMeters") or 0) / 1000, 1),
        "duration_minutes": round(float(str(route.get("duration") or "0s").rstrip("s")) / 60) or 1,
        "geometry": geometry,
        "mode": mode,
        "toll_cost": f"{amount:.2f} {toll['currencyCode']}" if toll and amount is not None else None,
    }


def _air_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


async def get_route_metrics(request: RouteMetricsRequest) -> dict:
    """Return road metrics when available; never substitute them for air distance."""
    google_routes = bool((settings.GOOGLE_MAPS_API_KEY or settings.GOOGLE_PLACES_API_KEY) and settings.GOOGLE_ROUTES_ENABLED)
    if not google_routes and not settings.ORS_API_KEY:
        return {"provider": "unavailable", "legs": []}

    async with httpx.AsyncClient(timeout=8.0) as client:
        gate = asyncio.Semaphore(3)

        async def one(leg):
            km = _air_km(leg.from_lat, leg.from_lon, leg.to_lat, leg.to_lon)
            if km > 2500:
                return None
            mode = route_mode(km)
            key = tuple(round(value, 5) for value in (
                leg.from_lat, leg.from_lon, leg.to_lat, leg.to_lon,
            )) + (mode,)
            route = _cache.get(key)
            if route is None:
                async with gate:
                    route = _cache.get(key)
                    if route is None:
                        try:
                            # Free ORS routing is preferred. Google Routes requires an
                            # explicit opt-in so a demo Places key cannot incur route calls.
                            if google_routes:
                                try:
                                    route = await _google_route(client, leg, mode)
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
                                return None
                            if len(_cache) >= 256:
                                _cache.clear()
                            _cache[key] = route
                        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
                            logger.info("Road metrics unavailable for leg %s: %s", leg.id, exc)
                            return None
            return {"id": leg.id, **route}

        results = [row for row in await asyncio.gather(*(one(leg) for leg in request.legs)) if row]
    return {"provider": "google" if google_routes else "openrouteservice", "legs": results}
