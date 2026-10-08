"""build_tool_gateway: mock providers wired from settings; vendors are provisional."""

from __future__ import annotations

import pytest

from backend.schemas.common import AgentName, PathName
from backend.schemas.tools import GeocodeRequest, ToolOperation, ToolOutcomeStatus
from backend.tests.tools.helpers import (
    TRACE,
    FakeAudit,
    FakeObservability,
    FakeSleep,
    forecast_json,
    make_settings,
)
from backend.tools.factory import build_tool_gateway
from backend.tools.providers.mock import FaultKind, FaultPlan
from backend.settings import Settings


async def test_factory_builds_mock_gateway_with_sleep_passthrough() -> None:
    sleep = FakeSleep()
    observability = FakeObservability()
    faults = (
        FaultPlan()
        .fail(ToolOperation.FORECAST, FaultKind.TIMEOUT)
        .fail_always(ToolOperation.GEOCODE, FaultKind.UNAVAILABLE, provider="mock-google")
    )
    gateway = build_tool_gateway(
        make_settings(), observability, FakeAudit(), faults=faults, sleep=sleep  # type: ignore[arg-type]
    )

    weather = gateway.scoped(path=PathName.PLAN, agent=AgentName.WEATHER, trace=TRACE)
    hotel = gateway.scoped(path=PathName.PLAN, agent=AgentName.HOTEL, trace=TRACE)
    forecast = await weather.call(forecast_json())
    geocode = await hotel.call(GeocodeRequest(query="Park Hyatt Tokyo", city="Tokyo"))

    assert forecast.status is ToolOutcomeStatus.OK and forecast.attempts == 2
    assert len(sleep.delays) == 1
    assert geocode.status is ToolOutcomeStatus.OK and geocode.provider == "mock-amap"
    assert [r.trace_id for r in observability.records] == [TRACE.trace_id] * 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"search_provider": "web"},
        {"maps_providers": ["google"]},
        {"maps_providers": ["mock", "amap"]},
        {"weather_provider": "live"},
        {"places_provider": "google"},
        {"ticket_provider": "live"},
    ],
)
def test_factory_vendor_selection_is_provisional(overrides: dict[str, object]) -> None:
    settings = Settings(_env_file=None, **overrides)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError, match="TODO\\(provisional\\)"):
        build_tool_gateway(settings, FakeObservability(), FakeAudit())  # type: ignore[arg-type]
