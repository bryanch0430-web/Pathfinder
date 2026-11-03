"""Proposal DB-11 (p3 l.122-123): timeouts and rate limits are retried with backoff
and jitter within a strict time budget.

The retry loop in ``request_json`` runs against a fake transport, a fake clock and
a recording sleep, so no test waits for real backoff.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind
from app.infrastructure.http import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_JITTER_SECONDS,
    RetryTiming,
    backoff_delay,
    request_json,
    retry_after_seconds,
    use_transport,
)

URL = "https://provider.example/api"


class FakeClock:
    """A monotonic clock that only moves when the loop sleeps or an attempt takes time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


class Script:
    """Answers each attempt with the next scripted step.

    A step is an HTTP status, a ``(status, headers)`` pair, or ``"timeout"``: the
    attempt uses its whole timeout on the fake clock and then times out.
    """

    def __init__(self, clock: FakeClock, steps: list, *, attempt_seconds: float = 0.0):
        self.clock = clock
        self.steps = list(steps)
        self.attempt_seconds = attempt_seconds
        self.timeouts: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        read_timeout = float(request.extensions["timeout"]["read"])
        self.timeouts.append(read_timeout)
        step = self.steps.pop(0) if self.steps else 200
        if step == "timeout":
            self.clock.now += read_timeout
            raise httpx.ReadTimeout("timed out", request=request)
        self.clock.now += min(self.attempt_seconds, read_timeout)
        status, headers = step if isinstance(step, tuple) else (step, {})
        body = {"ok": True} if status < 400 else {"error": status}
        return httpx.Response(status, json=body, headers=headers)

    @property
    def attempts(self) -> int:
        return len(self.timeouts)


def test_upstream_error_body_is_not_part_of_the_error_text() -> None:
    """S-M3: a source's own error page can hold keys and markup; the error names the status only."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>secret key abc</html>")

    with use_transport(httpx.MockTransport(handler)):
        with pytest.raises(ProviderError) as caught:
            asyncio.run(request_json("GET", URL, source="demo", retries=0))
    assert caught.value.kind is FailureKind.SERVER_ERROR
    assert "secret" not in str(caught.value) and "<html>" not in str(caught.value)
    assert str(caught.value) == "demo returned HTTP 500"


def _run(script: Script, clock: FakeClock, *, jitter=None, **kwargs) -> dict:
    timing = RetryTiming(clock=clock, sleep=clock.sleep, jitter=jitter or random.uniform)

    async def go() -> dict:
        with use_transport(httpx.MockTransport(script)):
            return await request_json("GET", URL, source="probe", timing=timing, **kwargs)

    return asyncio.run(go())


def _backoff_bounds(attempt: int) -> tuple[float, float]:
    low = BACKOFF_BASE_SECONDS * 2**attempt
    return low, low + BACKOFF_JITTER_SECONDS


def test_db11_a_timeout_is_retried_with_exponential_backoff() -> None:
    clock = FakeClock()
    script = Script(clock, ["timeout", "timeout", 200])
    assert _run(script, clock, timeout=5.0, retries=2, budget=30.0) == {"ok": True}
    assert script.attempts == 3
    assert len(clock.sleeps) == 2
    for attempt, slept in enumerate(clock.sleeps):
        low, high = _backoff_bounds(attempt)
        assert low <= slept <= high, (attempt, slept)


def test_db11_retries_run_out_and_the_timeout_is_reported_as_a_timeout() -> None:
    clock = FakeClock()
    script = Script(clock, ["timeout"] * 5)
    with pytest.raises(ProviderError) as caught:
        _run(script, clock, timeout=2.0, retries=2, budget=60.0)
    assert script.attempts == 3 and len(clock.sleeps) == 2
    assert caught.value.kind == FailureKind.TIMEOUT and caught.value.retryable


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 408])
def test_db11_rate_limits_and_server_errors_are_retried(status: int) -> None:
    clock = FakeClock()
    script = Script(clock, [status, 200])
    assert _run(script, clock, timeout=5.0, retries=2, budget=30.0) == {"ok": True}
    assert script.attempts == 2
    low, high = _backoff_bounds(0)
    assert len(clock.sleeps) == 1 and low <= clock.sleeps[0] <= high


def test_db11_an_exhausted_rate_limit_keeps_its_kind() -> None:
    clock = FakeClock()
    script = Script(clock, [429, 429, 429])
    with pytest.raises(ProviderError) as caught:
        _run(script, clock, timeout=5.0, retries=2, budget=30.0)
    assert script.attempts == 3
    assert caught.value.kind == FailureKind.RATE_LIMIT and caught.value.retryable


@pytest.mark.parametrize("status", [400, 401, 403, 409, 418, 422])
def test_db11_other_client_errors_are_not_retried(status: int) -> None:
    clock = FakeClock()
    script = Script(clock, [status, 200])
    with pytest.raises(ProviderError) as caught:
        _run(script, clock, timeout=5.0, retries=2, budget=30.0)
    assert script.attempts == 1 and clock.sleeps == []
    assert not caught.value.retryable


def test_db11_not_found_is_no_data_and_is_not_retried() -> None:
    clock = FakeClock()
    script = Script(clock, [404, 200])
    with pytest.raises(ProviderNoData):
        _run(script, clock, timeout=5.0, retries=2, budget=30.0)
    assert script.attempts == 1 and clock.sleeps == []


def test_db11_retry_after_in_seconds_is_honoured() -> None:
    clock = FakeClock()
    script = Script(clock, [(429, {"Retry-After": "3"}), (503, {"Retry-After": "0"}), 200])
    assert _run(script, clock, timeout=5.0, retries=2, budget=30.0) == {"ok": True}
    assert clock.sleeps == [3.0, 0.0]


def test_db11_retry_after_as_an_http_date_is_honoured() -> None:
    now = datetime(2026, 4, 12, 9, 0, 0, tzinfo=timezone.utc)
    later = format_datetime(now + timedelta(seconds=7), usegmt=True)
    assert retry_after_seconds(later, now=now) == pytest.approx(7.0)
    assert retry_after_seconds(format_datetime(now - timedelta(seconds=30), usegmt=True), now=now) == 0.0
    assert retry_after_seconds("2.5") == 2.5
    assert retry_after_seconds(None) is None and retry_after_seconds("") is None
    assert retry_after_seconds("soon") is None


def test_db11_unreadable_retry_after_falls_back_to_the_backoff() -> None:
    clock = FakeClock()
    script = Script(clock, [(429, {"Retry-After": "soon"}), 200])
    assert _run(script, clock, timeout=5.0, retries=2, budget=30.0) == {"ok": True}
    low, high = _backoff_bounds(0)
    assert len(clock.sleeps) == 1 and low <= clock.sleeps[0] <= high


def test_db11_a_retry_after_beyond_the_budget_ends_the_loop_without_waiting() -> None:
    clock = FakeClock()
    script = Script(clock, [(429, {"Retry-After": "120"}), 200])
    with pytest.raises(ProviderError) as caught:
        _run(script, clock, timeout=5.0, retries=2, budget=30.0)
    assert caught.value.kind == FailureKind.RATE_LIMIT
    assert script.attempts == 1 and clock.sleeps == [] and clock.now <= 30.0


def test_db11_jitter_stays_within_bounds() -> None:
    rng = random.Random(1102)
    for attempt in range(4):
        low, high = _backoff_bounds(attempt)
        assert backoff_delay(attempt, lambda a, b: a) == pytest.approx(low)
        assert backoff_delay(attempt, lambda a, b: b) == pytest.approx(high)
        draws = [backoff_delay(attempt, rng.uniform) for _ in range(500)]
        assert all(low <= value <= high for value in draws)
        assert len({round(value, 6) for value in draws}) > 400  # the jitter really varies
    # The loop asks for jitter in exactly that window.
    seen: list[tuple[float, float]] = []

    def recording(a: float, b: float) -> float:
        seen.append((a, b))
        return b

    clock = FakeClock()
    _run(Script(clock, [503, 503, 200]), clock, jitter=recording, timeout=5.0, retries=2, budget=30.0)
    assert seen == [(0.0, BACKOFF_JITTER_SECONDS)] * 2
    assert clock.sleeps == pytest.approx([_backoff_bounds(0)[1], _backoff_bounds(1)[1]])


@pytest.mark.parametrize(
    ("timeout", "budget", "retries", "steps", "attempt_seconds"),
    [
        (8.0, 20.0, 5, ["timeout"] * 6, 0.0),  # every attempt hangs to its timeout
        (15.0, 30.0, 2, ["timeout"] * 3, 0.0),  # the default shape: the third attempt is cut
        (5.0, 6.0, 9, [503] * 10, 1.9),  # slow failures plus backoff
        (5.0, 1.0, 9, [429] * 10, 0.3),  # a budget shorter than one timeout
        (3.0, 10.0, 4, [(429, {"Retry-After": "4"})] * 5, 0.5),  # server-requested waits
    ],
)
def test_db11_total_time_never_exceeds_the_budget(timeout, budget, retries, steps, attempt_seconds) -> None:
    for jitter in (lambda a, b: a, lambda a, b: b, random.Random(7).uniform):
        clock = FakeClock()
        script = Script(clock, steps, attempt_seconds=attempt_seconds)
        with pytest.raises(ProviderError):
            _run(script, clock, jitter=jitter, timeout=timeout, retries=retries, budget=budget)
        assert clock.now <= budget + 1e-9, (clock.now, budget, script.timeouts, clock.sleeps)
        # No attempt is allowed more time than the per-attempt timeout or the time left.
        assert all(0 < limit <= timeout for limit in script.timeouts)
        assert script.attempts <= retries + 1


def test_db11_the_last_attempt_only_gets_the_time_left() -> None:
    clock = FakeClock()
    script = Script(clock, ["timeout", "timeout", "timeout"])
    with pytest.raises(ProviderError):
        _run(script, clock, jitter=lambda a, b: a, timeout=15.0, retries=2, budget=20.0)
    assert script.timeouts[0] == 15.0
    assert script.timeouts[1] == pytest.approx(20.0 - 15.0 - BACKOFF_BASE_SECONDS)
    assert script.attempts == 2 and clock.now == pytest.approx(20.0)


def test_db11_default_budget_is_twice_the_timeout() -> None:
    clock = FakeClock()
    script = Script(clock, ["timeout"] * 3)
    with pytest.raises(ProviderError):
        _run(script, clock, timeout=4.0, retries=2)
    assert clock.now <= 8.0 + 1e-9 and script.attempts == 2


def test_db11_an_attempt_that_hangs_is_cut_off_at_the_time_left() -> None:
    """The budget also holds when a transport ignores its own timeout."""
    clock = FakeClock()
    timing = RetryTiming(clock=clock, sleep=clock.sleep, jitter=random.uniform)

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def go() -> None:
        with use_transport(httpx.MockTransport(hang)):
            await request_json("GET", URL, source="probe", timing=timing, timeout=5.0, retries=0, budget=0.02)

    with pytest.raises(ProviderError) as caught:
        asyncio.run(asyncio.wait_for(go(), timeout=5.0))
    assert caught.value.kind == FailureKind.TIMEOUT
