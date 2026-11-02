"""Per-leg route geometry for the map.

The console draws a day as a chain of legs; a straight line between two pins is a
guess, not a route. This module asks the configured routes provider for the real
polyline and duration of each leg, including the legs to restaurants, and falls
back to a labelled estimate when no provider is available.

Results are cached per plan version: a plan version is immutable, so the same
legs never cost a second API call.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.config import Settings
from app.domain.models import GeoPoint, RouteLeg, TripPlan
from app.infrastructure.http import haversine_km
from app.infrastructure.providers.routes import estimate_leg

# Legs per day are few (a day has at most ~7 stops), and the cache is bounded so a
# long session does not grow without limit.
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LIMIT = 64


def _travel_mode(km: float) -> str:
    """Walking for short hops, transit inside a city, driving beyond it."""
    if km < 1.6:
        return "walk"
    if km < 30:
        return "transit"
    return "drive"


def _cache_key(plan: TripPlan, day: str | None) -> str:
    return f"{plan.trip_id}:{plan.version}:{day or 'all'}"


def _dump(leg: RouteLeg, *, from_id: str, to_id: str) -> dict[str, Any]:
    return {
        "from_id": from_id,
        "to_id": to_id,
        "mode": leg.mode,
        "minutes": leg.duration_minutes,
        "km": round(leg.distance_km, 2) if leg.distance_km is not None else None,
        # [lat, lng] pairs: the map draws polylines in that order.
        "path": [[point.lat, point.lng] for point in leg.polyline],
        "source": leg.source,
    }


async def legs_for_plan(
    plan: TripPlan,
    *,
    routes: list[Any],
    settings: Settings,
    day: str | None = None,
) -> dict[str, Any]:
    key = _cache_key(plan, day)
    if key in _CACHE:
        return _CACHE[key]

    source = routes[0] if routes and getattr(routes[0], "available", False) else None
    days = [entry for entry in plan.days if day is None or entry.date.isoformat() == day]
    jobs: list[tuple[str, str, GeoPoint, GeoPoint, str]] = []
    hotel = plan.hotels[0] if plan.hotels else None
    # A day starts and ends at the stay: hotel -> first stop, last stop -> hotel.
    # A stay that is absurdly far away (another city) is left to the long-distance
    # tickets rather than drawn as a local leg.
    stay_geo = hotel.geo if hotel and hotel.geo is not None else None

    def local(origin: GeoPoint, destination: GeoPoint) -> bool:
        return haversine_km(origin.lat, origin.lng, destination.lat, destination.lng) <= 400

    for entry in days:
        items = sorted(
            (item for item in entry.items if item.geo is not None),
            key=lambda item: item.start,
        )
        if hotel and stay_geo is not None and items:
            first_geo = items[0].geo
            if first_geo is not None and local(stay_geo, first_geo):
                km = haversine_km(stay_geo.lat, stay_geo.lng, first_geo.lat, first_geo.lng)
                jobs.append((hotel.id, items[0].id, stay_geo, first_geo, _travel_mode(km)))
        for first, second in zip(items, items[1:]):
            first_geo = first.geo
            second_geo = second.geo
            if first_geo is None or second_geo is None:
                continue
            km = haversine_km(first_geo.lat, first_geo.lng, second_geo.lat, second_geo.lng)
            jobs.append((first.id, second.id, first_geo, second_geo, _travel_mode(km)))
        if hotel and stay_geo is not None and items:
            # The return leg is appended last so the list stays chronological.
            last_geo = items[-1].geo
            if last_geo is not None and local(last_geo, stay_geo):
                km = haversine_km(last_geo.lat, last_geo.lng, stay_geo.lat, stay_geo.lng)
                jobs.append((items[-1].id, hotel.id, last_geo, stay_geo, _travel_mode(km)))

    async def resolve(job: tuple[str, str, GeoPoint, GeoPoint, str]) -> dict[str, Any]:
        from_id, to_id, origin, destination, mode = job
        if source is None:
            return _dump(estimate_leg(origin, destination, mode), from_id=from_id, to_id=to_id)
        try:
            leg = await asyncio.wait_for(
                source.leg(origin, destination, mode=mode),
                timeout=settings.agent_timeout_seconds,
            )
        except Exception:
            leg = estimate_leg(origin, destination, mode)
        return _dump(leg, from_id=from_id, to_id=to_id)

    legs = await asyncio.gather(*(resolve(job) for job in jobs))
    payload = {
        "trip_id": plan.trip_id,
        "version": plan.version,
        "day": day,
        "legs": legs,
    }
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = payload
    return payload
