"""Scenario 10: validation errors are structured, never sent to a provider, never retried."""

from __future__ import annotations

import json

import pytest

from backend.schemas.common import ToolName
from backend.schemas.tools import (
    FailureKind,
    FieldIssue,
    ForecastRequest,
    ToolOperation,
    ToolOutcomeStatus,
)
from backend.tests.tools.helpers import ScriptedWeather, forecast_json, make_harness
from backend.tools.providers.base import ProviderBadRequest
from backend.tools.providers.mock import FaultKind, FaultPlan


@pytest.mark.parametrize("free_text", ["12 April", "next Friday"])
async def test_s10_free_text_date_is_validation_error_not_sent(free_text: str) -> None:
    weather = ScriptedWeather()
    h = make_harness(weather=weather)
    raw = forecast_json(start_date=free_text)

    outcome = await h.scope().call(raw)

    assert outcome.status is ToolOutcomeStatus.VALIDATION_ERROR
    assert outcome.failure is FailureKind.VALIDATION
    assert outcome.attempts == 0
    assert outcome.operation is ToolOperation.FORECAST and outcome.tool is ToolName.WEATHER
    assert [i.loc for i in outcome.issues] == ["start_date"]
    assert outcome.issues[0].kind.startswith("date")
    assert weather.calls == 0 and h.total_provider_calls() == 0
    assert h.sleep.delays == []
    [record] = h.observability.records
    assert record.raw_request == raw
    assert record.status is ToolOutcomeStatus.VALIDATION_ERROR
    assert record.issues == outcome.issues


async def test_s10_invalid_json_is_one_whole_payload_issue() -> None:
    weather = ScriptedWeather()
    h = make_harness(weather=weather)

    outcome = await h.scope().call('{"operation": "forecast", "location": ')

    assert outcome.status is ToolOutcomeStatus.VALIDATION_ERROR
    assert outcome.failure is FailureKind.VALIDATION
    assert len(outcome.issues) == 1
    assert outcome.issues[0].loc == "" and outcome.issues[0].kind == "json_invalid"
    assert outcome.operation is None and outcome.tool is None
    assert outcome.attempts == 0 and weather.calls == 0


async def test_s10_missing_required_field() -> None:
    weather = ScriptedWeather()
    h = make_harness(weather=weather)
    body = json.loads(forecast_json())
    del body["end_date"]

    outcome = await h.scope().call(json.dumps(body))

    assert outcome.status is ToolOutcomeStatus.VALIDATION_ERROR
    assert [(i.loc, i.kind) for i in outcome.issues] == [("end_date", "missing")]
    assert outcome.attempts == 0 and weather.calls == 0


async def test_s10_extra_field_is_rejected() -> None:
    weather = ScriptedWeather()
    h = make_harness(weather=weather)

    outcome = await h.scope().call(forecast_json(units="metric"))

    assert outcome.status is ToolOutcomeStatus.VALIDATION_ERROR
    assert [(i.loc, i.kind) for i in outcome.issues] == [("units", "extra_forbidden")]
    assert outcome.attempts == 0 and weather.calls == 0


async def test_s10_cross_field_rule_is_whole_payload_issue() -> None:
    h = make_harness()

    outcome = await h.scope().call(forecast_json(start_date="2026-04-14", end_date="2026-04-12"))

    assert outcome.status is ToolOutcomeStatus.VALIDATION_ERROR
    assert outcome.issues[0].loc == ""
    assert h.total_provider_calls() == 0


async def test_s10_provider_bad_request_is_validation_error_without_retry() -> None:
    issue = FieldIssue(loc="location", message="unknown location", kind="unknown_location")
    weather = ScriptedWeather(ProviderBadRequest([issue]))
    h = make_harness(weather=weather)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.VALIDATION_ERROR
    assert outcome.failure is FailureKind.VALIDATION
    assert outcome.issues == [issue]
    assert outcome.attempts == 1
    assert weather.calls == 1
    assert h.sleep.delays == []


async def test_s10_mock_bad_request_fault_not_retried() -> None:
    faults = FaultPlan().fail(ToolOperation.FORECAST, FaultKind.BAD_REQUEST)
    h = make_harness(faults=faults)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.VALIDATION_ERROR
    assert outcome.attempts == 1 and h.providers.weather.call_count == 1
    assert outcome.issues and outcome.issues[0].kind == "provider_bad_request"


async def test_s10_repaired_request_then_succeeds() -> None:
    """The gateway never resends a bad request; the caller (model repair) sends a new one."""
    h = make_harness()
    scope = h.scope()

    bad = await scope.call(forecast_json(start_date="12 April"))
    good = await scope.call(forecast_json())

    assert bad.status is ToolOutcomeStatus.VALIDATION_ERROR
    assert good.status is ToolOutcomeStatus.OK
    assert isinstance(good.request, ForecastRequest)
    assert h.providers.weather.call_count == 1
    assert [o.call_id for o in scope.outcomes] == [bad.call_id, good.call_id]
    assert all(o.call_id.startswith("tc_") and len(o.call_id) == 15 for o in scope.outcomes)
