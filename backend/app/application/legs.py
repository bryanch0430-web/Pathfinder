"""Per-leg route geometry for the map.

The console draws a day as a chain of its scheduled stops; a straight line
between two pins is a guess, not a route. Trips to and from the stay are not
invented here: if the itinerary does not schedule them, the map does not draw
them. This module asks the configured routes provider for the real polyline and
duration of each scheduled leg, including the legs to restaurants, and falls
back to a labelled estimate when no provider is available. The estimate is the
itinerary's own transfer (the transport model the planner's routes.segments call
returned), so the map and the schedule show the same minutes.

Results are cached per plan version: a plan version is immutable, so the same
legs never cost a second API call.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.config import Settings
from app.domain.models import GeoPoint, RouteLeg, TransferLeg, TripPlan
from app.infrastructure.http import haversine_km
from app.infrastructure.providers.routes import model_travel, transport_mode

# Legs per day are few (a day has at most ~7 stops), and the cache is bounded so a
# long session does not grow without limit.
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LIMIT = 64
# The routes API vocabulary for the transport model's modes. A local leg is never
# flown, so the longest legs are driven.
_API_MODE = {"walk": "walk", "metro": "transit", "drive": "drive", "flight": "drive"}

# On-demand "how else can I get there" comparison. Speeds are the published
# transport model (plus a cycle speed), used only when the provider cannot answer.
_OPTION_SPEED_KMH = {"walk": 4.5, "cycle": 15.0, "transit": 22.0, "drive": 32.0}
_OPTION_OVERHEAD_MIN = {"walk": 0, "cycle": 0, "transit": 8, "drive": 5}
_OPTIONS_CACHE: dict[str, dict[str, Any]] = {}
_OPTIONS_CACHE_LIMIT = 128


def _travel_mode(km: float) -> str:
    """The transport model's mode (walk under 2.5 km, transit under 35 km, then car)."""
    return _API_MODE[transport_mode(km)]


def _estimate(origin: GeoPoint, destination: GeoPoint, stored: TransferLeg | None) -> RouteLeg:
    """The itinerary's own leg: the plan's logged transfer, or the same transport model."""
    km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
    mode, minutes = model_travel(km)
    if stored is not None:
        mode, minutes = stored.mode, stored.minutes
    return RouteLeg(
        from_geo=origin,
        to_geo=destination,
        duration_minutes=minutes,
        distance_km=round(km, 2),
        mode=_API_MODE.get(mode, mode),
        source="estimate",
        polyline=[origin, destination],
    )


def _option_estimate(origin: GeoPoint, destination: GeoPoint, mode: str) -> RouteLeg:
    """The published-model fallback for one alternative, clearly labelled."""
    km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
    speed = _OPTION_SPEED_KMH[mode]
    minutes = max(2, round(km / speed * 60 + _OPTION_OVERHEAD_MIN[mode]))
    return RouteLeg(
        from_geo=origin,
        to_geo=destination,
        duration_minutes=minutes,
        distance_km=round(km, 2),
        mode=mode,
        source="estimate",
        polyline=[origin, destination],
    )


def _plan_point(plan: TripPlan, item_id: str) -> GeoPoint | None:
    for day in plan.days:
        for item in day.items:
            if item.id == item_id:
                return item.geo
    for hotel in plan.hotels:
        if hotel.id == item_id:
            return hotel.geo
    return None


def _plan_name(plan: TripPlan, item_id: str) -> str:
    for day in plan.days:
        for item in day.items:
            if item.id == item_id:
                return item.name
    for hotel in plan.hotels:
        if hotel.id == item_id:
            return hotel.name
    return item_id


async def leg_options_for_plan(
    plan: TripPlan,
    *,
    routes: list[Any],
    settings: Settings,
    from_id: str,
    to_id: str,
) -> dict[str, Any]:
    """Walk / cycle / transit / taxi comparison for one leg.

    Read-only: it never changes the plan's timing or mode. Google answers where
    it can; each option says whether it is routed or an estimate.
    """
    key = f"{plan.trip_id}:{plan.version}:{from_id}:{to_id}"
    cached = _OPTIONS_CACHE.get(key)
    if cached is not None:
        return cached

    origin = _plan_point(plan, from_id)
    destination = _plan_point(plan, to_id)
    if origin is None or destination is None:
        raise KeyError("both stops must be in the current plan")

    source = routes[0] if routes and getattr(routes[0], "available", False) else None

    async def resolve(mode: str) -> dict[str, Any]:
        if source is not None:
            try:
                provider_mode = "bicycle" if mode == "cycle" else mode
                leg = await asyncio.wait_for(
                    source.leg(origin, destination, mode=provider_mode),
                    timeout=settings.agent_timeout_seconds,
                )
                if leg.source == "estimate" and mode == "cycle":
                    # The provider can answer "no bicycle route"; use the cycle
                    # speed rather than its walking fallback.
                    leg = _option_estimate(origin, destination, mode)
                return {
                    "mode": mode,
                    "minutes": leg.duration_minutes,
                    "km": round(leg.distance_km, 2) if leg.distance_km is not None else None,
                    "source": leg.source,
                    "routed": leg.source == "google-routes",
                    "ferry": any(step.mode == "ferry" for step in leg.steps),
                }
            except Exception:
                pass
        leg = _option_estimate(origin, destination, mode)
        return {
            "mode": mode,
            "minutes": leg.duration_minutes,
            "km": round(leg.distance_km, 2) if leg.distance_km is not None else None,
            "source": leg.source,
            "routed": False,
            "ferry": False,
        }

    options = await asyncio.gather(*(resolve(mode) for mode in ("walk", "cycle", "transit", "drive")))
    payload = {
        "from_id": from_id,
        "to_id": to_id,
        "from_name": _plan_name(plan, from_id),
        "to_name": _plan_name(plan, to_id),
        "options": list(options),
    }
    if len(_OPTIONS_CACHE) >= _OPTIONS_CACHE_LIMIT:
        _OPTIONS_CACHE.pop(next(iter(_OPTIONS_CACHE)))
    _OPTIONS_CACHE[key] = payload
    return payload


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
        # WALK routes can contain a ferry/cable step; the map styles those
        # segments separately instead of drawing a footpath across water.
        "steps": [
            {
                "mode": step.mode,
                "minutes": step.minutes,
                "path": [[point.lat, point.lng] for point in step.polyline],
                "instruction": step.instruction,
            }
            for step in leg.steps
        ],
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
    jobs: list[tuple[str, str, GeoPoint, GeoPoint, str, TransferLeg | None]] = []

    for entry in days:
        items = sorted(
            (item for item in entry.items if item.geo is not None),
            key=lambda item: item.start,
        )
        for first, second in zip(items, items[1:]):
            first_geo = first.geo
            second_geo = second.geo
            if first_geo is None or second_geo is None:
                continue
            km = haversine_km(first_geo.lat, first_geo.lng, second_geo.lat, second_geo.lng)
            # The plan's own transfer for this pair, when it is from this stop.
            stored = second.transfer if second.transfer and second.transfer.from_item_id == first.id else None
            jobs.append((first.id, second.id, first_geo, second_geo, _travel_mode(km), stored))

    async def resolve(job: tuple[str, str, GeoPoint, GeoPoint, str, TransferLeg | None]) -> dict[str, Any]:
        from_id, to_id, origin, destination, mode, stored = job
        if source is None:
            return _dump(_estimate(origin, destination, stored), from_id=from_id, to_id=to_id)
        try:
            leg = await asyncio.wait_for(
                source.leg(origin, destination, mode=mode),
                timeout=settings.agent_timeout_seconds,
            )
        except Exception:
            leg = _estimate(origin, destination, stored)
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
