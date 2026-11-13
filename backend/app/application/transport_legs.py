"""Transport between the cities of a multi-city trip: the legs a route makes and the ranked options of each.

A leg gets flights from the flight sources when the cities are far enough apart, and ground options from the routes
source when they are near enough. A ground option is always an estimate: no price (the routes source quotes no fare),
a duration, an assumed departure and a directions link. Where the routes source has no real transit route the leg still
gets one rough estimate from the straight-line distance, labelled as such.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from itertools import pairwise
from typing import Any
from urllib.parse import urlencode

from app.application.planner import Planner
from app.application.toolbox import Toolbox
from app.domain.models import (
    DataStatus,
    Destination,
    GeoPoint,
    RouteLeg,
    TicketOption,
    TransportLeg,
    TripSegment,
)
from app.infrastructure.http import haversine_km
from app.infrastructure.providers.routes import estimate_leg

FLIGHT_MIN_KM = 150
GROUND_MAX_KM = 900
AIRPORT_MINUTES = 150
ESTIMATE_DEPARTS = "09:00"
#: Most options one leg keeps.
MAX_LEG_OPTIONS = 5

#: Google's transit vehicle types by kind (Routes API ``transitLine.vehicle.type``).
_RAIL = frozenset(
    {
        "RAIL",
        "HEAVY_RAIL",
        "COMMUTER_TRAIN",
        "HIGH_SPEED_TRAIN",
        "LONG_DISTANCE_TRAIN",
        "METRO_RAIL",
        "MONORAIL",
        "SUBWAY",
        "TRAM",
        "LIGHT_RAIL",
        "TRAIN",
    }
)
_BUS = frozenset({"BUS", "INTERCITY_BUS", "TROLLEYBUS", "SHARE_TAXI"})


def _routed(leg: RouteLeg) -> bool:
    """True when the routes source itself answered with a route, not a distance estimate it fell back to."""
    return leg.source == "google-routes"


def ground_mode(leg: RouteLeg) -> str:
    """``train`` when the longest transit step of a routed leg is rail, ``bus`` when it is a bus, else ``ground``.

    A vehicle the source did not name is not guessed: the option is then the rough ground estimate.
    """
    steps = [step for step in leg.steps if step.vehicle]
    if not steps:
        return "ground"
    longest = max(steps, key=lambda step: step.minutes or 0)
    if longest.vehicle in _RAIL:
        return "train"
    if longest.vehicle in _BUS:
        return "bus"
    return "ground"


def _door_minutes(option: TicketOption) -> int | None:
    minutes = option.duration_minutes
    if minutes is None and option.depart_at and option.arrive_at:
        minutes = round((option.arrive_at - option.depart_at).total_seconds() / 60)
    if minutes is None:
        return None
    return minutes + (AIRPORT_MINUTES if option.mode == "flight" else 0)


def rank_options(options: list[TicketOption]) -> list[TicketOption]:
    """By door-to-door minutes (flights count the time at the airports), then price; unknowns last."""

    def key(option: TicketOption) -> tuple[bool, int, bool, float]:
        door = _door_minutes(option)
        price = option.price.amount if option.price else None
        return (door is None, door or 0, price is None, price or 0.0)

    return sorted(options, key=key)


def build_legs(
    segments: list[TripSegment], origin: str, one_way: bool
) -> list[TransportLeg]:
    """``leg_out`` (origin to the first city, on the first day) when an origin is set; ``leg_<n>`` between cities on the
    day they share; ``leg_home`` (the last city to the origin, on the last day) when an origin is set and the trip is
    not one way."""
    legs: list[TransportLeg] = []
    if origin and segments:
        legs.append(
            TransportLeg(
                id="leg_out",
                from_place=origin,
                to_place=segments[0].destination.name,
                depart_date=segments[0].dates.start,
                kind="outbound",
            )
        )
    for number, (before, after) in enumerate(pairwise(segments), 1):
        legs.append(
            TransportLeg(
                id=f"leg_{number}",
                from_place=before.destination.name,
                to_place=after.destination.name,
                depart_date=after.dates.start,
                kind="between",
            )
        )
    if origin and segments and not one_way:
        legs.append(
            TransportLeg(
                id="leg_home",
                from_place=segments[-1].destination.name,
                to_place=origin,
                depart_date=segments[-1].dates.end,
                kind="return",
            )
        )
    return legs


def _directions_link(origin: str, destination: str, travel_mode: str) -> str:
    query = urlencode(
        {
            "api": 1,
            "origin": origin,
            "destination": destination,
            "travelmode": travel_mode,
        }
    )
    return f"https://www.google.com/maps/dir/?{query}"


def _estimated(
    leg: TransportLeg,
    route: RouteLeg,
    mode: str,
    name: str,
    travel_mode: str,
    *,
    option_id: str,
) -> TicketOption:
    depart = datetime.combine(
        leg.depart_date or datetime.now().date(), time.fromisoformat(ESTIMATE_DEPARTS)
    )
    minutes = route.duration_minutes
    return TicketOption(
        id=option_id,
        leg_id=leg.id,
        mode=mode,
        name=name,
        origin=leg.from_place,
        destination=leg.to_place,
        depart_at=depart,
        arrive_at=depart + timedelta(minutes=minutes) if minutes else None,
        duration_minutes=minutes,
        price=None,
        estimated=True,
        booking_url=_directions_link(leg.from_place, leg.to_place, travel_mode),
        notes="Estimate: no timetable or fare was looked up.",
        data_status=DataStatus.OK,
    )


async def _ground_options(
    leg: TransportLeg,
    origin: GeoPoint,
    destination: GeoPoint,
    *,
    registry: Any,
    toolbox: Toolbox,
) -> list[TicketOption]:
    sources = [
        s
        for s in getattr(registry, "routes", [])
        if getattr(s, "available", True) and hasattr(s, "leg")
    ]
    transit: RouteLeg | None = None
    driving: RouteLeg | None = None
    for source in sources:
        try:
            transit = transit or await toolbox.call(
                "routes.leg",
                source.leg,
                origin=origin,
                destination=destination,
                mode="transit",
            )
            driving = driving or await toolbox.call(
                "routes.leg",
                source.leg,
                origin=origin,
                destination=destination,
                mode="drive",
            )
        except Exception:  # noqa: BLE001, S112 - a source that fails leaves the leg to the others
            continue
        if transit is not None and driving is not None:
            break
    options: list[TicketOption] = []
    if transit is not None and _routed(transit) and ground_mode(transit) != "ground":
        mode = ground_mode(transit)
        options.append(
            _estimated(
                leg,
                transit,
                mode,
                f"{mode.capitalize()} (estimate)",
                "transit",
                option_id=f"{leg.id}_{mode}",
            )
        )
    else:
        # No real transit route: one rough estimate from the straight-line distance.
        rough = estimate_leg(origin, destination, "drive")
        options.append(
            _estimated(
                leg,
                rough,
                "ground",
                "Ground transport (rough estimate)",
                "transit",
                option_id=f"{leg.id}_ground",
            )
        )
    if driving is not None and _routed(driving):
        options.append(
            _estimated(
                leg,
                driving,
                "drive",
                "Drive (estimate)",
                "driving",
                option_id=f"{leg.id}_drive",
            )
        )
    return options


async def _flight_options(
    leg: TransportLeg,
    *,
    registry: Any,
    toolbox: Toolbox,
    party_size: int,
    currency: str,
) -> list[TicketOption]:
    settings = getattr(registry, "settings", None)
    if settings is not None and not getattr(settings, "enable_flights", True):
        return []
    for source in getattr(registry, "flights", []):
        if not getattr(source, "available", True):
            continue
        try:
            results = await asyncio.wait_for(
                toolbox.call(
                    "flights.search",
                    source.search,
                    origin=leg.from_place,
                    destination=leg.to_place,
                    depart_date=leg.depart_date,
                    return_date=None,
                    party_size=party_size,
                    currency=currency,
                ),
                timeout=25.0,
            )
        except Exception:  # noqa: BLE001, S112 - a source that fails leaves the leg to the others
            continue
        if results:
            rows = [
                {"id": str(n + 1), **flight.model_dump(mode="json")}
                for n, flight in enumerate(results)
            ]
            return [
                ticket.model_copy(
                    update={"id": f"{leg.id}_{ticket.id}", "leg_id": leg.id}
                )
                for ticket in Planner._build_tickets(rows)
            ]
    return []


async def leg_options(
    leg: TransportLeg,
    origin: Destination,
    destination: Destination,
    *,
    registry: Any,
    toolbox: Toolbox,
    party_size: int,
    currency: str,
) -> list[TicketOption]:
    """Ranked options for one leg, the first one ``selected``. Every option has ``leg_id=leg.id``.

    Flights when the two places are more than ``FLIGHT_MIN_KM`` apart (or their distance is unknown); ground options
    when both have coordinates and are less than ``GROUND_MAX_KM`` apart. A leg may end with no option at all.
    """
    km: float | None = None
    if origin.geo is not None and destination.geo is not None:
        km = haversine_km(
            origin.geo.lat, origin.geo.lng, destination.geo.lat, destination.geo.lng
        )
    options: list[TicketOption] = []
    if km is None or km > FLIGHT_MIN_KM:
        options += await _flight_options(
            leg,
            registry=registry,
            toolbox=toolbox,
            party_size=party_size,
            currency=currency,
        )
    if km is not None and km < GROUND_MAX_KM:
        options += await _ground_options(
            leg, origin.geo, destination.geo, registry=registry, toolbox=toolbox
        )
    ranked = rank_options(options)[:MAX_LEG_OPTIONS]
    return [
        option.model_copy(update={"selected": index == 0})
        for index, option in enumerate(ranked)
    ]
