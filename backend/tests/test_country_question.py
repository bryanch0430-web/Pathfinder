"""A bare country or first-level region, for four days or more, asks which cities before planning."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from multi_city_rig import multi_city_rig, route_context

from app.application.country_check import country_question
from app.config import Settings
from app.domain.models import GeoLocation, GeoPoint
from app.eval_metering import RunLedger
from app.infrastructure.http import use_transport
from app.infrastructure.providers.geocoding import GoogleGeocoder, OpenMeteoGeocoder


def _place(kind: str) -> GeoLocation:
    return GeoLocation(
        name="X",
        country_code="JP",
        timezone="Asia/Tokyo",
        geo=GeoPoint(lat=35.0, lng=135.0),
        kind=kind,
    )


# --- the kind of place a geocoder answer states ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("PCLI", "country"),
        ("PCLD", "country"),
        ("ADM1", "region"),
        ("PPLA", "city"),
        ("PPLC", "city"),
        ("PPL", "city"),
    ],
)
def test_open_meteo_feature_codes_give_the_kind(
    settings: Settings, code: str, kind: str
) -> None:
    row = {
        "name": "Japan",
        "country_code": "JP",
        "latitude": 36.0,
        "longitude": 138.0,
        "feature_code": code,
        "population": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [row]})

    with use_transport(httpx.MockTransport(handler)):
        found = asyncio.run(OpenMeteoGeocoder(settings).geocode("Japan"))

    assert found is not None and found.kind == kind


def test_an_answer_with_no_feature_code_has_an_unknown_kind(settings: Settings) -> None:
    row = {
        "name": "Kyoto",
        "country_code": "JP",
        "latitude": 35.0,
        "longitude": 135.7,
        "population": 1_500_000,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [row]})

    with use_transport(httpx.MockTransport(handler)):
        found = asyncio.run(OpenMeteoGeocoder(settings).geocode("Kyoto"))

    assert found is not None and found.kind == "unknown"


@pytest.mark.parametrize(
    ("types", "kind"),
    [
        (["country", "political"], "country"),
        (["administrative_area_level_1", "political"], "region"),
        (["locality", "political"], "city"),
    ],
)
def test_google_result_types_give_the_kind(
    settings: Settings, types: list[str], kind: str
) -> None:
    keyed = settings.model_copy(update={"google_places_api_key": "FAKE-KEY"})
    body = {
        "status": "OK",
        "results": [
            {
                "formatted_address": "Somewhere",
                "types": types,
                "geometry": {"location": {"lat": 35.0, "lng": 135.0}},
                "address_components": [{"types": ["country"], "short_name": "JP"}],
            }
        ],
    }

    with use_transport(
        httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ):
        found = asyncio.run(GoogleGeocoder(keyed).geocode(f"Somewhere {kind}"))

    assert found is not None and found.kind == kind


# --- the question -------------------------------------------------------------------------------------------------------


def test_the_question_names_the_place_and_gives_an_example() -> None:
    asked = country_question(_place("country"), "Japan", days=14)

    assert asked is not None
    assert asked.question == (
        "Japan is a whole country/region. Which cities do you want to visit, and in what order? "
        "For example: Tokyo 4 nights, then Kyoto 3."
    )
    assert asked.slots == ["destination"]


@pytest.mark.parametrize(
    ("kind", "days", "asks"),
    [
        ("country", 14, True),
        ("region", 4, True),
        ("country", 3, False),
        ("country", 2, False),
        ("city", 14, False),
        ("unknown", 14, False),
    ],
)
def test_only_a_country_or_region_of_four_days_or_more_asks(
    kind: str, days: int, asks: bool
) -> None:
    assert (country_question(_place(kind), "Somewhere", days=days) is not None) is asks


# --- the turn -----------------------------------------------------------------------------------------------------------


class _Kinds:
    name = "kinds-geocoder"

    def __init__(self, kinds: dict[str, str]):
        self.kinds = kinds

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation:
        from app.evaluation import SandboxGeocoder

        found = await SandboxGeocoder("Kyoto").geocode(name)
        return found.model_copy(
            update={"name": name, "kind": self.kinds.get(name, "city")}
        )


def _turn(settings: Settings, destination: str, days: int) -> list[Any]:
    settings = settings.model_copy(
        update={
            "preplan_budget_seconds": 15,
            "agent_timeout_seconds": 8,
            "enable_flights": True,
        }
    )

    async def main():
        ledger = RunLedger()
        try:
            rig = multi_city_rig(settings, ledger)
            rig.plan_path.registry.geocoder = _Kinds({"Japan": "country"})
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-country-q")
            context = route_context(session.id, [(destination, None)], nights=days - 1)
            context.destination = destination
            context.origin = "Hong Kong"
            ledger.store.save_context(context)
            return [
                e
                async for e in rig.turns.handle_turn(
                    session_id=session.id, message="Plan this trip"
                )
            ]
        finally:
            ledger.close()

    return asyncio.run(main())


def test_japan_for_14_days_asks_instead_of_planning(settings: Settings) -> None:
    events = _turn(settings, "Japan", 14)

    kinds = [e.event for e in events]
    assert "clarify" in kinds and "plan" not in kinds
    asked = next(e.data for e in events if e.event == "clarify")
    assert asked["question"].startswith("Japan is a whole country/region. Which cities")


def test_japan_for_2_days_plans_as_before(settings: Settings) -> None:
    events = _turn(settings, "Japan", 2)

    assert "plan" in [e.event for e in events] and "clarify" not in [
        e.event for e in events
    ]


def test_kyoto_for_14_days_plans_as_before(settings: Settings) -> None:
    events = _turn(settings, "Kyoto", 14)

    assert "plan" in [e.event for e in events] and "clarify" not in [
        e.event for e in events
    ]
