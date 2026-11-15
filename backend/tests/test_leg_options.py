"""Transport options for one leg of a multi-city trip, and the legs a route makes. Offline: the sandbox sources."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import pytest

from app.application.toolbox import Toolbox
from app.application.transport_legs import (
    AIRPORT_MINUTES,
    build_legs,
    ground_mode,
    leg_options,
    rank_options,
)
from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import (
    DateRange,
    Destination,
    GeoPoint,
    Money,
    RouteLeg,
    RouteStep,
    TicketOption,
    TransportLeg,
    TripSegment,
)
from app.evaluation import SandboxRegistry
from app.infrastructure.observability import NoopTracer

DAY = date(2026, 12, 5)
KYOTO = Destination(name="Kyoto", geo=GeoPoint(lat=35.01, lng=135.77))
OSAKA = Destination(name="Osaka", geo=GeoPoint(lat=34.69, lng=135.50))
TOKYO = Destination(name="Tokyo", geo=GeoPoint(lat=35.68, lng=139.69))
SAPPORO = Destination(name="Sapporo", geo=GeoPoint(lat=43.06, lng=141.35))
HONG_KONG = Destination(name="Hong Kong", geo=GeoPoint(lat=22.32, lng=114.17))


def _search(
    registry, origin: Destination, destination: Destination, kind: str = "between"
) -> list[TicketOption]:
    leg = TransportLeg(
        id="leg_x",
        from_place=origin.name,
        to_place=destination.name,
        depart_date=DAY,
        kind=kind,
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


def _options(
    settings: Settings, origin: Destination, destination: Destination
) -> list[TicketOption]:
    settings = settings.model_copy(update={"enable_flights": True})
    return _search(SandboxRegistry(settings, "Kyoto"), origin, destination)


def test_a_short_hop_gets_a_rough_ground_estimate_and_no_flight(
    settings: Settings,
) -> None:
    options = _options(settings, KYOTO, OSAKA)

    # Part 3 of choose-and-lock: a leg under 300 km with no transit bus also gets one estimated bus-or-shuttle.
    assert [o.mode for o in options] == ["ground", "bus"]
    only = options[0]
    assert (
        only.estimated
        and only.price is None
        and only.selected
        and only.leg_id == "leg_x"
    )
    assert only.name == "Ground transport (rough estimate)"
    assert only.depart_at == datetime(2026, 12, 5, 9, 0)
    assert only.arrive_at == only.depart_at + timedelta(minutes=only.duration_minutes)


def test_a_medium_hop_has_flights_and_ground_ranked_by_door_to_door_time(
    settings: Settings,
) -> None:
    options = _options(settings, TOKYO, SAPPORO)

    assert {o.mode for o in options} == {"flight", "ground"}
    assert options[0].mode == "flight"
    assert [o.selected for o in options] == [True] + [False] * (len(options) - 1)
    assert all(o.leg_id == "leg_x" for o in options)


def test_a_long_hop_has_flights_only(settings: Settings) -> None:
    options = _options(settings, HONG_KONG, TOKYO)

    assert options and {o.mode for o in options} == {"flight"}
    assert not any(o.estimated for o in options)


def test_a_flight_source_that_fails_leaves_the_ground_options(
    settings: Settings,
) -> None:
    class Broken:
        name = "broken"
        available = True

        async def search(self, **_):
            raise ProviderError("down", source="broken")

    registry = SandboxRegistry(
        settings.model_copy(update={"enable_flights": True}), "Kyoto"
    )
    registry.flights = [Broken()]

    assert [o.mode for o in _search(registry, TOKYO, SAPPORO)] == ["ground"]


def test_flights_switched_off_are_not_searched(settings: Settings) -> None:
    registry = SandboxRegistry(
        settings.model_copy(update={"enable_flights": False}), "Kyoto"
    )

    assert _search(registry, HONG_KONG, TOKYO, "outbound") == []


def test_the_directions_link_carries_the_two_places_and_nothing_else(
    settings: Settings,
) -> None:
    link = _options(settings, KYOTO, OSAKA)[0].booking_url

    assert link.startswith(
        "https://www.google.com/maps/dir/?api=1&origin=Kyoto&destination=Osaka&travelmode="
    )
    assert "key=" not in link


def _ticket(
    ticket_id: str, mode: str, minutes: int | None, price: float | None
) -> TicketOption:
    return TicketOption(
        id=ticket_id,
        mode=mode,
        name=ticket_id,
        duration_minutes=minutes,
        price=Money(amount=price, currency="HKD") if price is not None else None,
    )


def test_options_are_ranked_by_door_to_door_time_then_price_with_no_price_last() -> (
    None
):
    ranked = rank_options(
        [
            _ticket("slow", "ground", 300, None),
            _ticket("flight_dear", "flight", 50, 900),  # 50 + airport time = 200
            _ticket("train_cheap", "train", 210, 100),
            _ticket("train_no_quote", "train", 210, None),
            _ticket("unknown", "ground", None, None),
        ]
    )

    assert [t.id for t in ranked] == [
        "flight_dear",
        "train_cheap",
        "train_no_quote",
        "slow",
        "unknown",
    ]
    assert AIRPORT_MINUTES == 150


def _segments(count: int) -> list[TripSegment]:
    places = [KYOTO, OSAKA, TOKYO][:count]
    return [
        TripSegment(
            id=f"seg_{n + 1}",
            destination=place,
            dates=DateRange(
                start=date(2026, 12, 3 + 2 * n), end=date(2026, 12, 5 + 2 * n)
            ),
        )
        for n, place in enumerate(places)
    ]


def test_legs_run_out_between_and_home() -> None:
    legs = build_legs(_segments(3), "Hong Kong", one_way=False)

    assert [
        (leg.id, leg.kind, leg.from_place, leg.to_place, leg.depart_date)
        for leg in legs
    ] == [
        ("leg_out", "outbound", "Hong Kong", "Kyoto", date(2026, 12, 3)),
        ("leg_1", "between", "Kyoto", "Osaka", date(2026, 12, 5)),
        ("leg_2", "between", "Osaka", "Tokyo", date(2026, 12, 7)),
        ("leg_home", "return", "Tokyo", "Hong Kong", date(2026, 12, 9)),
    ]


def test_a_one_way_trip_has_no_leg_home_and_no_origin_has_no_outbound_or_home() -> None:
    assert [leg.id for leg in build_legs(_segments(3), "Hong Kong", one_way=True)] == [
        "leg_out",
        "leg_1",
        "leg_2",
    ]
    assert [leg.id for leg in build_legs(_segments(2), "", one_way=False)] == ["leg_1"]


def _routed(*steps: tuple[str, int]) -> RouteLeg:
    return RouteLeg(
        from_geo=KYOTO.geo,
        to_geo=OSAKA.geo,
        duration_minutes=60,
        source="google-routes",
        steps=[
            RouteStep(mode="transit", minutes=minutes, vehicle=vehicle)
            for vehicle, minutes in steps
        ],
    )


@pytest.mark.parametrize(
    ("steps", "mode"),
    [
        ([("HEAVY_RAIL", 40), ("BUS", 10)], "train"),
        ([("BUS", 40), ("SUBWAY", 10)], "bus"),
        ([("", 40)], "ground"),
        ([], "ground"),
    ],
)
def test_the_longest_transit_step_decides_between_train_and_bus(steps, mode) -> None:
    assert ground_mode(_routed(*steps)) == mode


def test_the_routes_source_keeps_the_vehicle_of_a_transit_step(
    settings: Settings, monkeypatch
) -> None:
    from app.infrastructure.providers import routes

    async def fake(*_, **__):
        return {
            "routes": [
                {
                    "duration": "2400s",
                    "distanceMeters": 45000,
                    "legs": [
                        {
                            "steps": [
                                {
                                    "travelMode": "TRANSIT",
                                    "staticDuration": "2100s",
                                    "transitDetails": {
                                        "transitLine": {
                                            "vehicle": {"type": "HEAVY_RAIL"}
                                        }
                                    },
                                },
                                {"travelMode": "WALK", "staticDuration": "300s"},
                            ]
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(routes, "request_json", fake)
    source = routes.GoogleRoutesSource(
        settings.model_copy(
            update={"google_maps_api_key": "test-key", "enable_routes": True}
        )
    )

    leg = asyncio.run(source.leg(KYOTO.geo, OSAKA.geo, mode="transit"))

    assert [step.vehicle for step in leg.steps] == ["HEAVY_RAIL", ""]
    assert ground_mode(leg) == "train"
    assert "vehicle" not in leg.model_dump(mode="json")["steps"][0]
