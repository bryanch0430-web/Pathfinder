"""Wishlist copies, early body caps and the persistent per-address sign-in throttle."""

from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.api import turn as turn_module
from app.api.app import create_app
from app.config import Settings
from app.domain.models import DateRange, DayPlan, Destination, PlanItem, TripPlan

VICTIM = "victim@example.com"


def _settings(tmp_path, **extra) -> Settings:
    return Settings(
        database_path=str(tmp_path / "r2.db"),
        sandbox=True,
        local_admin=False,
        admin_token="test-admin",
        secret_key="test-secret",
        **extra,
    )


def _client(tmp_path, **extra) -> TestClient:
    return TestClient(create_app(_settings(tmp_path, **extra)))


# --- 1. a reopened wishlist trip is its own trip ---------------------------


def test_wishlist_open_gives_the_copy_a_fresh_trip_id(tmp_path):
    headers = {"X-Client-Token": "r2-owner"}
    plan = TripPlan(
        trip_id="trip-original01",
        session_id="old-session",
        destination=Destination(name="Kyoto"),
        dates=DateRange(start="2027-03-05", end="2027-03-06"),
        days=[
            DayPlan(
                date="2027-03-05",
                title="One",
                items=[PlanItem(id="i1", name="Temple", start="09:00", end="10:00")],
            )
        ],
    )
    with _client(tmp_path) as client:
        db = sqlite3.connect(tmp_path / "r2.db")
        db.execute(
            "INSERT INTO wishlist (id, session_id, identity, destination, summary, stars, body, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "saved-r2",
                "old-session",
                "guest:r2-owner",
                "Kyoto",
                "s",
                5,
                plan.model_dump_json(),
                "2026-11-23T00:00:00+00:00",
            ),
        )
        db.commit()
        db.close()
        first = client.post("/api/wishlist/saved-r2/open", headers=headers).json()
        second = client.post("/api/wishlist/saved-r2/open", headers=headers).json()
    ids = {first["plan"]["trip_id"], second["plan"]["trip_id"]}
    assert "trip-original01" not in ids
    assert len(ids) == 2  # two copies, two trips
    for view in (first, second):
        assert view["plan"]["session_id"] == view["session"]["id"]
        assert view["plan"]["trip_id"].startswith("trip-") and len(view["plan"]["trip_id"]) == 17


# --- 2. oversized bodies are refused before they are read ------------------

JSON = {"Content-Type": "application/json"}


@pytest.fixture
def handler_ran(monkeypatch):
    ran = []
    monkeypatch.setattr(turn_module, "check_attachments", lambda a: ran.append(1) or a)
    return ran


def test_turn_body_over_the_cap_is_refused_by_content_length(tmp_path, handler_ran):
    with _client(tmp_path, max_turn_body_bytes=2048) as client:
        response = client.post("/api/turn/stream", content=b'{"message": "' + b"x" * 5000 + b'"}', headers=JSON)
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]
    assert handler_ran == []


def test_turn_body_over_the_cap_is_refused_when_chunked(tmp_path, handler_ran):
    def chunks():
        for _ in range(10):
            yield b"x" * 1000  # no Content-Length: the body is streamed

    with _client(tmp_path, max_turn_body_bytes=2048) as client:
        response = client.post("/api/turn/stream", content=chunks(), headers=JSON)
    assert response.status_code == 413
    assert handler_ran == []


def test_a_body_under_the_cap_still_reaches_the_route(tmp_path, handler_ran):
    with _client(tmp_path, max_turn_body_bytes=2048) as client:
        response = client.post("/api/turn/stream", json={"message": "hello"})
    assert response.status_code != 413
    assert handler_ran == [1]


def test_import_body_over_the_cap_is_refused(tmp_path):
    with _client(tmp_path, max_import_body_bytes=2048) as client:
        big = b'{"plan": {"summary": "' + b"x" * 5000 + b'"}}'
        declared = client.post("/api/journeys/import", content=big, headers=JSON)
        chunked = client.post("/api/journeys/import", content=iter([big[:3000], big[3000:]]), headers=JSON)
        other = client.post("/api/journeys", json={})  # routes without a cap are untouched
    assert declared.status_code == 413
    assert chunked.status_code == 413
    assert other.status_code == 200


# --- 3. the sign-in throttle is persistent and per address -----------------


def _signup(client, email=VICTIM):
    assert client.post("/api/account/signup", json={"email": email, "password": "secret1"}).status_code == 200


def _fail(client, email=VICTIM, headers=None):
    return client.post("/api/account/signin", json={"email": email, "password": "wrong"}, headers=headers or {})


def test_lock_survives_a_restart_on_the_same_database(tmp_path):
    with _client(tmp_path) as client:
        _signup(client)
        assert [_fail(client).status_code for _ in range(10)] == [401] * 10
        locked = _fail(client)
        assert locked.status_code == 429
        assert 1 <= int(locked.headers["Retry-After"]) <= 601
    with _client(tmp_path) as fresh:  # a new app and store on the same file
        assert _fail(fresh).status_code == 429
        right = fresh.post("/api/account/signin", json={"email": VICTIM, "password": "secret1"})
        assert right.status_code == 429  # even the right password waits out the lock


def test_one_address_cannot_lock_the_victim_out_for_others(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app, client=("203.0.113.9", 5000)) as attacker:
        with TestClient(app, client=("198.51.100.7", 5000)) as owner:
            _signup(owner)
            assert [_fail(attacker).status_code for _ in range(10)] == [401] * 10
            assert _fail(attacker).status_code == 429
            ok = owner.post("/api/account/signin", json={"email": VICTIM, "password": "secret1"})
            assert ok.status_code == 200


def test_many_addresses_hit_the_per_email_ceiling(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app, client=("198.51.100.7", 1)) as owner:
        _signup(owner)
    for n in range(5):  # 5 addresses x 10 failures = 50 across the email
        with TestClient(app, client=(f"203.0.113.{n}", 1)) as botnet:
            assert [_fail(botnet).status_code for _ in range(10)] == [401] * 10
    with TestClient(app, client=("203.0.113.99", 1)) as new_address:
        assert _fail(new_address).status_code == 429


def test_success_clears_the_pair_and_old_rows_are_pruned(tmp_path):
    with _client(tmp_path) as client:
        _signup(client)
        assert [_fail(client).status_code for _ in range(9)] == [401] * 9
        assert client.post("/api/account/signin", json={"email": VICTIM, "password": "secret1"}).status_code == 200
        assert [_fail(client).status_code for _ in range(9)] == [401] * 9  # the count started over
        db = sqlite3.connect(tmp_path / "r2.db")
        db.execute(
            "INSERT INTO signin_failures (email, address, at) VALUES (?,?,?)",
            ("old@example.com", "x", time.time() - 7200),
        )
        db.commit()
        _fail(client, "someone@example.com")
        assert db.execute("SELECT COUNT(*) FROM signin_failures WHERE email='old@example.com'").fetchone()[0] == 0
        db.close()


def test_forwarded_address_is_used_behind_the_configured_proxy(tmp_path):
    app = create_app(_settings(tmp_path, local_admin_proxy="192.0.2.1"))
    with TestClient(app, client=("192.0.2.1", 1)) as proxy:
        _signup(proxy)
        attacker = {"X-Forwarded-For": "203.0.113.50"}
        for _ in range(10):
            _fail(proxy, headers=attacker)
        assert _fail(proxy, headers=attacker).status_code == 429
        elsewhere = proxy.post(
            "/api/account/signin",
            json={"email": VICTIM, "password": "secret1"},
            headers={"X-Forwarded-For": "198.51.100.7"},
        )
        assert elsewhere.status_code == 200
