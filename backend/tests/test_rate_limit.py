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


def test_behind_uvicorns_proxy_handling_only_a_trusted_peer_chooses_its_key(settings: Settings) -> None:
    """The app never reads X-Forwarded-For, but uvicorn rewrites the client of a peer it trusts (README, rate limits)."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    def forwarded_logins(peer: str) -> list[int]:
        app = ProxyHeadersMiddleware(create_app(settings), trusted_hosts="127.0.0.1")
        with TestClient(app, client=(peer, 50000)) as peer_client:
            return [
                peer_client.post(
                    "/api/account/signin", json=BAD_LOGIN, headers={"X-Forwarded-For": f"198.51.100.{n}"}
                ).status_code
                for n in range(11)
            ]

    assert forwarded_logins("127.0.0.1") == [401] * 11  # a trusted proxy's header names the caller
    assert forwarded_logins("203.0.113.9") == [401] * 10 + [429]  # anyone else's is ignored


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


# -- trips and share links ------------------------------------------------------------


def _create_limit(settings: Settings, limit: int):
    return TestClient(create_app(settings.model_copy(update={"rate_limit_create_per_minute": limit})))


def _planned_session(scoped: TestClient, token: str) -> str:
    """A guest session with a plan, so it can be shared."""
    import random

    from scripts.seed_platform_demo import DESTINATIONS, _plan

    session_id = scoped.post("/api/journeys", json={"client_token": token}).json()["session"]["id"]
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    plan = _plan(random.Random(3), place, 3).model_copy(update={"session_id": session_id})
    scoped.app.state.container.store.save_plan(plan, "test plan")
    return session_id


def test_new_trips_and_share_links_share_one_budget_per_address(settings: Settings) -> None:
    guest = {"X-Client-Token": "guest-create"}
    with _create_limit(settings, 4) as scoped:
        session_id = _planned_session(scoped, "guest-create")  # 1 trip
        shares = [scoped.post(f"/api/journeys/{session_id}/share", headers=guest) for _ in range(3)]
        refused_trip = scoped.post("/api/journeys", json={"client_token": "guest-create"})
        refused_share = scoped.post(f"/api/journeys/{session_id}/share", headers=guest)
        # Reading and revoking are not creation.
        assert scoped.get(f"/api/journeys/{session_id}", headers=guest).status_code == 200
        assert scoped.delete(f"/api/journeys/{session_id}/share", headers=guest).status_code == 200

    assert [response.status_code for response in shares] == [200, 200, 200]
    assert refused_trip.status_code == 429 and int(refused_trip.headers["Retry-After"]) >= 1
    assert set(refused_trip.json()) == {"detail"}
    assert refused_share.status_code == 429


def test_a_refused_trip_is_not_stored(settings: Settings) -> None:
    import sqlite3
    from contextlib import closing

    with _create_limit(settings, 2) as scoped:
        statuses = [scoped.post("/api/journeys", json={"client_token": f"guest-{n}"}).status_code for n in range(5)]
        with closing(sqlite3.connect(scoped.app.state.container.store.path)) as conn:
            stored = conn.execute("SELECT COUNT(*) FROM journeys").fetchone()[0]

    assert statuses == [200, 200, 429, 429, 429]
    assert stored == 2


def test_requests_that_make_nothing_do_not_use_the_creation_budget(settings: Settings) -> None:
    with _create_limit(settings, 2) as scoped:
        unusable = [scoped.post("/api/journeys", json={"client_token": "bad token"}).status_code for _ in range(6)]
        not_yours = [
            scoped.post("/api/journeys/sess_nope/share", headers={"X-Client-Token": "guest-x"}).status_code
            for _ in range(6)
        ]
        no_plan_yet = scoped.post("/api/journeys", json={"client_token": "guest-noplan"}).json()["session"]["id"]
        no_plan = [
            scoped.post(f"/api/journeys/{no_plan_yet}/share", headers={"X-Client-Token": "guest-noplan"}).status_code
            for _ in range(6)
        ]
        # One trip was made above; exactly one more fits.
        statuses = [scoped.post("/api/journeys", json={"client_token": "guest-ok"}).status_code for _ in range(2)]

    assert unusable == [422] * 6 and not_yours == [404] * 6 and no_plan == [404] * 6
    assert statuses == [200, 429]


def test_default_creation_limit_is_sixty_per_minute(client: TestClient) -> None:
    statuses = [client.post("/api/journeys", json={"client_token": f"guest-{n}"}).status_code for n in range(61)]

    assert statuses == [200] * 60 + [429]
    # The planning budget is separate.
    client.app.state.container.turn_service = QuickTurns()
    turn = {"message": "Plan Kyoto", "locale": "en", "client_token": "guest-rl"}
    assert client.post("/api/turn/stream", json=turn).status_code == 200


def test_creation_limit_zero_turns_it_off(settings: Settings) -> None:
    with _create_limit(settings, 0) as scoped:
        assert {scoped.post("/api/journeys", json={"client_token": f"g-{n}"}).status_code for n in range(80)} == {200}


# -- place suggestions and the handbook ----------------------------------------------


def _lookup_limit(settings: Settings, limit: int):
    return TestClient(create_app(settings.model_copy(update={"rate_limit_lookup_per_minute": limit})))


def test_a_fresh_header_every_call_does_not_reset_the_lookup_budget(settings: Settings) -> None:
    """The caller's identity is a string it makes up, so the budget belongs to the address."""
    with _lookup_limit(settings, 3) as scoped:
        statuses = []
        for number in range(6):
            route, params = (
                ("/api/places/suggest", {"q": f"kyoto{number}"})
                if number % 2 == 0
                else ("/api/handbook", {"destination": "Kyoto"})
            )
            response = scoped.get(route, params=params, headers={"X-Client-Token": f"fresh-{number}"})
            statuses.append(response.status_code)
            last = response

    assert statuses == [200, 200, 200, 429, 429, 429]  # both routes draw on one budget
    assert int(last.headers["Retry-After"]) >= 1 and set(last.json()) == {"detail"}


def test_calls_without_an_identity_do_not_use_the_lookup_budget(settings: Settings) -> None:
    with _lookup_limit(settings, 2) as scoped:
        anonymous = [scoped.get("/api/places/suggest", params={"q": "kyoto"}).status_code for _ in range(8)]
        too_short = [
            scoped.get("/api/places/suggest", params={"q": "ky"}, headers={"X-Client-Token": "guest-l"}).status_code
            for _ in range(8)
        ]
        identified = [
            scoped.get("/api/places/suggest", params={"q": "kyoto"}, headers={"X-Client-Token": "guest-l"}).status_code
            for _ in range(3)
        ]

    assert anonymous == [401] * 8 and too_short == [422] * 8
    assert identified == [200, 200, 429]


def test_default_lookup_limit_is_sixty_per_minute(client: TestClient) -> None:
    guest = {"X-Client-Token": "guest-lookup"}

    statuses = [client.get("/api/places/suggest", params={"q": "kyoto"}, headers=guest).status_code for _ in range(61)]

    assert statuses == [200] * 60 + [429]


def test_lookup_limit_zero_turns_it_off(settings: Settings) -> None:
    with _lookup_limit(settings, 0) as scoped:
        guest = {"X-Client-Token": "guest-lookup"}
        assert {scoped.get("/api/places/suggest", params={"q": "kyoto"}, headers=guest).status_code for _ in range(80)} == {200}


# -- junk does not use up the budget of the people it is not from ----------------------


def test_requests_that_are_refused_do_not_use_the_planning_budget(settings: Settings) -> None:
    """Anyone can send a turn for a trip that is not theirs. That must not lock the owner (or the admin) out."""
    owner, other = {"X-Client-Token": "guest-owner"}, {"X-Client-Token": "guest-other"}
    admin = {"X-Admin-Token": settings.admin_token}
    with TestClient(create_app(settings.model_copy(update={"rate_limit_ai_per_minute": 3}))) as scoped:
        scoped.app.state.container.turn_service = QuickTurns()
        trip = scoped.post("/api/journeys", json={"client_token": "guest-owner"}).json()["session"]["id"]

        def turn(body: dict, headers: dict | None = None) -> int:
            return scoped.post("/api/turn/stream", json=body, headers=headers or {}).status_code

        unknown_trip = [turn({"session_id": f"sess_nope{n}", "message": "hi"}) for n in range(10)]
        someone_elses = [turn({"session_id": trip, "message": "hi"}, other) for _ in range(10)]
        no_token_at_all = [turn({"message": "hi"}) for _ in range(10)]
        unusable_token = [turn({"message": "hi", "client_token": "bad token"}) for _ in range(10)]
        invalid_body = [turn({"session_id": trip, "message": ""}, owner) for _ in range(10)]
        wrong_admin = [
            scoped.post("/api/eval/trials", json={"dataset_id": "x"}, headers={"X-Admin-Token": "wrong"}).status_code
            for _ in range(10)
        ]
        # Then the people the junk was not from still have their whole budget.
        admin_run = scoped.post("/api/eval/trials", json={"dataset_id": "x"}, headers=admin).status_code
        owners = [turn({"session_id": trip, "message": "Plan Kyoto"}, owner) for _ in range(3)]

    assert unknown_trip == [404] * 10 and someone_elses == [404] * 10
    assert no_token_at_all == [401] * 10 and unusable_token == [422] * 10 and invalid_body == [422] * 10
    assert wrong_admin == [401] * 10
    assert admin_run == 404  # not 429: the dataset does not exist, and that run is the one request counted
    assert owners == [200, 200, 429]  # the admin run used one of the three
