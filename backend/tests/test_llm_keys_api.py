"""The /api/llm routes: add, test, choose and remove a caller's own LLM keys, and the operator's server key."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

import app.application.orchestrator as orchestrator_module
from app.config import Settings
from app.domain.credentials import SERVER_OWNER
from app.domain.models import GeoLocation, GeoPoint
from app.infrastructure.http import override_default_transport
from app.infrastructure.llm_providers import PROVIDERS
from app.infrastructure.secret_box import SecretBox, mask_key
from app.interface.api import create_app
from tests.test_caller_binding import TRIP, Recorder, Transported

KEY = "sk-ant-api-test-0000001111"
GUEST_A = {"X-Client-Token": "guest-a"}
GUEST_B = {"X-Client-Token": "guest-b"}
NO_SUCH_KEY = "No such key."


@pytest.fixture()
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture()
def replies() -> dict[str, Any]:
    """Per host: ``(status, json)`` to answer with instead of the recorder's answer, or an exception to raise."""
    return {}


@pytest.fixture()
def client(settings: Settings, recorder: Recorder, replies: dict[str, Any]) -> Iterator[TestClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        answer = recorder(request)
        reply = replies.get(request.url.host)
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            return answer
        status, body = reply
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    app = create_app(settings)
    app.add_middleware(Transported, transport=transport)
    with override_default_transport(transport), TestClient(app) as test_client:
        yield test_client


def _save(client: TestClient, headers: dict[str, str], *, provider: str = "anthropic", key: str = KEY, **extra: Any):
    return client.post("/api/llm/keys", json={"provider": provider, "api_key": key, "model": "", **extra}, headers=headers)


def _saved_id(client: TestClient, headers: dict[str, str], **kwargs: Any) -> str:
    response = _save(client, headers, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["key"]["id"]


def _store(client: TestClient):
    return client.app.state.container.store


def _box(client: TestClient) -> SecretBox:
    return client.app.state.container.box


def test_providers_list(client: TestClient) -> None:
    response = client.get("/api/llm/providers")

    assert response.status_code == 200
    providers = response.json()["providers"]
    assert [row["id"] for row in providers] == list(PROVIDERS)
    for row in providers:
        assert set(row) == {"id", "label", "default_model", "key_prefix", "supports_search"}
        spec = PROVIDERS[row["id"]]
        assert (row["label"], row["default_model"], row["key_prefix"], row["supports_search"]) == (
            spec.label, spec.default_model, spec.key_prefix, spec.supports_search,
        )
    assert response.headers["cache-control"] == "private, no-store"


def test_save_lists_masked_and_activates(client: TestClient) -> None:
    saved = _save(client, GUEST_A)

    assert saved.status_code == 200, saved.text
    view = saved.json()["key"]
    assert "warning" not in saved.json()
    assert view["key_tail"] == mask_key(KEY)
    assert view["provider"] == "anthropic" and view["label"] == "Anthropic Claude"
    assert view["model"] == PROVIDERS["anthropic"].default_model
    assert view["active"] is True and view["last_test_ok"] is True and view["unreadable"] is False
    assert set(view) == {
        "id", "provider", "label", "model", "key_tail", "active", "use_for_search",
        "last_test_ok", "last_tested_at", "last_error", "unreadable",
    }
    assert KEY not in saved.text

    listed = client.get("/api/llm/keys", headers=GUEST_A)
    assert listed.status_code == 200
    body = listed.json()
    assert [row["key_tail"] for row in body["keys"]] == [mask_key(KEY)]
    assert body["status"]["source"] == "byok"
    assert body["status"]["provider"] == "anthropic"
    assert body["status"]["model"] == PROVIDERS["anthropic"].default_model
    assert body["status"]["local"] == {"configured": False, "reachable": False, "model": ""}
    assert KEY not in listed.text


def test_save_refuses_bad_key(client: TestClient, recorder: Recorder) -> None:
    recorder.status["api.anthropic.com"] = 401

    refused = _save(client, GUEST_A)

    assert refused.status_code == 422
    assert refused.json()["kind"] == "auth"
    assert refused.json()["detail"]
    assert KEY not in refused.text
    assert _store(client).list_credentials("guest-a") == []
    assert client.get("/api/llm/keys", headers=GUEST_A).json()["keys"] == []


def test_save_allows_rate_limited_with_warning(client: TestClient, recorder: Recorder) -> None:
    recorder.status["api.anthropic.com"] = 429

    saved = _save(client, GUEST_A)

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["warning"]
    assert body["key"]["last_test_ok"] is False and body["key"]["active"] is True
    assert KEY not in saved.text
    assert len(_store(client).list_credentials("guest-a")) == 1


LOW_CREDIT = {
    "type": "error",
    "error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."},
}


def test_save_allows_out_of_credit_with_warning(client: TestClient, replies: dict[str, Any]) -> None:
    replies["api.anthropic.com"] = (400, LOW_CREDIT)

    saved = _save(client, GUEST_A)

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["warning"] and body["key"]["last_test_ok"] is False and body["key"]["active"] is True
    assert body["key"]["last_error"] == body["warning"]
    assert KEY not in saved.text
    assert len(_store(client).list_credentials("guest-a")) == 1


def test_save_allows_network_failure_with_warning(client: TestClient, replies: dict[str, Any]) -> None:
    replies["api.anthropic.com"] = httpx.ConnectError("connection refused")

    saved = _save(client, GUEST_A)

    assert saved.status_code == 200, saved.text
    assert saved.json()["warning"] and saved.json()["key"]["last_test_ok"] is False
    assert len(_store(client).list_credentials("guest-a")) == 1


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (404, {"type": "error", "error": {"type": "not_found_error", "message": "model: claude-nope"}}, "model"),
        (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "something else"}}, "other"),
    ],
)
def test_save_refuses_model_and_other_failures(
    client: TestClient, replies: dict[str, Any], status: int, body: dict[str, Any], kind: str
) -> None:
    replies["api.anthropic.com"] = (status, body)

    refused = _save(client, GUEST_A)

    assert refused.status_code == 422, refused.text
    assert refused.json()["kind"] == kind
    assert KEY not in refused.text
    assert _store(client).list_credentials("guest-a") == []


def test_save_refuses_an_unknown_failure_kind(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.interface.llm_keys_api as keys_api
    from app.infrastructure.llm_providers import KeyTestResult

    async def novel(*_: Any) -> KeyTestResult:
        return KeyTestResult(False, "something_new", "It failed in a new way.", 1)

    monkeypatch.setattr(keys_api, "probe_key", novel)

    refused = _save(client, GUEST_A)

    assert refused.status_code == 422
    assert refused.json() == {"detail": "It failed in a new way.", "kind": "something_new"}
    assert _store(client).list_credentials("guest-a") == []


def test_save_strips_whitespace(client: TestClient, recorder: Recorder) -> None:
    saved = _save(client, GUEST_A, provider="openai", key="  sk-abc12345678\n")

    assert saved.status_code == 200, saved.text
    [row] = _store(client).list_credentials("guest-a")
    assert _box(client).decrypt(row.ciphertext) == "sk-abc12345678"
    sent = recorder.to("api.openai.com")
    assert sent and all(call["key"] == "sk-abc12345678" for call in sent)


def test_owner_isolation_404(client: TestClient) -> None:
    key_id = _saved_id(client, GUEST_A)

    responses = [
        client.patch(f"/api/llm/keys/{key_id}", json={"model": "other"}, headers=GUEST_B),
        client.post(f"/api/llm/keys/{key_id}/test", headers=GUEST_B),
        client.delete(f"/api/llm/keys/{key_id}", headers=GUEST_B),
        client.put("/api/llm/choice", json={"key_id": key_id}, headers=GUEST_B),
    ]

    for response in responses:
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == NO_SUCH_KEY
    assert client.get("/api/llm/keys", headers=GUEST_B).json()["keys"] == []
    [view] = client.get("/api/llm/keys", headers=GUEST_A).json()["keys"]
    assert view["id"] == key_id and view["active"] is True
    assert view["model"] == PROVIDERS["anthropic"].default_model


def test_scopes_do_not_reach_each_others_keys(client: TestClient, settings: Settings) -> None:
    admin = {"X-Admin-Token": settings.admin_token}
    server_id = client.post(
        "/api/llm/keys?scope=server", json={"provider": "anthropic", "api_key": KEY, "model": ""}, headers=admin
    ).json()["key"]["id"]
    guest_id = _saved_id(client, GUEST_A)

    def attempts(key_id: str, path: str, headers: dict[str, str]) -> list[httpx.Response]:
        return [
            client.patch(f"/api/llm/keys/{key_id}{path}", json={"model": "other"}, headers=headers),
            client.post(f"/api/llm/keys/{key_id}/test{path}", headers=headers),
            client.delete(f"/api/llm/keys/{key_id}{path}", headers=headers),
            client.put(f"/api/llm/choice{path}", json={"key_id": key_id}, headers=headers),
        ]

    # A guest acting on the server's key id, and the admin's server scope acting on a guest's key id.
    for response in attempts(server_id, "", GUEST_B) + attempts(guest_id, "?scope=server", admin):
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == NO_SUCH_KEY
    [server_row] = _store(client).list_credentials(SERVER_OWNER)
    [guest_row] = _store(client).list_credentials("guest-a")
    assert (server_row.id, server_row.active, server_row.model) == (server_id, True, PROVIDERS["anthropic"].default_model)
    assert (guest_row.id, guest_row.active, guest_row.model) == (guest_id, True, PROVIDERS["anthropic"].default_model)


def test_unknown_id_404(client: TestClient) -> None:
    for response in (
        client.patch("/api/llm/keys/nope", json={"model": "x"}, headers=GUEST_A),
        client.post("/api/llm/keys/nope/test", headers=GUEST_A),
        client.delete("/api/llm/keys/nope", headers=GUEST_A),
        client.put("/api/llm/choice", json={"key_id": "nope"}, headers=GUEST_A),
    ):
        assert response.status_code == 404
        assert response.json()["detail"] == NO_SUCH_KEY


def test_choice_null_returns_default(client: TestClient) -> None:
    key_id = _saved_id(client, GUEST_A)

    cleared = client.put("/api/llm/choice", json={"key_id": None}, headers=GUEST_A)

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["status"]["source"] == "none"
    assert [row["active"] for row in client.get("/api/llm/keys", headers=GUEST_A).json()["keys"]] == [False]

    chosen = client.put("/api/llm/choice", json={"key_id": key_id}, headers=GUEST_A)
    assert chosen.json()["status"]["source"] == "byok"


def test_patch_and_delete_take_effect_on_the_next_request(client: TestClient) -> None:
    key_id = _saved_id(client, GUEST_A)
    selector = client.app.state.container.selector
    assert selector.for_caller("guest-a").model == PROVIDERS["anthropic"].default_model

    patched = client.patch(f"/api/llm/keys/{key_id}", json={"model": " claude-other ", "use_for_search": False}, headers=GUEST_A)

    assert patched.status_code == 200, patched.text
    assert patched.json()["key"]["model"] == "claude-other"
    assert patched.json()["key"]["use_for_search"] is False
    assert selector.for_caller("guest-a").model == "claude-other"
    assert selector.for_caller("guest-a").llm.name == "claude-other"

    reset = client.patch(f"/api/llm/keys/{key_id}", json={"model": ""}, headers=GUEST_A)
    assert reset.json()["key"]["model"] == PROVIDERS["anthropic"].default_model

    deleted = client.delete(f"/api/llm/keys/{key_id}", headers=GUEST_A)
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
    assert selector.for_caller("guest-a").source == "none"
    assert selector.secret_for(key_id) == ""
    assert client.get("/api/llm/keys", headers=GUEST_A).json()["keys"] == []


def test_retest_keeps_the_cached_key_for_scrubbing(client: TestClient) -> None:
    key_id = _saved_id(client, GUEST_A)
    selector = client.app.state.container.selector
    selector.for_caller("guest-a")  # a turn in flight has built the writer from the row
    assert selector.secret_for(key_id) == KEY

    retested = client.post(f"/api/llm/keys/{key_id}/test", headers=GUEST_A)

    assert retested.status_code == 200, retested.text
    assert retested.json()["result"]["ok"] is True
    assert selector.secret_for(key_id) == KEY


def test_server_scope_requires_admin(client: TestClient, settings: Settings) -> None:
    body = {"provider": "anthropic", "api_key": KEY, "model": ""}
    assert client.post("/api/llm/keys?scope=server", json=body).status_code == 401
    assert client.post("/api/llm/keys?scope=server", json=body, headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/api/llm/keys?scope=server", headers=GUEST_A).status_code == 401

    admin = {"X-Admin-Token": settings.admin_token}
    saved = client.post("/api/llm/keys?scope=server", json=body, headers=admin)

    assert saved.status_code == 200, saved.text
    assert [row.owner for row in _store(client).list_credentials(SERVER_OWNER)] == [SERVER_OWNER]
    server_view = client.get("/api/llm/keys?scope=server", headers=admin).json()
    assert [row["id"] for row in server_view["keys"]] == [saved.json()["key"]["id"]]
    assert server_view["status"]["source"] == "server"
    guest = client.get("/api/llm/keys", headers=GUEST_B).json()
    assert guest["keys"] == []
    assert guest["status"]["source"] == "server"


def test_server_token_is_not_the_server_scope(client: TestClient, settings: Settings) -> None:
    admin = {"X-Admin-Token": settings.admin_token}
    server_id = client.post(
        "/api/llm/keys?scope=server", json={"provider": "anthropic", "api_key": KEY, "model": ""}, headers=admin
    ).json()["key"]["id"]
    pretender = {"X-Client-Token": SERVER_OWNER}

    for headers in (pretender, {**pretender, **admin}):
        responses = [
            client.get("/api/llm/keys", headers=headers),
            _save(client, headers),
            client.patch(f"/api/llm/keys/{server_id}", json={"model": "x"}, headers=headers),
            client.post(f"/api/llm/keys/{server_id}/test", headers=headers),
            client.put("/api/llm/choice", json={"key_id": None}, headers=headers),
            client.delete(f"/api/llm/keys/{server_id}", headers=headers),
        ]
        for response in responses:
            assert response.status_code == 401, response.text
            assert response.json()["detail"] == "Sign in or send a client token."
    [row] = _store(client).list_credentials(SERVER_OWNER)
    assert row.id == server_id and row.active and row.model == PROVIDERS["anthropic"].default_model


def test_unreadable_row_listed(client: TestClient, settings: Settings) -> None:
    other_box = SecretBox(settings.model_copy(update={"auth_secret": "a-different-secret-" + "x" * 32}))
    _store(client).add_credential(
        "guest-a", "anthropic", "", other_box.encrypt(KEY), mask_key(KEY), False, True, ""
    )

    listed = client.get("/api/llm/keys", headers=GUEST_A)

    assert listed.status_code == 200, listed.text
    [view] = listed.json()["keys"]
    assert view["unreadable"] is True and view["key_tail"] == mask_key(KEY)


def test_key_test_rate_limited(client: TestClient, recorder: Recorder) -> None:
    body = {"provider": "anthropic", "api_key": KEY, "model": ""}
    for _ in range(6):
        tested = client.post("/api/llm/keys/test", json=body, headers=GUEST_A)
        assert tested.status_code == 200, tested.text
        assert tested.json()["ok"] is True and tested.json()["kind"] == "ok"
        assert set(tested.json()) == {"ok", "kind", "message", "latency_ms", "models"}
        assert KEY not in tested.text

    refused = client.post("/api/llm/keys/test", json=body, headers=GUEST_A)

    assert refused.status_code == 429
    assert refused.headers["retry-after"]
    assert _store(client).list_credentials("guest-a") == []


def test_validation_does_not_echo_key(client: TestClient, recorder: Recorder) -> None:
    long_key = "sk-" + "Z" * 597
    refused = client.post(
        "/api/llm/keys", json={"provider": "anthropic", "api_key": long_key, "model": ""}, headers=GUEST_A
    )

    assert refused.status_code == 422
    assert refused.json()["detail"] == "The key must be 8 to 512 printable characters."
    assert long_key not in refused.text

    # Other refusals do not repeat the key either, as FastAPI's own validation answer would.
    for body in (
        {"api_key": KEY, "model": ""},
        {"provider": "nobody", "api_key": KEY},
        {"provider": "anthropic", "api_key": KEY, "model": "m" * 121},
        {"provider": "anthropic", "api_key": "has a space inside"},
        KEY,
        [KEY],
    ):
        for path in ("/api/llm/keys", "/api/llm/keys/test"):
            response = client.post(path, json=body, headers=GUEST_A)
            assert response.status_code == 422, (body, response.text)
            assert KEY not in response.text and "has a space inside" not in response.text
    assert recorder.calls == []


def test_guest_token_must_be_one_the_binding_accepts(client: TestClient) -> None:
    response = client.get("/api/llm/keys", headers={"X-Client-Token": "x" * 121})
    assert response.status_code == 422
    assert client.get("/api/llm/keys").status_code == 401


def _sign_up(client: TestClient, email: str, guest_token: str) -> dict[str, str]:
    response = client.post(
        "/api/account/signup", json={"email": email, "password": "correct-horse", "client_token": guest_token}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_adopt_on_sign_in(client: TestClient) -> None:
    key_id = _saved_id(client, {"X-Client-Token": "guest-adopt"})

    account = _sign_up(client, "keys@example.com", "guest-adopt")

    [view] = client.get("/api/llm/keys", headers=account).json()["keys"]
    assert view["id"] == key_id and view["active"] is True
    assert client.get("/api/llm/keys", headers={"X-Client-Token": "guest-adopt"}).json()["keys"] == []

    # Signing in again from another browser keeps the account's own key active.
    second = _saved_id(client, {"X-Client-Token": "guest-second"})
    signed_in = client.post(
        "/api/account/signin",
        json={"email": "keys@example.com", "password": "correct-horse", "client_token": "guest-second"},
    )
    assert signed_in.status_code == 200
    rows = {row["id"]: row["active"] for row in client.get("/api/llm/keys", headers=account).json()["keys"]}
    assert rows == {key_id: True, second: False}


def test_sign_in_with_server_token_adopts_no_server_key(client: TestClient, settings: Settings) -> None:
    admin = {"X-Admin-Token": settings.admin_token}
    client.post("/api/llm/keys?scope=server", json={"provider": "anthropic", "api_key": KEY, "model": ""}, headers=admin)

    account = _sign_up(client, "thief@example.com", SERVER_OWNER)

    assert client.get("/api/llm/keys", headers=account).json()["keys"] == []
    assert len(_store(client).list_credentials(SERVER_OWNER)) == 1


def _stream_turn(client: TestClient, headers: dict[str, str]) -> list[httpx.Response]:
    async def geocode(name: str, *, country_hint: str = "") -> GeoLocation | None:
        return GeoLocation(name=name, geo=GeoPoint(lat=35.0116, lng=135.7681), source="fake-geocoder")

    async def rates() -> None:
        return None

    container = client.app.state.container
    container.registry.geocoder = SimpleNamespace(name="fake-geocoder", geocode=geocode)
    container.registry.fx = SimpleNamespace(refresh=rates)
    created = client.post("/api/journeys", json={}, headers=headers)
    session_id = created.json()["session"]["id"]
    state = client.patch(f"/api/journeys/{session_id}/state", json=TRIP, headers=headers)
    turn = client.post(
        "/api/turn/stream",
        json={"message": "Plan my trip.", "locale": "en", "session_id": session_id},
        headers=headers,
    )
    return [created, state, turn]


def test_no_key_in_logs_traces_or_responses(
    client: TestClient,
    recorder: Recorder,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "sk-LEAKSENTINEL-0001"
    emitted: list[tuple[str, Any]] = []
    real_bind = orchestrator_module.bind_live_emit

    def spy(emit: Any) -> Any:
        def recording(event: str, data: Any) -> Any:
            emitted.append((event, data))
            return emit(event, data)

        return real_bind(recording)

    monkeypatch.setattr(orchestrator_module, "bind_live_emit", spy)
    caplog.set_level(logging.DEBUG)

    saved = _save(client, GUEST_A, provider="openai", key=sentinel)
    assert saved.status_code == 200, saved.text
    key_id = saved.json()["key"]["id"]
    responses = [saved, client.get("/api/llm/keys", headers=GUEST_A)]
    responses += _stream_turn(client, GUEST_A)
    chat_calls = [call for call in recorder.to("api.openai.com") if call["path"].endswith("/chat/completions")]
    assert len(chat_calls) > 1, recorder.calls  # the save's test call, then the turn's own calls
    assert all(call["key"] == sentinel for call in chat_calls)

    recorder.status["api.openai.com"] = 401  # the provider's error body repeats the key
    retest = client.post(f"/api/llm/keys/{key_id}/test", headers=GUEST_A)
    assert retest.status_code == 200, retest.text
    assert retest.json()["result"]["ok"] is False and retest.json()["key"]["last_test_ok"] is False
    responses += [retest, client.get("/api/llm/keys", headers=GUEST_A), client.delete(f"/api/llm/keys/{key_id}", headers=GUEST_A)]

    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    for response in responses:
        assert sentinel not in response.text
    assert sentinel not in caplog.text
    assert emitted
    assert sentinel not in json.dumps(emitted, default=str)
    trace_files = [path for path in Path(settings.trace_dir).rglob("*") if path.is_file()]
    assert trace_files
    # The traces, and every other file the app wrote (the database included: only the ciphertext holds the key).
    for path in Path(settings.data_dir).rglob("*"):
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes(), path
