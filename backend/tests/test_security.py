from __future__ import annotations

import asyncio
import base64
import json
import sqlite3

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.turn import check_attachments
from app.config import Settings
from app.domain.models import BufferedAttachment
from app.evidence import providers as providers_module
from app.evidence.providers import Providers


def _client(tmp_path, *, local_admin: bool = False) -> TestClient:
    settings = Settings(
        database_path=str(tmp_path / "sec.db"),
        sandbox=True,
        local_admin=local_admin,
        admin_token="test-admin",
        secret_key="test-secret",
    )
    return TestClient(create_app(settings))


def _signup(client: TestClient, email: str, **extra) -> dict:
    response = client.post("/api/account/signup", json={"email": email, "password": "secret1", **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(auth: dict) -> dict:
    return {"Authorization": f"Bearer {auth['token']}"}


def _session_user(tmp_path, session_id: str) -> str:
    db = sqlite3.connect(tmp_path / "sec.db")
    try:
        return db.execute("SELECT user_id FROM sessions WHERE id=?", (session_id,)).fetchone()[0]
    finally:
        db.close()


# --- 1. wishlist open ownership ------------------------------------------


def test_wishlist_open_needs_the_owner(tmp_path):
    with _client(tmp_path) as client:
        db = sqlite3.connect(tmp_path / "sec.db")
        db.execute(
            "INSERT INTO wishlist (id, session_id, identity, destination, summary, stars, body, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "saved-open",
                "s1",
                "guest:owner",
                "Kyoto",
                "A saved trip",
                5,
                json.dumps({}),
                "2026-11-23T00:00:00+00:00",
            ),
        )
        db.commit()
        db.close()
        other = _signup(client, "other@example.com")
        assert client.post("/api/wishlist/saved-open/open").status_code == 403
        assert client.post("/api/wishlist/saved-open/open", headers={"X-Client-Token": "stranger"}).status_code == 403
        assert client.post("/api/wishlist/saved-open/open", headers=_bearer(other)).status_code == 403
        # The owner is let through the ownership check (the empty body then fails as unreadable, not as forbidden).
        assert client.post("/api/wishlist/saved-open/open", headers={"X-Client-Token": "owner"}).status_code != 403


# --- 2. account takeover through attach / signin / signup -----------------


def test_a_trip_owned_by_another_account_cannot_be_claimed(tmp_path):
    with _client(tmp_path) as client:
        u2 = _signup(client, "u2@example.com")
        u3 = _signup(client, "u3@example.com")
        session_id = client.post("/api/journeys", json={}, headers=_bearer(u2)).json()["session"]["id"]
        assert _session_user(tmp_path, session_id) == u2["user"]["id"]

        assert (
            client.post("/api/account/attach", json={"session_id": session_id}, headers=_bearer(u3)).status_code == 403
        )
        signin = client.post(
            "/api/account/signin", json={"email": "u3@example.com", "password": "secret1", "session_id": session_id}
        )
        assert signin.status_code == 200
        _signup(client, "u4@example.com", session_id=session_id)
        assert _session_user(tmp_path, session_id) == u2["user"]["id"]
        # The real owner may still attach their own trip.
        assert (
            client.post("/api/account/attach", json={"session_id": session_id}, headers=_bearer(u2)).status_code == 200
        )


def test_a_guest_trip_is_still_claimable_with_its_token(tmp_path):
    with _client(tmp_path) as client:
        u2 = _signup(client, "u2@example.com")
        session_id = client.post("/api/journeys", json={}, headers={"X-Client-Token": "guest-1"}).json()["session"][
            "id"
        ]
        denied = client.post(
            "/api/account/attach", json={"session_id": session_id, "client_token": "x"}, headers=_bearer(u2)
        )
        assert denied.status_code == 403
        ok = client.post(
            "/api/account/attach", json={"session_id": session_id, "client_token": "guest-1"}, headers=_bearer(u2)
        )
        assert ok.status_code == 200
        assert _session_user(tmp_path, session_id) == u2["user"]["id"]


def test_repeated_wrong_passwords_lock_the_sign_in(tmp_path):
    with _client(tmp_path) as client:
        _signup(client, "lock@example.com")
        for _ in range(10):
            assert (
                client.post("/api/account/signin", json={"email": "lock@example.com", "password": "wrong"}).status_code
                == 401
            )
        assert (
            client.post("/api/account/signin", json={"email": "lock@example.com", "password": "wrong"}).status_code
            == 429
        )
        assert (
            client.post("/api/account/signin", json={"email": "other@example.com", "password": "wrong"}).status_code
            == 401
        )


# --- 3. SSRF guard on page fetches ---------------------------------------

PUBLIC = {"example.com": ["93.184.216.34"], "www.example.com": ["93.184.216.34"]}


def _providers(handler, monkeypatch):
    async def resolve(host, port):
        return PUBLIC.get(host, [host])  # an IP literal resolves to itself

    monkeypatch.setattr(providers_module, "_resolve_host", resolve)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Providers(Settings(database_path="x.db", sandbox=True), client=client), client


def _fetch(url, handler, monkeypatch):
    seen: list[str] = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    async def run():
        providers, client = _providers(spy, monkeypatch)
        try:
            return await providers.page_text(url)
        finally:
            await client.aclose()

    return asyncio.run(run()), seen


def _page(_request):
    return httpx.Response(200, text="<html><body><p>Hello traveller</p></body></html>")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8101/api/admin/escalation",
        "http://127.0.0.1/api/admin/escalation",
        "http://[::1]/",
        "http://10.1.2.3/",
        "http://192.168.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://example.com:8101/",
        "file:///etc/passwd",
        "ftp://example.com/",
    ],
)
def test_page_fetch_refuses_internal_and_odd_urls(url, monkeypatch):
    text, seen = _fetch(url, _page, monkeypatch)
    assert text == ""
    assert seen == []


def test_page_fetch_reads_a_public_page(monkeypatch):
    text, seen = _fetch("https://example.com/guide", _page, monkeypatch)
    assert text == "Hello traveller"
    assert seen == ["https://example.com/guide"]


def test_page_fetch_checks_every_redirect_hop(monkeypatch):
    def handler(request):
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8101/api/admin/escalation"})
        return httpx.Response(200, text="secret")

    text, seen = _fetch("https://example.com/go", handler, monkeypatch)
    assert text == ""
    assert seen == ["https://example.com/go"]


def test_page_fetch_follows_a_public_redirect_but_caps_the_chain(monkeypatch):
    def hop(request):
        if request.url.path == "/a":
            return httpx.Response(301, headers={"Location": "/b"})
        return _page(request)

    assert _fetch("https://example.com/a", hop, monkeypatch)[0] == "Hello traveller"

    def loop(request):
        return httpx.Response(302, headers={"Location": "/again"})

    text, seen = _fetch("https://example.com/start", loop, monkeypatch)
    assert text == ""
    assert len(seen) == providers_module.MAX_PAGE_REDIRECTS + 1


def test_page_fetch_reads_at_most_two_megabytes(monkeypatch):
    def huge(request):
        return httpx.Response(200, text="<p>" + "word " * 1_000_000 + "</p>")

    text, _seen = _fetch("https://example.com/huge", huge, monkeypatch)
    assert 0 < len(text) <= 6000


# --- 4. upload caps and fake images --------------------------------------


def _attachment(name="a.png", media_type="image/png", data=""):
    return BufferedAttachment(name=name, media_type=media_type, data=data)


def test_too_many_or_too_large_attachments_are_rejected():
    with pytest.raises(HTTPException) as many:
        check_attachments([_attachment(data="QQ==")] * 7)
    assert many.value.status_code == 413
    with pytest.raises(HTTPException) as big:
        check_attachments([_attachment(data="A" * (8 * 1024 * 1024 + 1))])
    assert big.value.status_code == 413
    with pytest.raises(HTTPException) as total:
        check_attachments([_attachment(data="A" * (7 * 1024 * 1024))] * 3)
    assert total.value.status_code == 413
    assert len(check_attachments([_attachment(data="QQ==")] * 6)) == 6


def test_garbage_image_is_not_passed_on_as_an_image():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()
    jpeg = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 32).decode()
    fake = base64.b64encode(b"this is not a picture at all, just text").decode()
    clean = check_attachments(
        [
            _attachment(data=png),
            _attachment("b.jpg", "image/jpeg", jpeg),
            _attachment("c.png", data=fake),
            _attachment("d.svg", "image/svg+xml", fake),
            _attachment("e.txt", "text/plain", fake),
        ]
    )
    assert [a.media_type for a in clean] == [
        "image/png",
        "image/jpeg",
        "application/x-unreadable",
        "application/x-unreadable",
        "text/plain",
    ]
    assert clean[2].data == "" and clean[4].data == fake


def test_turn_stream_rejects_oversized_uploads(tmp_path):
    with _client(tmp_path) as client:
        body = {
            "message": "hi",
            "attachments": [{"name": f"{i}.txt", "media_type": "text/plain", "data": "QQ=="} for i in range(7)],
        }
        response = client.post("/api/turn/stream", json=body, headers={"X-Client-Token": "t"})
        assert response.status_code == 413


# --- 5. bad input gives 4xx, never 500 ----------------------------------


def test_malformed_bodies_give_clean_client_errors(tmp_path):
    with TestClient(
        create_app(
            Settings(
                database_path=str(tmp_path / "sec.db"), sandbox=True, local_admin=False, admin_token="a", secret_key="s"
            )
        ),
        raise_server_exceptions=False,
    ) as client:
        headers = {"X-Client-Token": "tester", "Content-Type": "application/json"}
        for body in ("[1, 2]", "null", "{not json", '"text"'):
            assert client.post("/api/account/signup", content=body, headers=headers).status_code == 400
            assert client.post("/api/account/signin", content=body, headers=headers).status_code == 400
            assert client.patch("/api/traveller", content=body, headers=headers).status_code == 400
        for preferences in (5, {"a": 1}, True):
            response = client.patch(
                "/api/traveller", json={"client_token": "tester", "preferences": preferences}, headers=headers
            )
            assert response.status_code == 422
        auth = _signup(client, "x@example.com")
        assert (
            client.post(
                "/api/account/attach", content="[1]", headers={**_bearer(auth), "Content-Type": "application/json"}
            ).status_code
            == 400
        )
        cleared = client.post("/api/recall/clear", headers={"X-Client-Token": "tester"})
        assert cleared.status_code == 200
        assert cleared.json()["cleared"] is True


# --- 6. traveller profile ------------------------------------------------


def test_a_string_preference_is_one_item_and_patch_matches_get(tmp_path):
    with _client(tmp_path) as client:
        headers = {"X-Client-Token": "tester"}
        client.post("/api/journeys", json={}, headers=headers)
        patched = client.patch(
            "/api/traveller", json={"client_token": "tester", "preferences": "vegan"}, headers=headers
        )
        assert patched.status_code == 200
        profile = patched.json()["profile"]
        assert profile["preferences"] == ["vegan"]
        got = client.get("/api/traveller", headers=headers).json()
        assert set(profile) == set(got)
        assert profile["sessions"] == got["sessions"] == 1
        assert profile["destinations"] == got["destinations"]


def test_sessions_count_ignores_sessions_without_a_token(tmp_path):
    with _client(tmp_path) as client:
        auth = _signup(client, "count@example.com")
        client.post("/api/journeys", json={}, headers=_bearer(auth))
        db = sqlite3.connect(tmp_path / "sec.db")
        for sid in ("anon-1", "anon-2"):
            db.execute(
                "INSERT INTO sessions (id, client_token, user_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                (sid, "", "", "2026-11-23T00:00:00+00:00", "2026-11-23T00:00:00+00:00"),
            )
        db.commit()
        db.close()
        assert client.get("/api/traveller", headers=_bearer(auth)).json()["sessions"] == 1
