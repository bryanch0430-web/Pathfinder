"""Scenarios 11 and 12: transient failures retried with backoff + full jitter inside a time
budget; still unusable -> UNAVAILABLE (never filled from model knowledge)."""

from __future__ import annotations

import random

import pytest

from backend.schemas.tools import FailureKind, ToolOperation, ToolOutcomeStatus
from backend.tests.tools.helpers import ScriptedWeather, forecast_json, make_harness, make_settings
from backend.tools.errors import classify
from backend.tools.providers.base import (
    ProviderBadRequest,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
    ProviderUnavailable,
)
from backend.tools.providers.mock import FaultKind, FaultPlan
from backend.tools.retry import RetryPolicy

# ---- classification ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (ProviderTimeout(), FailureKind.TIMEOUT),
        (TimeoutError(), FailureKind.TIMEOUT),
        (ProviderRateLimited(), FailureKind.RATE_LIMIT),
        (ProviderServerError(), FailureKind.SERVER_FAULT),
        (ProviderBadRequest([]), FailureKind.VALIDATION),
        (ProviderUnavailable(), FailureKind.PERMANENT),
        (RuntimeError("bug"), FailureKind.SERVER_FAULT),
    ],
)
def test_classify(exc: Exception, kind: FailureKind) -> None:
    assert classify(exc) is kind


# ---- scenario 11 ---------------------------------------------------------------------------------


async def test_s11_timeout_retried_with_backoff() -> None:
    faults = FaultPlan().fail(ToolOperation.FORECAST, FaultKind.TIMEOUT)
    h = make_harness(faults=faults)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.OK
    assert outcome.attempts == 2
    assert len(h.sleep.delays) == 1
    assert 0.0 <= h.sleep.delays[0] <= h.settings.tool_backoff_base_s
    assert outcome.payload is not None and outcome.payload.kind == "forecast"
    assert h.observability.records[0].attempts == 2


async def test_s11_rate_limit_honours_retry_after() -> None:
    faults = FaultPlan().fail(ToolOperation.FORECAST, FaultKind.RATE_LIMIT)
    h = make_harness(faults=faults)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.OK and outcome.attempts == 2
    assert h.sleep.delays[0] >= 0.05  # mock retry_after_s


async def test_s11_retry_after_wins_over_smaller_backoff_cap() -> None:
    weather = ScriptedWeather(ProviderRateLimited(retry_after_s=0.5))
    h = make_harness(weather=weather)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.OK
    # The first backoff cap is base (0.2 s); the provider asked for 0.5 s and got it.
    assert h.sleep.delays == [0.5]


async def test_s11_server_fault_twice_then_success() -> None:
    faults = FaultPlan().fail(ToolOperation.FORECAST, FaultKind.SERVER_FAULT, FaultKind.SERVER_FAULT)
    h = make_harness(faults=faults)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.OK
    assert outcome.attempts == 3
    assert len(h.sleep.delays) == 2
    assert h.providers.weather.call_count == 3


async def test_s11_full_jitter_with_growing_caps() -> None:
    settings = make_settings(
        tool_max_attempts=5, tool_backoff_base_s=0.2, tool_backoff_max_s=2.0, tool_retry_budget_s=100.0
    )
    faults = FaultPlan().fail_always(ToolOperation.FORECAST, FaultKind.SERVER_FAULT)
    h = make_harness(faults=faults, settings=settings, seed=1234)

    outcome = await h.scope().call(forecast_json())

    policy = RetryPolicy.from_settings(settings)
    caps = [policy.backoff_cap(n) for n in range(1, 5)]
    expected_rng = random.Random(1234)
    expected = [expected_rng.uniform(0.0, cap) for cap in caps]
    assert caps == [0.2, 0.4, 0.8, 1.6]
    assert h.sleep.delays == expected  # drawn uniformly in [0, cap] from the injected rng
    assert all(0.0 <= d <= cap for d, cap in zip(h.sleep.delays, caps, strict=True))
    assert len(set(h.sleep.delays)) == len(h.sleep.delays)  # jittered, not fixed
    assert outcome.status is ToolOutcomeStatus.UNAVAILABLE and outcome.attempts == 5


def test_s11_backoff_cap_is_bounded_by_max() -> None:
    policy = RetryPolicy(max_attempts=10, base_s=0.2, max_s=2.0, attempt_timeout_s=4.0, budget_s=8.0)
    assert [policy.backoff_cap(n) for n in (1, 2, 3, 4, 5, 6, 200)] == [0.2, 0.4, 0.8, 1.6, 2.0, 2.0, 2.0]
    assert policy.with_budget(3.0).budget_s == 3.0
    assert policy.with_budget(30.0).budget_s == 8.0


async def test_s11_budget_exhausted_without_oversleeping() -> None:
    settings = make_settings(
        tool_max_attempts=50, tool_backoff_base_s=0.2, tool_backoff_max_s=2.0, tool_retry_budget_s=0.5
    )
    faults = FaultPlan().fail_always(ToolOperation.FORECAST, FaultKind.TIMEOUT)
    h = make_harness(faults=faults, settings=settings, seed=99)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.UNAVAILABLE
    assert outcome.failure is FailureKind.BUDGET_EXHAUSTED
    assert outcome.attempts < 50
    assert sum(h.sleep.delays) < 0.5
    assert outcome.payload is None


async def test_s11_agent_budget_caps_retry_budget() -> None:
    weather = ScriptedWeather(ProviderRateLimited(retry_after_s=1.0))
    h = make_harness(weather=weather)

    outcome = await h.scope().call(forecast_json(), budget_s=0.5)

    assert outcome.status is ToolOutcomeStatus.UNAVAILABLE
    assert outcome.failure is FailureKind.BUDGET_EXHAUSTED
    assert outcome.attempts == 1
    assert h.sleep.delays == []  # sleeping 1.0 s would overrun the 0.5 s budget


async def test_s11_zero_budget_never_calls_provider() -> None:
    weather = ScriptedWeather()
    h = make_harness(weather=weather)

    outcome = await h.scope().call(forecast_json(), budget_s=0.0)

    assert outcome.failure is FailureKind.BUDGET_EXHAUSTED
    assert outcome.attempts == 0 and weather.calls == 0


async def test_s11_per_attempt_timeout_enforced() -> None:
    settings = make_settings(tool_call_timeout_s=0.03)
    faults = FaultPlan().set_latency(ToolOperation.FORECAST, 0.5)
    h = make_harness(faults=faults, settings=settings)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.UNAVAILABLE
    assert outcome.failure is FailureKind.TIMEOUT
    assert outcome.attempts == settings.tool_max_attempts
    assert outcome.latency_ms < 400


async def test_s11_budget_capped_attempt_timeout_is_budget_exhausted() -> None:
    faults = FaultPlan().set_latency(ToolOperation.FORECAST, 0.5)
    h = make_harness(faults=faults)

    outcome = await h.scope().call(forecast_json(), budget_s=0.03)

    assert outcome.failure is FailureKind.BUDGET_EXHAUSTED
    assert outcome.attempts == 1


# ---- scenario 12 ---------------------------------------------------------------------------------


async def test_s12_persistent_server_fault_marked_unavailable() -> None:
    faults = FaultPlan().fail_always(ToolOperation.FORECAST, FaultKind.SERVER_FAULT)
    h = make_harness(faults=faults)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.UNAVAILABLE
    assert outcome.failure is FailureKind.SERVER_FAULT
    assert outcome.attempts == h.settings.tool_max_attempts
    assert outcome.payload is None and outcome.fetched_at is None  # nothing to fill in from
    assert len(h.sleep.delays) == h.settings.tool_max_attempts - 1
    [record] = h.observability.records
    assert record.status is ToolOutcomeStatus.UNAVAILABLE and record.payload is None


async def test_s12_provider_unavailable_is_permanent_no_retry() -> None:
    weather = ScriptedWeather(ProviderUnavailable("auth failed"))
    h = make_harness(weather=weather)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.UNAVAILABLE
    assert outcome.failure is FailureKind.PERMANENT
    assert outcome.attempts == 1 and weather.calls == 1
    assert h.sleep.delays == []


async def test_s12_unexpected_provider_bug_is_retried_then_unavailable() -> None:
    weather = ScriptedWeather(always=RuntimeError("malformed vendor payload"))
    h = make_harness(weather=weather)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.UNAVAILABLE
    assert outcome.failure is FailureKind.SERVER_FAULT
    assert weather.calls == h.settings.tool_max_attempts
