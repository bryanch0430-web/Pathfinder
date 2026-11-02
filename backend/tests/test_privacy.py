"""P0 privacy: memory, saved trips and profiles belong to one caller.

Identity rule under test: a valid bearer is ``user:<id>``; otherwise a guest
``client_token``; a ``user:`` token without that user's bearer, or no identity
at all, is refused with 401.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.application import trending_rag
from app.application.memory import MemoryService, embed
from app.config import Settings
from app.domain.models import SavedTrip, TripContext
from app.infrastructure.observability import start_trace
from app.infrastructure.store import SQLiteStore
from app.interface.api import create_app
from scripts.seed_platform_demo import DESTINATIONS, _plan, _saved_trip

PASSWORD = "correct-horse"


@pytest.fixture()
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def _store(client: TestClient) -> SQLiteStore:
    return client.app.state.container.store


def _signup(client: TestClient, email: str, client_token: str = "") -> tuple[dict[str, str], str]:
    response = client.post(
        "/api/account/signup",
        json={"email": email, "password": PASSWORD, "client_token": client_token},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["profile_token"]


def _save_useful_trip(
    client: TestClient,
    *,
    client_token: str,
    headers: dict[str, str] | None = None,
    trip_no: int = 1,
    destination: str = "Kyoto",
) -> str:
    """Create a session as the caller, give it a plan and mark the plan useful."""
    headers = headers or {}
    created = client.post("/api/journeys", json={"client_token": client_token}, headers=headers)
    assert created.status_code == 200, created.text
    session_id = created.json()["session"]["id"]
    place = next(item for item in DESTINATIONS if item["name"] == destination)
    plan = _plan(random.Random(trip_no), place, trip_no)
    plan = plan.model_copy(update={"session_id": session_id, "trip_id": f"trip_privacy_{trip_no:03d}"})
    _store(client).save_plan(plan, "test plan")
    rated = client.post(
        f"/api/journeys/{session_id}/rating",
        json={"useful": True, "stars": 5, "client_token": client_token},
        headers=headers,
    )
    assert rated.status_code == 200, rated.text
    return plan.trip_id


def _wishlist(client: TestClient, client_token: str = "", headers: dict[str, str] | None = None) -> list[str]:
    params = {"client_token": client_token} if client_token else {}
    response = client.get("/api/wishlist", params=params, headers=headers or {})
    assert response.status_code == 200, response.text
    return [trip["trip_id"] for trip in response.json()["trips"]]


def _preferences(client: TestClient, client_token: str = "", headers: dict[str, str] | None = None) -> list[str]:
    params = {"client_token": client_token} if client_token else {}
    response = client.get("/api/traveller", params=params, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()["preferences"]


# -- saved trips --------------------------------------------------------------


def test_user_cannot_see_another_users_wishlist(client: TestClient) -> None:
    a_headers, a_token = _signup(client, "a@example.com")
    b_headers, b_token = _signup(client, "b@example.com")
    a_trip = _save_useful_trip(client, client_token=a_token, headers=a_headers, trip_no=1)
    b_trip = _save_useful_trip(client, client_token=b_token, headers=b_headers, trip_no=2)

    assert _wishlist(client, a_token, a_headers) == [a_trip]
    assert _wishlist(client, b_token, b_headers) == [b_trip]
    # The bearer alone is enough to identify a signed-in caller.
    assert _wishlist(client, headers=a_headers) == [a_trip]
    # The profile counts only the caller's own saved trips.
    profile = client.get("/api/traveller", headers=b_headers).json()
    assert profile["saved_trips"] == 1


def test_guest_cannot_see_a_users_wishlist(client: TestClient) -> None:
    a_headers, a_token = _signup(client, "a@example.com")
    a_trip = _save_useful_trip(client, client_token=a_token, headers=a_headers, trip_no=1)
    g_trip = _save_useful_trip(client, client_token="guest-g", trip_no=2)

    assert _wishlist(client, "guest-g") == [g_trip]
    assert a_trip not in _wishlist(client, "guest-other")
    assert _wishlist(client, a_token, a_headers) == [a_trip]


def test_saving_the_same_trip_twice_keeps_one_row_per_owner(client: TestClient) -> None:
    created = client.post("/api/journeys", json={"client_token": "guest-dup"}).json()
    session_id = created["session"]["id"]
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    plan = _plan(random.Random(5), place, 5).model_copy(update={"session_id": session_id})
    _store(client).save_plan(plan, "test plan")
    for _ in range(2):
        rated = client.post(
            f"/api/journeys/{session_id}/rating",
            json={"useful": True, "stars": 4, "client_token": "guest-dup"},
        )
        assert rated.status_code == 200
    assert _wishlist(client, "guest-dup") == [plan.trip_id]


# -- clear memory -------------------------------------------------------------


def test_clear_memory_only_touches_the_caller(client: TestClient) -> None:
    a_headers, a_token = _signup(client, "a@example.com")
    b_headers, b_token = _signup(client, "b@example.com")
    _save_useful_trip(client, client_token=a_token, headers=a_headers, trip_no=1)
    b_trip = _save_useful_trip(client, client_token=b_token, headers=b_headers, trip_no=2)
    client.patch("/api/traveller", json={"client_token": a_token, "preferences": ["museums"]}, headers=a_headers)
    client.patch("/api/traveller", json={"client_token": b_token, "preferences": ["hiking"]}, headers=b_headers)

    cleared = client.post("/api/recall/clear", json={"client_token": a_token}, headers=a_headers)

    assert cleared.status_code == 200
    assert cleared.json() == {"cleared": True, "saved_trips": 1, "preferences": 1}
    assert _wishlist(client, a_token, a_headers) == []
    assert _preferences(client, a_token, a_headers) == []
    assert _wishlist(client, b_token, b_headers) == [b_trip]
    assert _preferences(client, b_token, b_headers) == ["hiking"]


def test_guest_clear_memory_leaves_users_intact(client: TestClient) -> None:
    a_headers, a_token = _signup(client, "a@example.com")
    a_trip = _save_useful_trip(client, client_token=a_token, headers=a_headers, trip_no=1)
    _save_useful_trip(client, client_token="guest-g", trip_no=2)

    cleared = client.post("/api/recall/clear", json={"client_token": "guest-g"})

    assert cleared.status_code == 200
    assert cleared.json()["saved_trips"] == 1
    assert _wishlist(client, "guest-g") == []
    assert _wishlist(client, a_token, a_headers) == [a_trip]


# -- identity is required and cannot be spoofed --------------------------------


def test_privacy_endpoints_require_an_identity(client: TestClient) -> None:
    assert client.get("/api/wishlist").status_code == 401
    assert client.post("/api/recall/clear").status_code == 401
    assert client.post("/api/recall/clear", json={}).status_code == 401
    assert client.get("/api/traveller").status_code == 401
    assert client.patch("/api/traveller", json={"preferences": ["museums"]}).status_code == 401


def test_user_profile_token_needs_the_matching_bearer(client: TestClient) -> None:
    a_headers, a_token = _signup(client, "a@example.com")
    b_headers, _ = _signup(client, "b@example.com")
    a_trip = _save_useful_trip(client, client_token=a_token, headers=a_headers, trip_no=1)
    client.patch("/api/traveller", json={"client_token": a_token, "preferences": ["museums"]}, headers=a_headers)
    forged = {"Authorization": "Bearer not.a.token"}

    for headers in ({}, forged, b_headers):
        assert client.get("/api/traveller", params={"client_token": a_token}, headers=headers).status_code == 401
        assert (
            client.patch(
                "/api/traveller", json={"client_token": a_token, "preferences": ["spoofed"]}, headers=headers
            ).status_code
            == 401
        )
        assert client.get("/api/wishlist", params={"client_token": a_token}, headers=headers).status_code == 401
        assert client.post("/api/recall/clear", json={"client_token": a_token}, headers=headers).status_code == 401

    # Nothing of A's changed, and A still reaches it with A's own bearer.
    assert _preferences(client, a_token, a_headers) == ["museums"]
    assert _wishlist(client, a_token, a_headers) == [a_trip]


def test_rating_cannot_write_into_another_users_profile(client: TestClient) -> None:
    a_headers, a_token = _signup(client, "a@example.com")
    client.patch("/api/traveller", json={"client_token": a_token, "preferences": ["museums"]}, headers=a_headers)
    session_id = client.post("/api/journeys", json={"client_token": "guest-x"}).json()["session"]["id"]
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    plan = _plan(random.Random(7), place, 7).model_copy(update={"session_id": session_id})
    _store(client).save_plan(plan, "test plan")

    spoofed = client.post(
        f"/api/journeys/{session_id}/rating",
        json={"useful": True, "stars": 5, "client_token": a_token},
    )

    assert spoofed.status_code == 401
    assert _preferences(client, a_token, a_headers) == ["museums"]
    assert _wishlist(client, a_token, a_headers) == []


def test_session_cannot_be_created_for_another_users_profile(client: TestClient) -> None:
    a_headers, a_token = _signup(client, "a@example.com")
    b_headers, _ = _signup(client, "b@example.com")
    assert client.post("/api/journeys", json={"client_token": a_token}).status_code == 401
    assert client.post("/api/journeys", json={"client_token": a_token}, headers=b_headers).status_code == 401
    assert client.post("/api/journeys", json={"client_token": a_token}, headers=a_headers).status_code == 200


# -- first sign-in moves guest memory -------------------------------------------


def test_guest_trips_move_to_the_user_on_sign_up(client: TestClient) -> None:
    g_trip = _save_useful_trip(client, client_token="guest-move", trip_no=1)
    client.patch("/api/traveller", json={"client_token": "guest-move", "preferences": ["street food"]})

    headers, token = _signup(client, "mover@example.com", client_token="guest-move")

    assert _wishlist(client, token, headers) == [g_trip]
    assert "street food" in _preferences(client, token, headers)
    assert _wishlist(client, "guest-move") == []


def test_guest_trips_move_to_the_user_on_sign_in(client: TestClient) -> None:
    _signup(client, "returning@example.com")
    g_trip = _save_useful_trip(client, client_token="guest-later", trip_no=3)

    signed_in = client.post(
        "/api/account/signin",
        json={"email": "returning@example.com", "password": PASSWORD, "client_token": "guest-later"},
    )

    assert signed_in.status_code == 200
    body = signed_in.json()
    headers = {"Authorization": f"Bearer {body['token']}"}
    assert _wishlist(client, body["profile_token"], headers) == [g_trip]
    assert _wishlist(client, "guest-later") == []


def test_sign_in_cannot_absorb_another_users_memory(client: TestClient) -> None:
    a_headers, a_token = _signup(client, "a@example.com")
    a_trip = _save_useful_trip(client, client_token=a_token, headers=a_headers, trip_no=1)
    client.patch("/api/traveller", json={"client_token": a_token, "preferences": ["museums"]}, headers=a_headers)
    _signup(client, "b@example.com")

    signed_in = client.post(
        "/api/account/signin",
        json={"email": "b@example.com", "password": PASSWORD, "client_token": a_token},
    )

    assert signed_in.status_code == 200
    b_headers = {"Authorization": f"Bearer {signed_in.json()['token']}"}
    assert _wishlist(client, headers=b_headers) == []
    assert "museums" not in _preferences(client, headers=b_headers)
    assert _wishlist(client, a_token, a_headers) == [a_trip]
    assert _preferences(client, a_token, a_headers) == ["museums"]


def test_moving_a_trip_the_user_already_saved_keeps_one_row(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "move.db")
    store.save_saved_trip(
        SavedTrip(trip_id="t1", destination="Kyoto", summary="user copy", embedding=[0.0]), owner="user:u"
    )
    store.save_saved_trip(
        SavedTrip(trip_id="t1", destination="Kyoto", summary="guest copy", embedding=[0.0]), owner="guest-g"
    )

    assert store.reassign_saved_trips("guest-g", "user:u") == 1
    assert [trip.summary for trip in store.list_saved_trips_for_owner("user:u")] == ["guest copy"]
    assert store.list_saved_trips_for_owner("guest-g") == []


# -- migration ------------------------------------------------------------------

LEGACY_KEPT_JOURNEYS = """
CREATE TABLE kept_journeys (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  destination TEXT NOT NULL,
  rating REAL NOT NULL DEFAULT 0,
  stars INTEGER,
  summary TEXT NOT NULL DEFAULT '',
  embedding TEXT NOT NULL,
  data TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def test_migration_adds_owner_to_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy = SavedTrip(trip_id="trip_legacy", destination="Kyoto", summary="old row", embedding=[0.0])
    with sqlite3.connect(path) as conn:
        conn.executescript(LEGACY_KEPT_JOURNEYS)
        conn.execute(
            "INSERT INTO kept_journeys (id, trip_id, destination, rating, stars, summary, embedding, data, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (legacy.id, legacy.trip_id, "Kyoto", 1.0, None, "old row", "[0.0]", legacy.model_dump_json(),
             legacy.created_at.isoformat()),
        )
    conn.close()

    store = SQLiteStore(path)
    SQLiteStore(path)  # idempotent: a second start must not fail

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(kept_journeys)")}
        owners = [row[0] for row in conn.execute("SELECT owner FROM kept_journeys")]
    conn.close()
    assert "owner" in columns
    assert owners == [None]
    # Legacy rows have no owner, so no per-user list ever returns them.
    assert store.list_saved_trips_for_owner("", 20) == []
    assert store.list_saved_trips_for_owner("guest-any", 20) == []
    assert store.list_saved_trips_for_owner("user:any", 20) == []
    # New rows carry their owner.
    store.save_saved_trip(SavedTrip(trip_id="trip_new", destination="Kyoto", embedding=[0.0]), owner="guest-new")
    assert [trip.trip_id for trip in store.list_saved_trips_for_owner("guest-new", 20)] == ["trip_new"]


# -- planner recall ---------------------------------------------------------------

PRIVATE_SUMMARY = "Kyoto for Grandma Lee's 80th, call +852 5555 0000 about the ryokan"


def _kyoto_context(session_id: str) -> TripContext:
    start = date.today() + timedelta(days=30)
    return TripContext(
        session_id=session_id,
        destination="Kyoto",
        start_date=start,
        end_date=start + timedelta(days=2),
        party_size=2,
        preferences=["slow pace", "food"],
    )


def _save_private_trip(store: SQLiteStore, owner: str) -> None:
    store.save_saved_trip(
        SavedTrip(
            trip_id="trip_private",
            destination="Kyoto",
            rating=1.0,
            stars=5,
            summary=PRIVATE_SUMMARY,
            embedding=embed("Kyoto temples food three days two travellers slow pace"),
        ),
        owner=owner,
    )


def test_recall_returns_only_the_owners_trips(settings: Settings, container) -> None:
    _save_private_trip(container.store, "user:ua")
    memory = MemoryService(container.store, settings)
    context = _kyoto_context("s")

    assert [trip.summary for _, trip in memory.recall(context, owner="user:ua")] == [PRIVATE_SUMMARY]
    assert memory.recall(context, owner="user:ub") == []
    assert memory.recall(context, owner="guest-g") == []
    assert memory.recall(context, owner="") == []


def _planner_memory(settings: Settings, container, monkeypatch: pytest.MonkeyPatch, session_id: str) -> list[str]:
    """The saved_trip_summaries the plan path would put in the planner prompt."""
    orchestrator = container.turn_service
    captured: list[list[str]] = []

    async def fake_run(**kwargs):
        captured.append(kwargs["memory_summaries"])
        raise RuntimeError("stop after capturing the prompt inputs")

    monkeypatch.setattr(orchestrator.plan_path, "run", fake_run)

    async def drive() -> None:
        with pytest.raises(RuntimeError, match="stop after"):
            async for _ in orchestrator._plan(
                context=_kyoto_context(session_id),
                session_id=session_id,
                tracer=start_trace(settings, "req_recall"),
                request_id="req_recall",
            ):
                pass

    asyncio.run(drive())
    return captured[-1]


def test_planner_prompt_never_sees_another_travellers_trip(
    settings: Settings, container, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = container.store
    _save_private_trip(store, "user:ua")
    a_session = store.create_session("guest-a", user_id="ua")
    b_session = store.create_session("guest-b", user_id="ub")
    guest_session = store.create_session("guest-g")
    unowned_session = store.create_session("")
    spoofed_session = store.create_session("user:ua")  # a user: token never proven with a bearer

    own = _planner_memory(settings, container, monkeypatch, a_session.id)
    assert len(own) == 1 and "Grandma Lee" in own[0]
    for session in (b_session, guest_session, unowned_session, spoofed_session):
        assert _planner_memory(settings, container, monkeypatch, session.id) == []


def test_guest_recall_uses_the_guest_token(settings: Settings, container, monkeypatch: pytest.MonkeyPatch) -> None:
    store = container.store
    _save_private_trip(store, "guest-g")
    assert len(_planner_memory(settings, container, monkeypatch, store.create_session("guest-g").id)) == 1
    assert _planner_memory(settings, container, monkeypatch, store.create_session("guest-h").id) == []


def test_modify_prompt_never_sees_another_travellers_trip(
    settings: Settings, container, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = container.store
    _save_private_trip(store, "user:ua")
    orchestrator = container.turn_service
    captured: list[list[str]] = []

    async def fake_run(**kwargs):
        captured.append(kwargs["memory_summaries"])
        raise RuntimeError("stop after capturing the prompt inputs")

    monkeypatch.setattr(orchestrator.modify_path, "run", fake_run)

    def modify_memory(session_id: str) -> list[str]:
        async def drive() -> None:
            with pytest.raises(RuntimeError, match="stop after"):
                async for _ in orchestrator._modify(
                    plan=SimpleNamespace(trip_id="t"),
                    context=_kyoto_context(session_id),
                    message="Swap the hotel",
                    decision=SimpleNamespace(affected_parts=["hotel"]),
                    session_id=session_id,
                    tracer=start_trace(settings, "req_modify"),
                    request_id="req_modify",
                ):
                    pass

        asyncio.run(drive())
        return captured[-1]

    assert len(modify_memory(store.create_session("", user_id="ua").id)) == 1
    assert modify_memory(store.create_session("", user_id="ub").id) == []


# -- /api/popular -------------------------------------------------------------------


def _seed_trending(client: TestClient) -> None:
    store = _store(client)
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    rng = random.Random(11)
    for index in range(10):
        store.save_saved_trip(_saved_trip(rng, _plan(rng, place, 100 + index)))
    trending_rag.invalidate()


def _matches(response) -> list[str]:
    return [tag for item in response.json()["items"] for tag in item.get("match", [])]


def test_popular_cannot_probe_another_users_preferences(client: TestClient) -> None:
    a_headers, a_token = _signup(client, "a@example.com")
    b_headers, _ = _signup(client, "b@example.com")
    client.patch("/api/traveller", json={"client_token": a_token, "preferences": ["food"]}, headers=a_headers)
    _seed_trending(client)

    for headers in ({}, {"Authorization": "Bearer not.a.token"}, b_headers):
        assert client.get("/api/popular", params={"client_token": a_token}, headers=headers).status_code == 401

    own = client.get("/api/popular", params={"client_token": a_token}, headers=a_headers)
    assert own.status_code == 200
    assert "food" in _matches(own)
    assert "food" in _matches(client.get("/api/popular", headers=a_headers))


def test_popular_stays_public_and_matches_guests(client: TestClient) -> None:
    client.patch("/api/traveller", json={"client_token": "guest-pop", "preferences": ["food"]})
    _seed_trending(client)

    public = client.get("/api/popular")
    assert public.status_code == 200
    assert public.json()["items"]
    assert _matches(public) == []

    guest = client.get("/api/popular", params={"client_token": "guest-pop"})
    assert guest.status_code == 200
    assert "food" in _matches(guest)
