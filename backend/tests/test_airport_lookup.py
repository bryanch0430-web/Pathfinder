"""A city with no entry in the airport table gets the nearest large or medium airport from a bundled list, and the table
stays the override. Offline: nothing here may open a network connection."""

from __future__ import annotations

import asyncio
import socket
from datetime import date
from pathlib import Path

import pytest

from app.application.toolbox import Toolbox
from app.application.transport_legs import leg_options
from app.config import Settings
from app.domain.models import Destination, GeoPoint, TransportLeg
from app.evaluation import SandboxRegistry
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers import flights
from app.infrastructure.providers.flights import (
    AIRPORTS,
    airport_for,
    airport_near,
    resolve_airport,
)

KANAZAWA = GeoPoint(lat=36.5613, lng=136.6562)  # about 36 km from Komatsu
HIROSHIMA = GeoPoint(lat=34.3853, lng=132.4553)
SAPPORO = GeoPoint(lat=43.0621, lng=141.3544)
MID_PACIFIC = GeoPoint(lat=0.0, lng=-140.0)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("a network connection was opened")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.mark.parametrize(
    ("place", "geo", "code"),
    [
        ("Kanazawa", KANAZAWA, "KMQ"),
        ("Hiroshima", HIROSHIMA, "HIJ"),
        ("Sapporo", SAPPORO, "CTS"),
    ],
)
def test_a_city_not_in_the_table_gets_its_nearest_airport(
    place: str, geo: GeoPoint, code: str
) -> None:
    assert resolve_airport(place) is None
    assert airport_for(place, geo) == code


def test_a_place_with_no_airport_within_120_km_gets_none() -> None:
    assert airport_near(MID_PACIFIC.lat, MID_PACIFIC.lng) is None
    assert airport_for("Nowhere", MID_PACIFIC) is None
    assert airport_for("Nowhere", None) is None


def test_the_search_radius_is_120_km_unless_another_is_given() -> None:
    assert airport_near(KANAZAWA.lat, KANAZAWA.lng) == "KMQ"
    assert airport_near(KANAZAWA.lat, KANAZAWA.lng, max_km=10) is None


def test_a_large_airport_in_range_comes_before_a_nearer_medium_one() -> None:
    iwakuni = next(row for row in flights._bundled_airports() if row[0] == "IWK")
    assert iwakuni[3] == "medium"
    # Iwakuni is nearer to Hiroshima than Hiroshima airport is, but it is a medium airport.
    assert flights.haversine_km(HIROSHIMA.lat, HIROSHIMA.lng, iwakuni[1], iwakuni[2]) < flights.haversine_km(
        HIROSHIMA.lat, HIROSHIMA.lng, 34.4361, 132.919
    )
    assert airport_near(HIROSHIMA.lat, HIROSHIMA.lng) == "HIJ"
    # With no large airport in range, the nearest medium one is used.
    assert airport_near(iwakuni[1], iwakuni[2], max_km=30) == "IWK"


def test_every_city_in_the_table_resolves_as_it_did_and_the_table_wins_over_the_coordinates() -> (
    None
):
    for city, codes in AIRPORTS.items():
        assert resolve_airport(city) == codes[0]
        assert airport_for(city, MID_PACIFIC) == codes[0], city
        assert airport_for(city, KANAZAWA) == codes[0], city


def test_an_airport_code_from_the_list_is_accepted_as_a_code() -> None:
    assert resolve_airport("KMQ") == "KMQ"
    assert resolve_airport("kmq") == "KMQ"
    assert resolve_airport("HKG") == "HKG"


def test_the_bundled_file_holds_only_large_and_medium_airports_and_names_its_source_and_licence() -> (
    None
):
    path = Path(flights.__file__).with_name("airports.csv")
    lines = path.read_text(encoding="utf-8").splitlines()
    comment = "\n".join(line for line in lines if line.startswith("#"))
    rows = [line.split(",") for line in lines if not line.startswith("#")][1:]

    assert "OurAirports" in comment and "Unlicense" in comment and "https://" in comment
    assert len(rows) > 1000 and {row[-1] for row in rows} == {"large", "medium"}
    assert len({row[0] for row in rows}) == len(rows)


class Recorder:
    name = "recorder"
    available = True

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    async def search(self, *, origin, destination, **_):
        self.asked.append((origin, destination))
        return []


def _asked(
    settings: Settings, origin: Destination, destination: Destination
) -> tuple[str, str]:
    registry = SandboxRegistry(
        settings.model_copy(update={"enable_flights": True}), "Kyoto"
    )
    recorder = Recorder()
    registry.flights = [recorder]
    leg = TransportLeg(
        id="leg_x",
        from_place=origin.name,
        to_place=destination.name,
        depart_date=date(2026, 12, 5),
        kind="outbound",
    )
    toolbox = Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t")
    asyncio.run(
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
    return recorder.asked[0]


def test_a_leg_to_a_city_not_in_the_table_searches_flights_to_its_nearest_airport(
    settings: Settings,
) -> None:
    hong_kong = Destination(name="Hong Kong", geo=GeoPoint(lat=22.32, lng=114.17))

    assert _asked(settings, hong_kong, Destination(name="Sapporo", geo=SAPPORO)) == (
        "Hong Kong",
        "CTS",
    )
    assert _asked(
        settings, hong_kong, Destination(name="Hiroshima", geo=HIROSHIMA)
    ) == ("Hong Kong", "HIJ")


def test_a_leg_between_cities_in_the_table_is_searched_by_name_as_before(
    settings: Settings,
) -> None:
    hong_kong = Destination(name="Hong Kong", geo=GeoPoint(lat=22.32, lng=114.17))
    tokyo = Destination(name="Tokyo", geo=GeoPoint(lat=35.68, lng=139.69))

    assert _asked(settings, hong_kong, tokyo) == ("Hong Kong", "Tokyo")


def test_a_city_with_no_coordinates_and_no_table_entry_is_searched_by_name_as_before(
    settings: Settings,
) -> None:
    hong_kong = Destination(name="Hong Kong", geo=GeoPoint(lat=22.32, lng=114.17))

    assert _asked(settings, hong_kong, Destination(name="Sapporo")) == (
        "Hong Kong",
        "Sapporo",
    )
