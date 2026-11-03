"""A bare name must not resolve to an obscure village that happens to share it.

Open-Meteo's geocoder indexes populated places, not countries, so "Korea" comes back
as Koréa in Côte d'Ivoire (population 3,012). A trip to Korea was then planned in West
Africa. The chain must prefer a source that knows the prominent match.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import httpx

from app.application.agents import AgentContext, AttractionAgent
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import (
    AgentStatus,
    DateRange,
    FailureKind,
    GeoLocation,
    GeoPoint,
    PlaceCandidate,
    SourceRef,
    TripContext,
)
from app.infrastructure.http import use_transport
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.geocoding import GeocoderChain, NominatimGeocoder, OpenMeteoGeocoder

KOREA_VILLAGES = [
    {"name": "Koréa", "country_code": "CI", "country": "Ivory Coast", "admin1": "Sassandra-Marahoue",
     "feature_code": "PPL", "population": 3012, "latitude": 6.82523, "longitude": -6.64787,
     "timezone": "Africa/Abidjan"},
    {"name": "Korea", "country_code": "SS", "country": "South Sudan", "feature_code": "PPL",
     "latitude": 4.9, "longitude": 31.6, "timezone": "Africa/Juba"},
    {"name": "Korea", "country_code": "IN", "country": "India", "feature_code": "PPL",
     "latitude": 22.4, "longitude": 82.6, "timezone": "Asia/Kolkata"},
]
SOUTH_KOREA_OSM = [
    {"lat": "36.638392", "lon": "127.6961188", "display_name": "대한민국",
     "address": {"country": "대한민국", "country_code": "kr"}},
]
SEOUL = [
    {"name": "Seoul", "country_code": "KR", "country": "South Korea", "admin1": "Seoul",
     "feature_code": "PPLC", "population": 10349312, "latitude": 37.566, "longitude": 126.9784,
     "timezone": "Asia/Seoul"},
]


class _Geo:
    def __init__(self, open_meteo: list[dict], nominatim: list[dict]):
        self.open_meteo = open_meteo
        self.nominatim = nominatim
        self.hosts: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": self.open_meteo})
        if request.url.host == "nominatim.openstreetmap.org":
            return httpx.Response(200, json=self.nominatim)
        return httpx.Response(404)

    def geocode(self, settings: Settings, name: str):
        chain = GeocoderChain([OpenMeteoGeocoder(settings), NominatimGeocoder(settings)])
        with use_transport(httpx.MockTransport(self.handler)):
            return asyncio.run(chain.geocode(name))


def test_bare_country_name_is_not_resolved_to_a_village(settings: Settings) -> None:
    geo = _Geo(KOREA_VILLAGES, SOUTH_KOREA_OSM)
    found = geo.geocode(settings, "Korea")
    assert found is not None
    assert found.country_code == "KR"
    assert 33 < found.geo.lat < 39 and 124 < found.geo.lng < 131


def test_a_prominent_open_meteo_match_is_used_without_asking_further(settings: Settings) -> None:
    geo = _Geo(SEOUL, SOUTH_KOREA_OSM)
    found = geo.geocode(settings, "Seoul")
    assert found is not None and found.country_code == "KR" and found.timezone == "Asia/Seoul"
    assert geo.hosts == ["geocoding-api.open-meteo.com"]


def test_the_village_is_kept_only_when_no_other_source_answers(settings: Settings) -> None:
    geo = _Geo(KOREA_VILLAGES, [])
    found = geo.geocode(settings, "Korea")
    assert found is not None and found.country_code == "CI"


class _GuideWithoutCoordinates:
    """A place source whose listings carry no coordinates, so each one must be geocoded."""

    name = "guide"
    available = True

    async def search(self, **kwargs):
        if kwargs.get("category") != "attraction":
            return []
        ref = SourceRef(title="Kyoto guide", url="https://example.com/kyoto", source="guide")
        return [PlaceCandidate(name=name, source_refs=[ref]) for name in ("Kinkaku-ji", "Nijo Castle")]


class _GeocoderDown:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.asked: list[str] = []

    async def geocode(self, name: str, **_):
        self.asked.append(name)
        raise self.error


def _attractions_with(geocoder: object, settings: Settings):
    registry = SimpleNamespace(places=[_GuideWithoutCoordinates()], notes=[], geocoder=geocoder, web=[], web_deep=[])
    start = date.today() + timedelta(days=30)
    request = AgentContext(
        context=TripContext(session_id="s", destination="Kyoto", start_date=start, end_date=start + timedelta(days=2)),
        destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=35.0116, lng=135.7681)),
        date_range=DateRange(start=start, end=start + timedelta(days=2)),
        request_id="req_b8",
        toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_b8"),
        settings=settings,
    )
    return asyncio.run(AttractionAgent(registry, settings).run(request))


def test_geocoder_network_failure_is_a_retryable_gap(settings: Settings) -> None:
    geocoder = _GeocoderDown(httpx.ConnectError("connection refused"))

    result = _attractions_with(geocoder, settings)

    assert result.status is AgentStatus.UNAVAILABLE
    assert len(geocoder.asked) == 2, "every place was asked about"
    gaps = [gap for gap in result.failures if gap.section == "attractions"]
    # A geocoder that cannot be reached has not said the places do not exist.
    assert [(gap.kind, gap.retryable) for gap in gaps] == [(FailureKind.NETWORK, True)]
    assert "could not be checked" in gaps[0].message
    assert "none could be located" not in gaps[0].message
