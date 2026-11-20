"""Bounded place lookups and honest, deterministic day-travel estimates.

Nominatim lookups reuse enrichment's persistent cache and 1/s limiter. Each trip
attempts at most 12 new names (6 in one turn); road routing attempts at most 6
pairs (2 in one turn). Failed and ambiguous lookups stay unknown, never "near".
"""

import asyncio
import logging
from math import asin, cos, radians, sin, sqrt

import httpx

logger = logging.getLogger(__name__)
POINT_LIMIT = 12
ROAD_LIMIT = 6
SPEED_KMH = {"walk": 4.5, "transit": 35.0, "taxi": 38.0, "drive": 42.0}


def _key(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def point(value: dict | None) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        lat, lon = float(value["lat"]), float(value["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def base_point(state: dict, base: str) -> tuple[float, float] | None:
    return point((state.get("route_points") or {}).get("base:" + _key(base)))


def item_point(state: dict, item: dict) -> tuple[float, float] | None:
    return point(item) or point((state.get("route_points") or {}).get("item:" + _key(item.get("name"))))


def air_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    value = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 12742.0 * asin(min(1.0, sqrt(value)))


def pair_key(a: tuple[float, float], b: tuple[float, float]) -> str:
    ends = sorted((f"{a[0]:.4f},{a[1]:.4f}", f"{b[0]:.4f},{b[1]:.4f}"))
    return "|".join(ends)


def leg(state: dict, a: tuple[float, float] | None, b: tuple[float, float] | None,
        mode: str) -> dict | None:
    if a is None or b is None:
        return None
    distance = air_km(a, b)
    if distance < 0.2:
        return {"hours": 0.0, "distance_km": 0.0, "basis": "nearby"}
    road = (state.get("route_times") or {}).get(pair_key(a, b))
    if road is not None and mode in ("taxi", "drive"):
        return {"hours": round(float(road) / 60, 2), "distance_km": round(distance, 1), "basis": "road"}
    speed = SPEED_KMH.get(mode, SPEED_KMH["transit"])
    if mode == "transit" and distance > 50:
        speed = 65.0
    elif mode in ("taxi", "drive") and distance > 50:
        speed = 58.0
    # Straight-line distance is shorter than an actual route. The multiplier is
    # deliberately conservative and is labelled as an estimate in every surface.
    hours = distance * 1.35 / speed + (0.12 if mode == "walk" else 0.25)
    return {"hours": round(hours, 2), "distance_km": round(distance, 1), "basis": "distance estimate"}


def day_travel(state: dict, base: str, items: list[dict], mode: str,
               fallback_minutes: int = 35) -> dict:
    """Travel from the overnight base through the listed places and back."""
    anchor = base_point(state, base)
    previous = anchor
    hours = known_hours = distance = unknown = 0.0
    basis: set[str] = set()
    for item in items:
        current = item_point(state, item)
        trip = leg(state, previous, current, mode)
        if trip:
            hours += trip["hours"]
            known_hours += trip["hours"]
            distance += trip["distance_km"]
            basis.add(trip["basis"])
        else:
            hours += fallback_minutes / 60
            unknown += 1
        previous = current
    if items:
        back = leg(state, previous, anchor, mode)
        if back:
            hours += back["hours"]
            known_hours += back["hours"]
            distance += back["distance_km"]
            basis.add(back["basis"])
        else:
            hours += fallback_minutes / 60
            unknown += 1
    return {"hours": round(hours, 2), "known_hours": round(known_hours, 2),
            "distance_km": round(distance, 1),
            "unverified_legs": int(unknown), "basis": "road" if basis == {"road"} else "distance estimate"}


async def resolve_points(state: dict, requests: list[tuple[str, str]], *, per_turn: int = 3) -> None:
    """Persist a small, trip-scoped lookup budget. Cached hits count too, keeping load predictable."""
    from app.services.enrichment import HEADERS, _search

    points = state.setdefault("route_points", {})
    tried = state.setdefault("route_queries", [])
    pending = [(key, query) for key, query in requests if key not in points and key not in tried]
    async with httpx.AsyncClient(timeout=4.0, headers=HEADERS) as client:
        for key, query in pending[:min(per_turn, max(0, POINT_LIMIT - len(tried)))]:
            tried.append(key)
            try:
                found = await asyncio.wait_for(_search(client, query), timeout=4.5)
            except (httpx.HTTPError, asyncio.TimeoutError, TypeError, ValueError) as exc:
                logger.info("Route place lookup unavailable for %s: %s", key, exc)
                continue
            if point(found):
                points[key] = {"lat": found["lat"], "lon": found["lon"]}


async def resolve_roads(state: dict, pairs: list[tuple[tuple[float, float], tuple[float, float]]],
                        *, per_turn: int = 2) -> None:
    """Use the existing optional road provider for a few legs; never require it."""
    from app.schemas.trip import RouteMetricsRequest
    from app.services.route_metrics import get_route_metrics

    times = state.setdefault("route_times", {})
    tried = state.setdefault("route_requests", [])
    fresh = list({pair_key(a, b): (a, b) for a, b in pairs
                  if air_km(a, b) >= 1 and pair_key(a, b) not in tried}.values())
    fresh.sort(key=lambda pair: air_km(*pair), reverse=True)
    chosen = fresh[:min(per_turn, max(0, ROAD_LIMIT - len(tried)))]
    if not chosen:
        return
    for a, b in chosen:
        tried.append(pair_key(a, b))
    request = RouteMetricsRequest.model_validate({"legs": [
        {"id": str(index), "from_lat": a[0], "from_lon": a[1], "to_lat": b[0], "to_lon": b[1]}
        for index, (a, b) in enumerate(chosen)]})
    try:
        result = await asyncio.wait_for(get_route_metrics(request), timeout=8.0)
    except (asyncio.TimeoutError, httpx.HTTPError, TypeError, ValueError) as exc:
        logger.info("Road route lookup unavailable: %s", exc)
        return
    for route in result.get("legs", []):
        try:
            a, b = chosen[int(route["id"])]
            minutes = float(route["duration_minutes"])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if minutes > 0:
            times[pair_key(a, b)] = minutes


def requests_for_day(destination: str, base: str, items: list[dict]) -> list[tuple[str, str]]:
    base_query = base if _key(base) == _key(destination) else f"{base}, {destination}"
    requests = [("base:" + _key(base), base_query)]
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        context = item.get("base")
        requests.append(("item:" + _key(name), f"{name}, {context}" if context and _key(name) != _key(context) else name))
    return requests


def road_pairs_for_day(state: dict, base: str, items: list[dict]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    points = [base_point(state, base), *(item_point(state, item) for item in items)]
    pairs = [(a, b) for a, b in zip(points, points[1:]) if a and b]
    if len(points) > 1 and points[-1] and points[0]:
        pairs.append((points[-1], points[0]))
    return pairs


def check_plan_days(state: dict, days: list[dict], mode: str, capacity: float) -> list[dict]:
    warnings = []
    for day in days:
        items = [item for item in day.get("items", []) if not item.get("option")]
        travel = day_travel(state, day.get("base") or "", items, mode)
        activity = sum(float(item.get("duration_hours") or (1 if item.get("category") == "food" else 1.5))
                       for item in items)
        far = next(((item, direct) for item in items if item.get("time_of_day") in ("afternoon", "evening")
                    if (direct := leg(state, base_point(state, day.get("base") or ""),
                                      item_point(state, item), mode)) and direct["hours"] > 3.5), None)
        if far or travel["known_hours"] >= capacity:
            message = (f"{far[0]['name']} is too far from {day['base']} for one afternoon: about {far[1]['hours']:g} h one way"
                       if far else f"Travel alone takes about {travel['hours']:g} h from {day['base']}")
            severity = "impossible"
        elif activity + travel["hours"] > capacity:
            message, severity = f"About {activity + travel['hours']:.1f} h of activities and travel exceeds this day's {capacity:g} h pace", "tight"
        elif travel["unverified_legs"]:
            message, severity = "Some places could not be located; travel time is provisional", "unverified"
        else:
            continue
        warnings.append({"day": day["day"], "severity": severity, "message": message,
                         "travel_hours": travel["hours"], "distance_km": travel["distance_km"],
                         "basis": travel["basis"]})
    return warnings


async def assess_itinerary(days: list[dict], destination: str, mode: str, pace: str,
                           previous: dict | None = None) -> dict:
    """Check a full draft or revision using a bounded set of named-place lookups."""
    state = {key: (previous or {}).get(key, {}).copy() if key in ("route_points", "route_times")
             else list((previous or {}).get(key, []))
             for key in ("route_points", "route_times", "route_queries", "route_requests")}
    requests = []
    for day in days:
        items = [item for item in day.get("items", []) if not item.get("option")]
        selected = [item for item in items if item.get("time_of_day") in ("afternoon", "evening")]
        day_requests = requests_for_day(destination, day.get("base") or destination,
                                        [*selected, *[item for item in items if item not in selected]])
        requests += day_requests
    requests = list(dict(requests).items())
    try:
        await asyncio.wait_for(resolve_points(state, requests, per_turn=6), timeout=8.0)
    except asyncio.TimeoutError:
        logger.info("Route place lookup reached its eight-second turn budget")
    pairs = []
    for day in days:
        items = [item for item in day.get("items", []) if not item.get("option")]
        pairs += road_pairs_for_day(state, day.get("base") or destination, items)
    if mode in ("taxi", "drive"):
        try:
            await asyncio.wait_for(resolve_roads(state, pairs), timeout=6.0)
        except asyncio.TimeoutError:
            logger.info("Road lookup reached its six-second turn budget")
    capacity = {"relaxed": 6.0, "balanced": 8.0, "packed": 11.0}.get(pace, 8.0)
    state["warnings"] = check_plan_days(state, days, mode, capacity)
    return state
