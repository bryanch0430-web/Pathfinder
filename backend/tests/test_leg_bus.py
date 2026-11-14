"""A short leg gets one estimated bus-or-shuttle option when transit returned no bus."""

from __future__ import annotations

import asyncio
from datetime import date, datetime

from app.application.toolbox import Toolbox
from app.application.transport_legs import ground_mode, leg_options
from app.config import Settings
from app.domain.models import (
    Destination,
    GeoPoint,
    RouteLeg,
    RouteStep,
    TicketOption,
    TransportLeg,
)
from app.evaluation import SandboxRegistry
from app.infrastructure.http import haversine_km
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.routes import estimate_leg

DAY = date(2026, 12, 5)
KYOTO = Destination(name="Kyoto", geo=GeoPoint(lat=35.01, lng=135.77))
OSAKA = Destination(name="Osaka", geo=GeoPoint(lat=34.69, lng=135.50))
SAPPORO = Destination(name="Sapporo", geo=GeoPoint(lat=43.06, lng=141.35))
TOKYO = Destination(name="Tokyo", geo=GeoPoint(lat=35.68, lng=139.69))
# About 500 km from Tokyo, still ground-range (under 900 km) and over the 300 km a bus estimate covers.
HIROSHIMA = Destination(name="Hiroshima", geo=GeoPoint(lat=34.39, lng=132.46))


def _options(
    registry, origin: Destination, destination: Destination
) -> list[TicketOption]:
    leg = TransportLeg(
        id="leg_x",
        from_place=origin.name,
        to_place=destination.name,
        depart_date=DAY,
        kind="between",
    )
    toolbox = Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t")
    return asyncio.run(
        leg_options(
            leg,
            origin,
            destination,
            registry=registry,
            toolbox=toolbox,
            party_size=2,
            currency="HKD",
        )
    )


def _registry(settings: Settings) -> SandboxRegistry:
    return SandboxRegistry(
        settings.model_copy(update={"enable_flights": True}), "Kyoto"
    )


def test_a_short_leg_without_a_transit_bus_gets_one_estimated_bus_or_shuttle(
    settings: Settings,
) -> None:
    options = _options(_registry(settings), KYOTO, OSAKA)

    modes = [o.mode for o in options]
    assert "ground" in modes and modes.count("bus") == 1
    bus = next(o for o in options if o.mode == "bus")
    assert bus.name == "Bus or shuttle (estimate)"
    assert bus.estimated is True and bus.price is None
    assert bus.depart_at == datetime(2026, 12, 5, 9, 0)
    assert (
        bus.booking_url.startswith("https://www.google.com/maps/dir/?")
        and "travelmode=transit" in bus.booking_url
    )
    assert all(o.estimated for o in options)


def test_the_bus_takes_the_drive_time_times_1_3(settings: Settings) -> None:
    options = _options(_registry(settings), KYOTO, OSAKA)

    km = haversine_km(KYOTO.geo.lat, KYOTO.geo.lng, OSAKA.geo.lat, OSAKA.geo.lng)
    drive = estimate_leg(KYOTO.geo, OSAKA.geo, "drive")
    bus = next(o for o in options if o.mode == "bus")
    assert km < 300
    assert bus.duration_minutes == round(drive.duration_minutes * 1.3)
    assert (
        bus.arrive_at is not None
        and (bus.arrive_at - bus.depart_at).total_seconds() / 60 == bus.duration_minutes
    )


def test_a_bus_estimate_takes_the_time_of_the_drive_option_when_there_is_one(
    settings: Settings,
) -> None:
    class Routed:
        name = "google-routes"
        available = True

        async def leg(self, origin, destination, *, mode="walk"):
            km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
            return RouteLeg(
                from_geo=origin,
                to_geo=destination,
                duration_minutes=100 if mode == "drive" else 70,
                distance_km=km,
                mode=mode,
                source="google-routes",
                steps=[],
            )

    registry = _registry(settings)
    registry.routes = [Routed()]

    options = _options(registry, KYOTO, OSAKA)

    drive = next(o for o in options if o.mode == "drive")
    bus = next(o for o in options if o.mode == "bus")
    assert drive.duration_minutes == 100 and bus.duration_minutes == 130


def test_a_500_km_leg_gets_no_estimated_bus(settings: Settings) -> None:
    options = _options(_registry(settings), TOKYO, HIROSHIMA)

    assert "bus" not in {o.mode for o in options}


def test_a_leg_where_transit_already_returned_a_bus_gets_no_second_one(
    settings: Settings,
) -> None:
    class Transit:
        name = "google-routes"
        available = True

        async def leg(self, origin, destination, *, mode="walk"):
            km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
            steps = (
                [
                    RouteStep(
                        instruction="Bus 7", mode="transit", minutes=60, vehicle="BUS"
                    )
                ]
                if mode == "transit"
                else []
            )
            return RouteLeg(
                from_geo=origin,
                to_geo=destination,
                duration_minutes=80 if mode == "transit" else 50,
                distance_km=km,
                mode=mode,
                source="google-routes",
                steps=steps,
            )

    registry = _registry(settings)
    registry.routes = [Transit()]

    options = _options(registry, KYOTO, OSAKA)

    assert [o.mode for o in options].count("bus") == 1
    assert next(o for o in options if o.mode == "bus").name == "Bus (estimate)"


def test_an_estimate_is_never_called_a_shuttle_and_only_a_named_vehicle_would_be() -> (
    None
):
    ground = RouteLeg(
        from_geo=KYOTO.geo,
        to_geo=OSAKA.geo,
        duration_minutes=60,
        distance_km=40.0,
        mode="transit",
        source="google-routes",
        steps=[RouteStep(instruction="x", mode="transit", minutes=60, vehicle="TRAM")],
    )

    assert ground_mode(ground) == "train"
    assert ground_mode(ground.model_copy(update={"steps": []})) == "ground"
