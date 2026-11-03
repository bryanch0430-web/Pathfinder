"""Per-IP sliding-window limits on sign-in/sign-up and on the model/planner routes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.interface.api import create_app
from app.interface.rate_limit import SlidingWindowLimiter

BAD_LOGIN = {"email": "nobody@example.com", "password": "wrong-password"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class QuickTurns:
    """Stands in for the turn service so the AI-route tests never plan."""

    async def handle_turn(self, **_: object):
        yield SimpleNamespace(event="done", data={"ok": True})


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _logins(client: TestClient, count: int, **kwargs) -> list[int]:
    return [client.post("/api/account/signin", json=BAD_LOGIN, **kwargs).status_code for _ in range(count)]


# -- the limiter ---------------------------------------------------------------


def test_limiter_allows_the_limit_then_reports_the_wait() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(3, window_seconds=60, clock=clock)

    assert [limiter.hit("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    clock.now += 20
    assert limiter.hit("a") == pytest.approx(40.0)


def test_limiter_keys_are_independent() -> None:
    limiter = SlidingWindowLimiter(2, window_seconds=60, clock=FakeClock())

    assert limiter.hit("10.0.0.1") == 0.0
    assert limiter.hit("10.0.0.1") == 0.0
    assert limiter.hit("10.0.0.1") > 0
    assert limiter.hit("10.0.0.2") == 0.0
    assert limiter.hit("10.0.0.2") == 0.0


def test_limiter_window_slides_and_recovers() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(2, window_seconds=60, clock=clock)
    limiter.hit("a")
    clock.now += 30
    limiter.hit("a")
    assert limiter.hit("a") == pytest.approx(30.0)

    clock.now += 30  # the first hit leaves the window; the second still counts
    assert limiter.hit("a") == 0.0
    assert limiter.hit("a") > 0
    clock.now += 60
    assert limiter.hit("a") == 0.0


def test_limit_zero_turns_the_limiter_off() -> None:
    limiter = SlidingWindowLimiter(0, clock=FakeClock())
    assert all(limiter.hit("a") == 0.0 for _ in range(100))


# -- auth routes ------------------------------------------------------------------


def test_eleventh_login_in_a_minute_is_refused_with_retry_after(client: TestClient) -> None:
    assert _logins(client, 10) == [401] * 10

    refused = client.post("/api/account/signin", json=BAD_LOGIN)

    assert refused.status_code == 429
    assert set(refused.json()) == {"detail"} and refused.json()["detail"]
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    # Sign-up shares the sign-in budget.
    signup = client.post("/api/account/signup", json={"email": "new@example.com", "password": "long-enough"})
    assert signup.status_code == 429


def test_login_window_recovers(client: TestClient) -> None:
    clock = FakeClock()
    client.app.state.rate_limits.auth.clock = clock
    _logins(client, 10)
    assert _logins(client, 1) == [429]

    clock.now += 61

    assert _logins(client, 1) == [401]


def test_another_ip_has_its_own_budget(client: TestClient) -> None:
    _logins(client, 10)
    assert _logins(client, 1) == [429]

    # Same app (same counters), different socket address; no lifespan rerun.
    other = TestClient(client.app, client=("203.0.113.9", 50000))
    assert _logins(other, 1) == [401]


def test_forwarded_for_does_not_buy_a_new_budget(client: TestClient) -> None:
    statuses = [
        client.post("/api/account/signin", json=BAD_LOGIN, headers={"X-Forwarded-For": f"198.51.100.{n}"}).status_code
        for n in range(11)
    ]
    assert statuses == [401] * 10 + [429]


def test_each_app_starts_with_empty_counters(settings: Settings) -> None:
    with TestClient(create_app(settings)) as first:
        assert _logins(first, 11)[-1] == 429
    with TestClient(create_app(settings)) as second:
        assert _logins(second, 1) == [401]


def test_auth_limit_comes_from_settings(settings: Settings) -> None:
    with TestClient(create_app(settings.model_copy(update={"rate_limit_auth_per_minute": 2}))) as scoped:
        assert _logins(scoped, 3) == [401, 401, 429]


# -- AI routes -------------------------------------------------------------------


def test_ai_routes_share_one_budget(settings: Settings) -> None:
    with TestClient(create_app(settings.model_copy(update={"rate_limit_ai_per_minute": 3}))) as scoped:
        scoped.app.state.container.turn_service = QuickTurns()
        turn = {"message": "Plan Kyoto", "locale": "en", "client_token": "guest-rl"}
        admin = {"X-Admin-Token": settings.admin_token}

        first = scoped.post("/api/turn/stream", json=turn)
        second = scoped.post("/api/turn/stream", json=turn)
        evaluation = scoped.post("/api/eval/trials", json={"dataset_id": "missing"}, headers=admin)
        refused_turn = scoped.post("/api/turn/stream", json=turn)
        refused_eval = scoped.post("/api/eval/trials", json={"dataset_id": "missing"}, headers=admin)

    assert first.status_code == 200 and "event: done" in first.text
    assert second.status_code == 200
    assert evaluation.status_code == 404  # counted even though the dataset is unknown
    assert refused_turn.status_code == 429 and int(refused_turn.headers["Retry-After"]) >= 1
    assert refused_eval.status_code == 429
    assert set(refused_turn.json()) == {"detail"}


def test_default_ai_limit_is_thirty_per_minute(client: TestClient) -> None:
    client.app.state.container.turn_service = QuickTurns()
    turn = {"message": "Plan Kyoto", "locale": "en", "client_token": "guest-rl"}

    statuses = [client.post("/api/turn/stream", json=turn).status_code for _ in range(31)]

    assert statuses == [200] * 30 + [429]
    # Other routes are not limited.
    assert client.post("/api/journeys", json={"client_token": "guest-rl"}).status_code == 200
