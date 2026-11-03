"""What a request's exceptions stand for: a transient network failure is retried, a cancellation is never data.

`classify_exception` turns whatever a request raised into a typed failure. Transport failures that
httpx does not map to its own `NetworkError` (a refused proxy, a reset connection, a failed DNS
lookup, a TLS handshake that broke) are as transient as the ones it does; a cancellation is not a
failure at all and has to reach whoever cancelled.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from typing import Any

import httpx
import pytest

from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind
from app.infrastructure.http import RetryTiming, classify_exception, request_json, use_transport

URL = "https://provider.example/api"


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.RemoteProtocolError("closed"),
        httpx.ProxyError("proxy refused the tunnel"),
        OSError("network unreachable"),
        ConnectionResetError("reset by peer"),
        socket.gaierror(-2, "Name or service not known"),
        ssl.SSLError("handshake failed"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_a_network_level_failure_is_transient(error: BaseException) -> None:
    failure = classify_exception(error, "src")

    assert failure.kind is FailureKind.NETWORK and failure.retryable
    assert str(failure) == f"src network failure: {type(error).__name__}"


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), asyncio.TimeoutError(), TimeoutError("slow")])
def test_a_timeout_is_still_a_timeout_not_a_network_failure(error: BaseException) -> None:
    failure = classify_exception(error, "src")

    assert failure.kind is FailureKind.TIMEOUT and failure.retryable


@pytest.mark.parametrize(
    "error", [httpx.TooManyRedirects("loop"), ValueError("bad payload"), KeyError("lat"), RuntimeError("boom")]
)
def test_a_failure_that_asking_again_would_not_fix_stays_permanent(error: BaseException) -> None:
    failure = classify_exception(error, "src")

    assert failure.kind is FailureKind.SERVER_ERROR and not failure.retryable


def test_a_typed_failure_comes_back_as_it_was() -> None:
    for error in (ProviderError("x", kind=FailureKind.AUTH, source="s", retryable=False), ProviderNoData("none", source="s")):
        assert classify_exception(error, "other") is error


@pytest.mark.parametrize("error", [asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(1)], ids=lambda e: type(e).__name__)
def test_a_cancellation_is_never_turned_into_a_failure(error: BaseException) -> None:
    with pytest.raises(type(error)):
        classify_exception(error, "src")


# -- through the request loop -----------------------------------------------------------------------


class Raises(httpx.AsyncBaseTransport):
    def __init__(self, error: BaseException, then_ok: bool = False) -> None:
        self.error = error
        self.then_ok = then_ok
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.then_ok and self.calls > 1:
            return httpx.Response(200, json={"ok": True}, request=request)
        raise self.error


async def _no_wait(seconds: float) -> None:
    return None


def _request(transport: httpx.AsyncBaseTransport, *, retries: int = 2) -> dict[str, Any]:
    with use_transport(transport):
        return asyncio.run(request_json("GET", URL, source="src", retries=retries, timing=RetryTiming(sleep=_no_wait)))


def test_a_refused_proxy_is_retried_and_can_recover() -> None:
    transport = Raises(httpx.ProxyError("proxy refused the tunnel"), then_ok=True)

    assert _request(transport) == {"ok": True}
    assert transport.calls == 2


def test_a_reset_connection_that_keeps_failing_ends_as_a_retryable_network_failure() -> None:
    transport = Raises(ConnectionResetError("reset by peer"))

    with pytest.raises(ProviderError) as failure:
        _request(transport, retries=2)

    assert transport.calls == 3
    assert failure.value.kind is FailureKind.NETWORK and failure.value.retryable


def test_a_redirect_loop_is_one_attempt() -> None:
    transport = Raises(httpx.TooManyRedirects("loop"))

    with pytest.raises(ProviderError) as failure:
        _request(transport, retries=2)

    assert transport.calls == 1 and not failure.value.retryable


def test_a_cancellation_inside_a_request_is_not_swallowed() -> None:
    transport = Raises(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        _request(transport)

    assert transport.calls == 1
