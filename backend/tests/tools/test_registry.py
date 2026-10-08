"""Registry dispatch and the maps fallback chain (Google, then AMap)."""

from __future__ import annotations

import asyncio

from backend.schemas.common import GeoPoint
from backend.schemas.tools import (
    FailureKind,
    GeocodeRequest,
    GeocodeResult,
    PlacesSearchRequest,
    ReservationCheckRequest,
    TicketSearchRequest,
    ToolOperation,
    ToolOutcomeStatus,
    WebSearchRequest,
)
from backend.tests.tools.helpers import TRAVEL_DAY, forecast_json, make_harness
from backend.tools.providers.mock import FaultKind, FaultPlan

GEOCODE = GeocodeRequest(query="Fushimi Inari Taisha", city="Kyoto")


async def test_registry_dispatches_every_operation() -> None:
    h = make_harness()
    scope = h.scope()
    requests = [
        WebSearchRequest(query="temples", destination="Kyoto"),
        GEOCODE,
        PlacesSearchRequest(destination="Kyoto", category="attraction"),
        TicketSearchRequest(origin="Tokyo", destination="Kyoto", travel_date=TRAVEL_DAY, modes=["train"]),
        ReservationCheckRequest(destination="Kyoto"),
    ]
    outcomes = [await scope.call(r) for r in requests]
    outcomes.append(await scope.call(forecast_json()))

    assert all(o.status is ToolOutcomeStatus.OK for o in outcomes)
    kinds = [o.payload.kind for o in outcomes if o.payload is not None]
    assert kinds == ["search", "geocode", "places", "tickets", "reservations", "forecast"]
    assert [o.provider for o in outcomes] == [
        "mock-search",
        "mock-google",
        "mock-places",
        "mock-tickets",
        "mock-tickets",
        "mock-weather",
    ]


async def test_maps_fallback_to_amap_when_google_down() -> None:
    faults = FaultPlan().fail_always(ToolOperation.GEOCODE, FaultKind.UNAVAILABLE, provider="mock-google")
    h = make_harness(faults=faults)

    outcome = await h.scope().call(GEOCODE)

    assert outcome.status is ToolOutcomeStatus.OK
    assert outcome.provider == "mock-amap"
    assert outcome.attempts == 1
    assert h.observability.records[0].provider == "mock-amap"


async def test_maps_transient_fault_on_google_falls_through_in_same_attempt() -> None:
    faults = FaultPlan().fail(ToolOperation.GEOCODE, FaultKind.SERVER_FAULT, provider="mock-google")
    h = make_harness(faults=faults)

    outcome = await h.scope().call(GEOCODE)

    assert outcome.status is ToolOutcomeStatus.OK and outcome.provider == "mock-amap"
    assert outcome.attempts == 1 and h.sleep.delays == []


async def test_maps_both_down_is_unavailable() -> None:
    faults = FaultPlan().fail_always(ToolOperation.GEOCODE, FaultKind.UNAVAILABLE)
    h = make_harness(faults=faults)

    outcome = await h.scope().call(GEOCODE)

    assert outcome.status is ToolOutcomeStatus.UNAVAILABLE
    assert outcome.failure is FailureKind.PERMANENT
    assert outcome.payload is None
    assert [m.call_count for m in h.providers.maps] == [1, 1]


async def test_maps_bad_request_not_passed_to_fallback() -> None:
    faults = FaultPlan().fail(ToolOperation.GEOCODE, FaultKind.BAD_REQUEST, provider="mock-google")
    h = make_harness(faults=faults)

    outcome = await h.scope().call(GEOCODE)

    assert outcome.status is ToolOutcomeStatus.VALIDATION_ERROR
    assert [m.call_count for m in h.providers.maps] == [1, 0]


class _HangingMaps:
    name = "hanging-google"

    async def geocode(self, request: GeocodeRequest) -> GeocodeResult:
        await asyncio.sleep(5)
        return GeocodeResult(query=request.query, location=GeoPoint(lat=0, lng=0), formatted_address="")


async def test_maps_hanging_primary_falls_back_within_fallback_timeout() -> None:
    h0 = make_harness()
    h = make_harness(maps=[_HangingMaps(), h0.providers.maps[1]], fallback_timeout_s=0.03)

    outcome = await h.scope().call(GEOCODE)

    assert outcome.status is ToolOutcomeStatus.OK
    assert outcome.provider == "mock-amap"
