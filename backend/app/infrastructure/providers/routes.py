"""Route geometry and duration. Google Routes when configured, haversine otherwise."""

from __future__ import annotations

import re
import uuid

from app.config import Settings
from app.domain.models import GeoPoint, RouteLeg, RouteStep
from app.infrastructure.http import haversine_km, request_json

# --- the published transport model -------------------------------------------
# One model for the schedule, the itinerary text, the console map and the TC-07
# check: walk under 2.5 km, metro or bus under 35 km, car under 160 km, then a
# flight. The console map mirrors these numbers (frontend maps/geo.ts and
# modeMinutes in organisms.tsx).
TRANSPORT_SPEED_KMH = {"walk": 4.5, "metro": 22.0, "drive": 32.0, "flight": 620.0}
TRANSPORT_OVERHEAD_MIN = {"walk": 0, "metro": 8, "drive": 5, "flight": 90}


def transport_mode(km: float) -> str:
    if km < 2.5:
        return "walk"
    if km < 35:
        return "metro"
    if km < 160:
        return "drive"
    return "flight"


def model_travel(km: float) -> tuple[str, int]:
    """Mode and minutes for a leg of ``km`` straight-line kilometres."""
    mode = transport_mode(km)
    minutes = max(2, round(km / TRANSPORT_SPEED_KMH[mode] * 60 + TRANSPORT_OVERHEAD_MIN[mode]))
    return mode, minutes


def step_mode(instruction: str, travel_mode: str = "WALK") -> str:
    """Google may fold a ferry (or cable car/tram) into a WALK route."""
    text = (instruction or "").lower()
    ferry_words = ("ferry" in text) or (
        "pier" in text and any(word in text for word in ("take", "board", "terminal"))
    )
    if travel_mode.upper() == "FERRY" or ferry_words:
        return "ferry"
    if "cable car" in text or "funicular" in text or "gondola" in text:
        return "cable"
    if re.search(r"\btram\b", text):
        return "tram"
    if travel_mode.upper() in {"TRANSIT", "BUS", "RAIL", "SUBWAY"}:
        return "transit"
    return "walk"


class TransportModelRoutes:
    """The transport model as a routes tool, so every transfer time is a logged result.

    The planner calls ``segments`` once per day through the Toolbox
    (``routes.segments``). Each returned leg carries an id, and the plan item
    records that id, so a transfer time on the plan can be traced to the tool
    result it came from (Proposal TC-09).
    """

    name = "transport-model"
    available = True

    async def segments(self, *, points: list[GeoPoint], ids: list[str]) -> list[RouteLeg]:
        """Legs between every ordered pair of the given stops."""
        if len(points) != len(ids):
            raise ValueError("points and ids must have the same length")
        call = uuid.uuid4().hex[:10]
        legs: list[RouteLeg] = []
        for i, origin in enumerate(points):
            for j, destination in enumerate(points):
                if i == j:
                    continue
                km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
                mode, minutes = model_travel(km)
                legs.append(
                    RouteLeg(
                        id=f"{call}:{i}-{j}",
                        from_id=ids[i],
                        to_id=ids[j],
                        from_geo=origin,
                        to_geo=destination,
                        duration_minutes=minutes,
                        distance_km=round(km, 3),
                        mode=mode,
                        source=self.name,
                        polyline=[origin, destination],
                    )
                )
        return legs


class GoogleRoutesSource:
    name = "google-routes"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._key = settings.google_maps_api_key or settings.google_places_api_key
        self.available = bool(self._key and settings.enable_routes)

    async def leg(self, origin: GeoPoint, destination: GeoPoint, *, mode: str = "walk") -> RouteLeg:
        travel_mode = {"walk": "WALK", "bicycle": "BICYCLE", "drive": "DRIVE", "transit": "TRANSIT"}.get(mode, "WALK")
        data = await request_json(
            "POST",
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            source=self.name,
            headers={
                "X-Goog-Api-Key": self._key,
                "X-Goog-FieldMask": (
                    "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,"
                    "routes.legs.steps.travelMode,routes.legs.steps.polyline.encodedPolyline,"
                    "routes.legs.steps.staticDuration,routes.legs.steps.navigationInstruction,"
                    "routes.legs.steps.transitDetails"
                ),
                "Content-Type": "application/json",
            },
            payload={
                "origin": {"location": {"latLng": {"latitude": origin.lat, "longitude": origin.lng}}},
                "destination": {
                    "location": {"latLng": {"latitude": destination.lat, "longitude": destination.lng}}
                },
                "travelMode": travel_mode,
                "languageCode": "en",
                "units": "METRIC",
            },
            timeout=self.settings.http_timeout_seconds,
            retries=self.settings.http_retries,
            user_agent=self.settings.user_agent,
        )
        routes = data.get("routes") or []
        if not routes:
            if mode == "transit":
                # This key/region returns no transit geometry. Keep the transit
                # estimate (walking + waiting) but follow the real road path so the
                # map vehicle does not cut across buildings.
                try:
                    road = await self.leg(origin, destination, mode="drive")
                    estimate = estimate_leg(origin, destination, "transit")
                    if road.polyline:
                        return estimate.model_copy(
                            update={
                                "distance_km": road.distance_km,
                                "polyline": road.polyline,
                                "source": "google-routes/road",
                            }
                        )
                except Exception:
                    pass
            return estimate_leg(origin, destination, mode)
        route = routes[0]
        duration = str(route.get("duration") or "")
        minutes = None
        if duration.endswith("s"):
            try:
                minutes = max(1, round(float(duration[:-1]) / 60))
            except ValueError:
                minutes = None
        polyline = decode_polyline(str((route.get("polyline") or {}).get("encodedPolyline") or ""))
        steps: list[RouteStep] = []
        for raw_leg in route.get("legs") or []:
            for raw_step in raw_leg.get("steps") or []:
                instruction = str(
                    (raw_step.get("navigationInstruction") or {}).get("instructions") or ""
                )
                step_seconds = str(raw_step.get("staticDuration") or "")
                step_minutes = None
                if step_seconds.endswith("s"):
                    try:
                        step_minutes = max(1, round(float(step_seconds[:-1]) / 60))
                    except ValueError:
                        step_minutes = None
                step_polyline = decode_polyline(
                    str((raw_step.get("polyline") or {}).get("encodedPolyline") or "")
                )
                line = (raw_step.get("transitDetails") or {}).get("transitLine") or {}
                vehicle = str((line.get("vehicle") or {}).get("type") or "")
                steps.append(
                    RouteStep(
                        mode=step_mode(instruction, str(raw_step.get("travelMode") or "WALK")),
                        minutes=step_minutes,
                        polyline=step_polyline,
                        instruction=instruction,
                        vehicle=vehicle,
                    )
                )
        return RouteLeg(
            from_geo=origin,
            to_geo=destination,
            duration_minutes=minutes,
            distance_km=(route.get("distanceMeters") or 0) / 1000 if route.get("distanceMeters") else None,
            mode=mode,
            source=self.name,
            polyline=polyline,
            steps=steps,
        )


    async def matrix(self, points: list[GeoPoint], *, mode: str = "walk") -> list[list[float]] | None:
        """N×N travel minutes between a day's stops (Route Matrix)."""
        if not self.available or len(points) < 2 or len(points) > 10:
            return None
        travel_mode = {"walk": "WALK", "bicycle": "BICYCLE", "drive": "DRIVE", "transit": "TRANSIT"}.get(mode, "WALK")
        waypoints = [
            {"waypoint": {"location": {"latLng": {"latitude": point.lat, "longitude": point.lng}}}}
            for point in points
        ]
        try:
            data = await request_json(
                "POST",
                "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix",
                source=self.name,
                headers={
                    "X-Goog-Api-Key": self._key,
                    "X-Goog-FieldMask": "originIndex,destinationIndex,duration,status,condition",
                    "Content-Type": "application/json",
                },
                payload={
                    "origins": waypoints,
                    "destinations": waypoints,
                    "travelMode": travel_mode,
                    "languageCode": "en",
                    "units": "METRIC",
                },
                timeout=max(self.settings.http_timeout_seconds, 20),
                retries=self.settings.http_retries,
                user_agent=self.settings.user_agent,
            )
        except Exception:
            return None
        rows = data.get("data") if isinstance(data.get("data"), list) else data.get("rows")
        if not isinstance(rows, list) or not rows:
            return None
        size = len(points)
        table = [[float("inf")] * size for _ in range(size)]
        for row in rows:
            if not isinstance(row, dict):
                continue
            origin_index = row.get("originIndex")
            destination_index = row.get("destinationIndex")
            duration = str(row.get("duration") or "")
            if origin_index is None or destination_index is None or not duration.endswith("s"):
                continue
            try:
                minutes = max(1.0, float(duration[:-1]) / 60)
            except ValueError:
                continue
            table[int(origin_index)][int(destination_index)] = minutes
        for index in range(size):
            table[index][index] = 0.0
        usable = any(
            table[i][j] != float("inf") for i in range(size) for j in range(size) if i != j
        )
        return table if usable else None


def estimate_leg(origin: GeoPoint, destination: GeoPoint, mode: str = "walk") -> RouteLeg:
    distance = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng) * 1.25
    speed = {"walk": 4.5, "transit": 18.0, "drive": 28.0}.get(mode, 4.5)
    return RouteLeg(
        from_geo=origin,
        to_geo=destination,
        duration_minutes=max(1, round((distance / speed) * 60)),
        distance_km=round(distance, 2),
        mode=mode,
        source="estimate",
        polyline=[origin, destination],
    )


def decode_polyline(encoded: str) -> list[GeoPoint]:
    if not encoded:
        return []
    points: list[GeoPoint] = []
    index = lat = lng = 0
    while index < len(encoded):
        for target in ("lat", "lng"):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if target == "lat":
                lat += delta
            else:
                lng += delta
        points.append(GeoPoint(lat=lat / 1e5, lng=lng / 1e5))
    return points
