"""Per-leg route geometry for the map.

A day is drawn from the stay: the hotel, then each stop in order, then back to
the hotel. An airport taxi is the first or last point when that day has one.
A stay in another region is not used as the start. This module asks the
configured routes provider for the real polyline of each of those legs, and
falls back to a labelled estimate when no provider is available.

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
_API_MODE = {"walk": "walk", "metro": "transit", "drive": "drive", "flight": "drive", "taxi": "drive"}

# On-demand "how else can I get there" comparison. Speeds are the published
# transport model (plus a cycle speed), used only when the provider cannot answer.
_OPTION_SPEED_KMH = {"walk": 4.5, "cycle": 15.0, "transit": 22.0, "drive": 32.0}
_OPTION_OVERHEAD_MIN = {"walk": 0, "cycle": 0, "transit": 8, "drive": 5}
_OPTIONS_CACHE: dict[str, dict[str, Any]] = {}
_OPTIONS_CACHE_LIMIT = 128


def _travel_mode(km: float) -> str:
    """The transport model's mode (walk under 2.5 km, transit under 35 km, then car)."""
    return _API_MODE[transport_mode(km)]


def _estimate(origin: GeoPoint, destination: GeoPoint, stored: TransferLeg | None, forced: str | None = None) -> RouteLeg:
    """The itinerary's own leg: the plan's logged transfer, or the same transport model."""
    from app.infrastructure.providers.routes import TRANSPORT_OVERHEAD_MIN, TRANSPORT_SPEED_KMH

    km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
    mode, minutes = model_travel(km)
    if stored is not None:
        mode, minutes = stored.mode, stored.minutes
    if forced:
        mode = forced
        if stored is None:
            speed = TRANSPORT_SPEED_KMH.get(forced, TRANSPORT_SPEED_KMH["drive"])
            overhead = TRANSPORT_OVERHEAD_MIN.get(forced, 0)
            minutes = max(2, round(km / speed * 60 + overhead))
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
    """Walk / cycle / transit / taxi for one leg, each with the line the map draws.

    The plan itself is not rewritten here. The map applies the picked mode:
    that line replaces the hop, and later stop times move by the difference.
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
                return _option_payload(mode, leg, routed=leg.source == "google-routes")
            except Exception:
                pass
        return _option_payload(mode, _option_estimate(origin, destination, mode), routed=False)

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


# A stay farther than this from every stop on the day is another city's hotel.
_STAY_RADIUS_KM = 150
_AIRPORT_POINTS: dict[str, GeoPoint] | None = None


def _airport_point(code: str) -> GeoPoint | None:
    global _AIRPORT_POINTS
    if _AIRPORT_POINTS is None:
        from app.infrastructure.providers.flights import _bundled_airports

        _AIRPORT_POINTS = {iata: GeoPoint(lat=lat, lng=lng) for iata, lat, lng, _size in _bundled_airports()}
    return _AIRPORT_POINTS.get((code or "").upper())


def _transfer_point(plan: TripPlan, item: Any) -> GeoPoint | None:
    """Where an airport taxi sits. The item rarely has its own coordinates, so the airport code is used."""
    if item.geo is not None:
        return item.geo
    leg_id = str(item.id).removeprefix("transit_")
    leg = next((candidate for candidate in plan.legs if candidate.id == leg_id), None)
    if leg is None or not leg.airport:
        return None
    return _airport_point(leg.airport)


def _stay_point(plan: TripPlan, day: Any, stops: list[GeoPoint]) -> tuple[str, GeoPoint] | None:
    stay = plan.stay_on(day.date)
    if stay is None or stay.geo is None:
        return None
    if stops and min(haversine_km(stay.geo.lat, stay.geo.lng, point.lat, point.lng) for point in stops) > _STAY_RADIUS_KM:
        return None
    return stay.id, stay.geo


def _day_points(plan: TripPlan, day: Any) -> list[tuple[str, GeoPoint, str]]:
    """The day's route in order: airport arrival, the stay, the stops, then the airport or back to the stay."""
    ordered = sorted(day.items, key=lambda item: item.start)
    arrivals = [item for item in ordered if str(item.id).startswith("transit_to_stay_")]
    departures = [item for item in ordered if str(item.id).startswith("transit_from_stay_")]
    middle = [item for item in ordered if item.geo is not None and item not in arrivals and item not in departures]
    stay = _stay_point(plan, day, [item.geo for item in middle if item.geo is not None])
    hotel = plan.stay_on(day.date) if stay is not None else None
    points: list[tuple[str, GeoPoint, str]] = []
    for item in arrivals:
        geo = _transfer_point(plan, item)
        if geo is not None:
            points.append((item.id, geo, item.name))
    if stay is not None and hotel is not None:
        points.append((stay[0], stay[1], hotel.name))
    for item in middle:
        points.append((item.id, item.geo, item.name))
    left = False
    for item in departures:
        geo = _transfer_point(plan, item)
        if geo is not None:
            points.append((item.id, geo, item.name))
            left = True
    if stay is not None and hotel is not None and middle and not left:
        points.append((stay[0], stay[1], hotel.name))
    kept: list[tuple[str, GeoPoint, str]] = []
    for point in points:
        if kept and kept[-1][0] == point[0]:
            continue
        kept.append(point)
    return kept


def _option_payload(mode: str, leg: RouteLeg, *, routed: bool) -> dict[str, Any]:
    """One how-to-get-there choice, including the line so the map can draw it."""
    dumped = _dump(leg, from_id="", to_id="")
    return {
        "mode": mode,
        "minutes": dumped["minutes"],
        "km": dumped["km"],
        "source": leg.source,
        "routed": routed,
        "ferry": any(step.mode == "ferry" for step in leg.steps),
        "path": dumped["path"],
        "steps": dumped["steps"],
    }


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
    anchors: list[dict[str, Any]] = []
    seen_anchors: set[str] = set()

    for entry in days:
        points = _day_points(plan, entry)
        by_id = {item.id: item for item in entry.items}
        for point_id, geo, name in points:
            if point_id in seen_anchors or _plan_point(plan, point_id) is not None:
                continue
            seen_anchors.add(point_id)
            anchors.append({"id": point_id, "name": name, "lat": geo.lat, "lng": geo.lng})
        for (first_id, first_geo, _), (second_id, second_geo, _) in zip(points, points[1:]):
            km = haversine_km(first_geo.lat, first_geo.lng, second_geo.lat, second_geo.lng)
            following = by_id.get(second_id)
            stored = (
                following.transfer
                if following is not None and following.transfer and following.transfer.from_item_id == first_id
                else None
            )
            airport = first_id.startswith("transit_to_stay_") or first_id.startswith("transit_from_stay_") or second_id.startswith("transit_to_stay_") or second_id.startswith("transit_from_stay_")
            jobs.append((first_id, second_id, first_geo, second_geo, "drive" if airport else _travel_mode(km), stored))

    async def resolve(job: tuple[str, str, GeoPoint, GeoPoint, str, TransferLeg | None]) -> dict[str, Any]:
        from_id, to_id, origin, destination, mode, stored = job
        if source is None:
            return _dump(_estimate(origin, destination, stored, mode if mode == "drive" else None), from_id=from_id, to_id=to_id)
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
        "anchors": anchors,
    }
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = payload
    return payload
