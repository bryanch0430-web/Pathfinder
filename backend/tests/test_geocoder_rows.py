"""A geocoder answer of the wrong shape is that source failing, and the next source is still asked.

A source can answer HTTP 200 with a body this service cannot read: a row without coordinates, a latitude that is
null, text or out of range, a list of rows that holds strings. That used to escape the chain as a raw `KeyError`,
`TypeError`, `ValueError` or `AttributeError`, so the Nominatim answer behind it was never used and the turn ended
in `internal_error`. It is now a typed failure of kind `parse`, whose message is ours and whose detail (for the
trace only) says what was wrong.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import FailureKind
from app.infrastructure.http import use_transport
from app.infrastructure.providers import geocoding
from app.infrastructure.providers.amap import AmapGeocoder
from app.infrastructure.providers.geocoding import GeocoderChain, GoogleGeocoder, NominatimGeocoder, OpenMeteoGeocoder

NOMINATIM_KYOTO = [{"lat": "35.0116", "lon": "135.7681", "display_name": "Kyoto, Japan", "address": {"country_code": "jp"}}]
GOOGLE_ZERO = {"status": "ZERO_RESULTS", "results": []}
#: The words of a source that must never reach a message: they stand for a key or an address in its answer.
SECRET = "SECRET-IN-THE-ANSWER"


def _google(location: Any) -> dict[str, Any]:
    return {"status": "OK", "results": [{"formatted_address": SECRET, "geometry": {"location": location}}]}


def _meteo(**row: Any) -> dict[str, Any]:
    return {"results": [{"name": SECRET, "country_code": "JP", "population": 1_500_000, **row}]}


#: Answers of a source ahead of one that answers well (Nominatim, with Kyoto), each of the wrong shape.
AHEAD_OF_A_GOOD_ONE: list[tuple[str, dict[str, Any]]] = [
    ("open-meteo row without latitude", {"meteo": _meteo(longitude=135.7)}),
    ("open-meteo row without longitude", {"meteo": _meteo(latitude=35.0)}),
    ("open-meteo latitude null", {"meteo": _meteo(latitude=None, longitude=135.7)}),
    ("open-meteo latitude text", {"meteo": _meteo(latitude="abc", longitude=135.7)}),
    ("open-meteo latitude out of range", {"meteo": _meteo(latitude=999, longitude=135.7)}),
    ("open-meteo results is a string", {"meteo": {"results": SECRET}}),
    ("open-meteo results holds a string", {"meteo": {"results": [SECRET]}}),
    ("google lat is text", {"google": _google({"lat": "abc", "lng": 1})}),
    ("google lat out of range", {"google": _google({"lat": 123, "lng": 1})}),
    ("google results[0] is a string", {"google": {"status": "OK", "results": [SECRET]}}),
    ("google location is a string", {"google": _google(SECRET)}),
]

#: What the last source answers when the others know nothing: nobody is left to ask.
AT_THE_END: list[tuple[str, list[Any]]] = [
    ("nominatim row without lat", [{"display_name": SECRET, "lon": "1"}]),
    ("nominatim row without lon", [{"display_name": SECRET, "lat": "1"}]),
    ("nominatim lat text", [{"lat": SECRET, "lon": "1"}]),
    ("nominatim lat out of range", [{"lat": "123", "lon": "1"}]),
    ("nominatim rows hold a string", [SECRET]),
]


def _handler(google: Any = None, meteo: Any = None, nominatim: Any = None, amap: Any = None):
    def handle(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if "googleapis" in host:
            return httpx.Response(200, json=google if google is not None else GOOGLE_ZERO)
        if "amap" in host:
            return httpx.Response(200, json=amap if amap is not None else {"status": "1", "geocodes": []})
        if "open-meteo" in host:
            return httpx.Response(200, json=meteo if meteo is not None else {})
        if "nominatim" in host:
            return httpx.Response(200, json=nominatim if nominatim is not None else [])
        return httpx.Response(500, text="unexpected host " + host)

    return handle


@pytest.fixture()
def keyed(settings: Settings):
    geocoding._GEOCODE_CACHE.clear()
    yield settings.model_copy(update={"google_maps_api_key": "KEY-123", "amap_key": "AMAP-KEY", "http_retries": 0})
    geocoding._GEOCODE_CACHE.clear()


def _chain(settings: Settings, *, amap: bool = False) -> GeocoderChain:
    sources: list[Any] = [GoogleGeocoder(settings)]
    if amap:
        sources.append(AmapGeocoder(settings))
    return GeocoderChain([*sources, OpenMeteoGeocoder(settings), NominatimGeocoder(settings)])


def _geocode(chain: GeocoderChain, handler, name: str = "Kyoto"):
    with use_transport(httpx.MockTransport(handler)):
        return asyncio.run(chain.geocode(name))


def _failure(chain: GeocoderChain, handler) -> ProviderError:
    with pytest.raises(ProviderError) as raised:
        _geocode(chain, handler)
    return raised.value


@pytest.mark.parametrize(("label", "answers"), AHEAD_OF_A_GOOD_ONE, ids=[row[0] for row in AHEAD_OF_A_GOOD_ONE])
def test_a_source_that_answers_in_the_wrong_shape_does_not_stop_the_next_source(
    keyed: Settings, label: str, answers: dict[str, Any]
) -> None:
    found = _geocode(_chain(keyed), _handler(**answers, nominatim=NOMINATIM_KYOTO))

    assert found is not None and found.source == "nominatim" and found.name == "Kyoto, Japan"


@pytest.mark.parametrize(("label", "rows"), AT_THE_END, ids=[row[0] for row in AT_THE_END])
def test_when_the_last_source_cannot_be_read_the_chain_reports_a_parse_failure_in_our_words(
    keyed: Settings, label: str, rows: list[Any]
) -> None:
    error = _failure(_chain(keyed), _handler(nominatim=rows))

    assert error.kind is FailureKind.PARSE and not error.retryable
    assert error.source == "geocoder-chain"
    assert str(error) == "geocoding failed for all sources: nominatim (parse)"
    assert SECRET not in str(error) and "KEY-123" not in str(error)
    assert "nominatim" in error.detail and error.detail  # what was wrong, for the trace only


def test_every_source_that_cannot_be_read_is_named_in_the_chain_failure(keyed: Settings) -> None:
    error = _failure(
        _chain(keyed),
        _handler(google=_google({"lat": "abc", "lng": 1}), meteo=_meteo(longitude=135.7), nominatim=[SECRET]),
    )

    assert error.kind is FailureKind.PARSE and not error.retryable
    assert str(error) == "geocoding failed for all sources: google-geocoder (parse), open-meteo-geocoder (parse), nominatim (parse)"
    assert SECRET not in str(error)


def test_the_detail_says_what_the_row_lacked(keyed: Settings) -> None:
    error = _failure(_chain(keyed), _handler(meteo=_meteo(longitude=135.7), nominatim=[SECRET]))

    assert "open-meteo-geocoder" in error.detail and "KeyError: 'latitude'" in error.detail


def test_a_failure_asking_again_could_fix_decides_the_advice_even_when_another_source_cannot_be_read(
    keyed: Settings,
) -> None:
    error = _failure(
        _chain(keyed),
        _handler(
            google={"status": "OVER_QUERY_LIMIT", "error_message": SECRET},
            meteo=_meteo(longitude=135.7),
            nominatim=[SECRET],
        ),
    )

    assert error.kind is FailureKind.RATE_LIMIT and error.retryable
    assert "google-geocoder (rate_limit)" in str(error) and "open-meteo-geocoder (parse)" in str(error)
    assert SECRET not in str(error)


def test_a_wrong_shaped_amap_answer_does_not_stop_the_chain_either(keyed: Settings) -> None:
    handler = _handler(amap={"status": "1", "geocodes": SECRET}, nominatim=NOMINATIM_KYOTO)

    found = _geocode(_chain(keyed, amap=True), handler, name="Beijing, China")

    assert found is not None and found.source == "nominatim"


def test_a_cancelled_lookup_is_still_cancelled(keyed: Settings) -> None:
    """The chain records a source that cannot be read; it does not swallow a cancellation."""

    class Cancelling:
        name = "cancelling"

        async def geocode(self, name: str, *, country_hint: str = "") -> None:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(GeocoderChain([Cancelling()]).geocode("Kyoto"))


def test_a_valid_answer_is_read_as_before(keyed: Settings) -> None:
    meteo = _meteo(latitude=35.0116, longitude=135.7681, timezone="Asia/Tokyo", feature_code="PPLA")

    found = _geocode(_chain(keyed), _handler(meteo=meteo))

    assert found is not None and found.source == "open-meteo-geocoder"
    assert (found.geo.lat, found.geo.lng) == (35.0116, 135.7681)
