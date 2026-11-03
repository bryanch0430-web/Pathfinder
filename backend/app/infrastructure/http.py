"""Small, shared HTTP helper with explicit failure classification."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Iterator

import httpx

from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

#: Backoff before retry n (0-based) is BACKOFF_BASE_SECONDS * 2**n plus a jitter
#: drawn from [0, BACKOFF_JITTER_SECONDS].
BACKOFF_BASE_SECONDS = 0.35
BACKOFF_JITTER_SECONDS = 0.25
#: Without an explicit budget, the attempts and waits of one call together get
#: twice the per-attempt timeout (Proposal p3: retries stay inside a strict time budget).
DEFAULT_BUDGET_FACTOR = 2.0


@dataclass(frozen=True)
class RetryTiming:
    """The clock, sleep and jitter source of the retry loop. Tests pass fakes."""

    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    jitter: Callable[[float, float], float] = field(default=random.uniform)


def backoff_delay(attempt: int, jitter: Callable[[float, float], float] = random.uniform) -> float:
    """Exponential backoff with additive jitter for retry number ``attempt`` (0-based)."""
    return BACKOFF_BASE_SECONDS * (2**attempt) + jitter(0.0, BACKOFF_JITTER_SECONDS)


def retry_after_seconds(value: str | None, *, now: datetime | None = None) -> float | None:
    """The wait a Retry-After header asks for, in seconds; None when absent or unreadable.

    The header is either a number of seconds or an HTTP date.
    """
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())

#: Transport for every request made in the current context. None is the real
#: network; the TC-02 replay (app.eval_replay) sets recorded-shape responses.
_TRANSPORT: ContextVar[httpx.AsyncBaseTransport | None] = ContextVar("pathfinder_http_transport", default=None)


@contextmanager
def use_transport(transport: httpx.AsyncBaseTransport | None) -> Iterator[None]:
    """Route request_json through ``transport`` inside this block only (this task and its children)."""
    token = _TRANSPORT.set(transport)
    try:
        yield
    finally:
        _TRANSPORT.reset(token)


def classify_status(status: int, source: str, message: str = "", *, detail: str = "") -> ProviderError:
    """The typed failure for an HTTP status. ``detail`` is what the source said: for the trace, not the message."""
    text = message or f"{source} returned HTTP {status}"
    if status in {401, 403}:
        return ProviderError(text, kind=FailureKind.AUTH, source=source, retryable=False, detail=detail)
    if status == 429:
        return ProviderError(text, kind=FailureKind.RATE_LIMIT, source=source, detail=detail)
    if status in {400, 422}:
        return ProviderError(text, kind=FailureKind.INVALID_REQUEST, source=source, retryable=False, detail=detail)
    if status == 404:
        error = ProviderNoData(text, source=source)
        error.detail = detail
        return error
    if status >= 500 or status in RETRYABLE_STATUS:
        # 408 Request Timeout and 425 Too Early are transient like a 5xx.
        return ProviderError(text, kind=FailureKind.SERVER_ERROR, source=source, detail=detail)
    return ProviderError(text, kind=FailureKind.SERVER_ERROR, source=source, retryable=False, detail=detail)


def classify_exception(exc: BaseException, source: str) -> ProviderError:
    """The typed failure that an exception raised by a request stands for.

    A timeout and a network failure are transient (retryable); anything else is a server
    error that retrying would not fix.
    """
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        return ProviderError(
            f"{source} timed out: {exc.__class__.__name__}",
            kind=FailureKind.TIMEOUT,
            source=source,
        )
    if isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError)):
        return ProviderError(
            f"{source} network failure: {exc.__class__.__name__}",
            kind=FailureKind.NETWORK,
            source=source,
        )
    return ProviderError(
        f"{source} failed: {exc.__class__.__name__}",
        kind=FailureKind.SERVER_ERROR,
        source=source,
        retryable=False,
    )


async def request_json(
    method: str,
    url: str,
    *,
    source: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 15.0,
    retries: int = 2,
    budget: float | None = None,
    user_agent: str = "Pathfinder/1.0",
    timing: RetryTiming | None = None,
) -> dict[str, Any]:
    """One JSON request, retried on timeouts, rate limits and 5xx inside a time budget.

    ``budget`` (default: DEFAULT_BUDGET_FACTOR x ``timeout``) bounds every attempt
    and every wait together. Each attempt gets at most the time left; a wait,
    whether the backoff or the server's Retry-After, that would not leave time
    for another attempt ends the loop with the last classified error instead.
    """
    timing = timing or RetryTiming()
    retries = max(retries, 0)  # below zero would leave the loop with no attempt at all
    total = budget if budget is not None else timeout * DEFAULT_BUDGET_FACTOR
    started = timing.clock()

    def remaining() -> float:
        return total - (timing.clock() - started)

    last: ProviderError | None = None
    merged_headers = {"User-Agent": user_agent, "Accept": "application/json"}
    if headers:
        merged_headers.update(headers)
    transport = _TRANSPORT.get()
    for attempt in range(retries + 1):
        wait_hint: float | None = None
        try:
            left = remaining()
            if left <= 0:
                raise asyncio.TimeoutError()
            attempt_timeout = min(timeout, left)
            async with httpx.AsyncClient(timeout=attempt_timeout, follow_redirects=True, transport=transport) as client:
                response = await asyncio.wait_for(
                    client.request(method, url, headers=merged_headers, params=params, json=payload),
                    timeout=attempt_timeout,
                )
            if response.status_code >= 400:
                if response.status_code in RETRYABLE_STATUS:
                    wait_hint = retry_after_seconds(response.headers.get("Retry-After"))
                # The source's own error page can hold keys and markup, and this error's text
                # reaches plan gaps: it names the status only. The page goes to the debug log.
                logger.debug("%s returned HTTP %s: %s", source, response.status_code, response.text[:300])
                raise classify_status(response.status_code, source)
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderError(
                    f"{source} returned non-JSON content",
                    kind=FailureKind.PARSE,
                    source=source,
                ) from exc
            if not isinstance(data, dict):
                return {"data": data}
            return data
        except ProviderNoData:
            raise
        except Exception as exc:  # never leak an SDK exception upward: it becomes a typed failure
            last = classify_exception(exc, source)
        if last is None or not last.retryable or attempt >= retries:
            break
        delay = wait_hint if wait_hint is not None else backoff_delay(attempt, timing.jitter)
        if delay >= remaining():
            # Waiting would spend the rest of the budget: report the failure now.
            break
        await timing.sleep(delay)
    assert last is not None
    raise last


def haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    radius = 6371.0
    d_lat = radians(b_lat - a_lat)
    d_lng = radians(b_lng - a_lng)
    h = sin(d_lat / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(d_lng / 2) ** 2
    return 2 * radius * asin(sqrt(h))
