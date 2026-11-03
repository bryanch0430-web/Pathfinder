"""The public wallet routes do not call the rate upstream on every request.

A good answer is kept for 12 hours and a failed fetch is remembered for a minute, so an upstream
that is down costs one attempt a minute (each waiting up to the timeout) instead of one per page load.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.infrastructure.providers.fx as fx_module
from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import FailureKind
from app.infrastructure.providers.fx import FAILURE_BACKOFF_SECONDS, RATES_TTL_SECONDS, FxService
from app.interface.api import create_app

LIVE = {"result": "success", "rates": {"JPY": 160.0, "HKD": 7.7, "USD": 1.0}}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class Upstream:
    """Stands in for open.er-api.com and counts the calls made to it."""

    def __init__(self) -> None:
        self.calls = 0
        self.answer: dict | Exception = ProviderError(
            "open.er-api is unreachable", kind=FailureKind.NETWORK, source="open.er-api"
        )

    async def __call__(self, *args: object, **kwargs: object) -> dict:
        self.calls += 1
        await asyncio.sleep(0)  # a request that arrives meanwhile gets to start its own, if it may
        answer = self.answer
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture()
def upstream(monkeypatch: pytest.MonkeyPatch) -> Upstream:
    fake = Upstream()
    monkeypatch.setattr(fx_module, "request_json", fake)
    return fake


async def _refresh(fx: FxService, times: int) -> None:
    for _ in range(times):
        await fx.refresh()


def test_a_good_answer_is_kept_for_twelve_hours(upstream: Upstream) -> None:
    upstream.answer = LIVE
    clock = Clock()
    fx = FxService(clock=clock)

    async def scenario() -> list[int]:
        calls = []
        await _refresh(fx, 5)
        calls.append(upstream.calls)
        clock.now += RATES_TTL_SECONDS - 1
        await _refresh(fx, 5)
        calls.append(upstream.calls)
        clock.now += 2
        await _refresh(fx, 5)
        calls.append(upstream.calls)
        return calls

    assert asyncio.run(scenario()) == [1, 1, 2]
    assert fx.source == "open.er-api" and fx.convert(1, "USD", "JPY") == 160.0


def test_a_down_upstream_is_asked_once_a_minute_not_on_every_request(upstream: Upstream) -> None:
    clock = Clock()
    fx = FxService(clock=clock)

    async def scenario() -> list[int]:
        calls = []
        await _refresh(fx, 20)
        calls.append(upstream.calls)
        clock.now += FAILURE_BACKOFF_SECONDS - 1
        await _refresh(fx, 20)
        calls.append(upstream.calls)
        clock.now += 2
        await _refresh(fx, 20)
        calls.append(upstream.calls)
        return calls

    assert asyncio.run(scenario()) == [1, 1, 2]
    assert fx.source == "static"  # the dated reference table keeps answering
    assert 100 < fx.convert(1000, "HKD", "USD") < 200


def test_requests_that_arrive_together_share_one_attempt(upstream: Upstream) -> None:
    fx = FxService(clock=Clock())

    async def scenario() -> None:
        await asyncio.gather(*(fx.refresh() for _ in range(10)))

    asyncio.run(scenario())
    assert upstream.calls == 1

    upstream.calls = 0
    upstream.answer = LIVE
    fresh = FxService(clock=Clock())

    async def together() -> None:
        await asyncio.gather(*(fresh.refresh() for _ in range(10)))

    asyncio.run(together())
    assert upstream.calls == 1 and fresh.source == "open.er-api"


def test_an_unusable_answer_is_remembered_like_a_failure(upstream: Upstream) -> None:
    upstream.answer = {"result": "error", "error-type": "quota-reached"}
    fx = FxService(clock=Clock())

    asyncio.run(_refresh(fx, 10))

    assert upstream.calls == 1 and fx.source == "static"


def test_the_upstream_is_used_again_once_it_has_recovered(upstream: Upstream) -> None:
    clock = Clock()
    fx = FxService(clock=clock)

    async def scenario() -> None:
        await _refresh(fx, 3)
        upstream.answer = LIVE
        clock.now += FAILURE_BACKOFF_SECONDS + 1
        await _refresh(fx, 3)

    asyncio.run(scenario())

    assert upstream.calls == 2 and fx.source == "open.er-api"
    assert fx.snapshot("USD")["rates"]["JPY"] == 160.0


def test_the_public_wallet_routes_make_one_outbound_call_while_the_upstream_is_down(
    settings: Settings, upstream: Upstream
) -> None:
    with TestClient(create_app(settings)) as client:
        wallet = [client.get("/api/wallet", params={"base": "HKD"}) for _ in range(20)]
        convert = [client.get("/api/wallet/convert", params={"amount": 100, "from": "HKD", "to": "USD"}) for _ in range(5)]

    assert {response.status_code for response in wallet + convert} == {200}
    assert {response.json()["source"] for response in wallet + convert} == {"static"}
    assert upstream.calls == 1
