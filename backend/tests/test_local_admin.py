"""Local admin mode: on the owner's own machine every admin route opens without the admin token.

`LOCAL_ADMIN=true` is honoured only with ENVIRONMENT=development/test, and only for a caller whose
direct peer address is loopback. A request that sends a token is checked as before, so a wrong token is
still refused. Production refuses to start with the flag set. Nothing else changes: guests still see
only their own trips, and the BYOK caller binding still keys every key to its own caller.
"""

from __future__ import annotations

from typing import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.credentials import SERVER_OWNER
from app.infrastructure.http import override_default_transport
from app.interface.api import create_app
from tests.test_caller_binding import Recorder, Transported

LOOPBACK = ("127.0.0.1", 50000)
LOOPBACK_V6 = ("::1", 50000)
LAN = ("192.168.1.20", 50000)
KEY = "sk-ant-api-test-0000001111"

# One GET per admin area: evaluation, the threat panel, and the server-wide key card.
ADMIN_ROUTES = ("/api/eval/suites", "/api/eval/trials", "/api/admin/threats", "/api/llm/keys?scope=server")


def _local(settings: Settings, **update) -> Settings:
    return settings.model_copy(update={"local_admin": True, **update})


@pytest.fixture()
def recorder() -> Recorder:
    return Recorder()


def _client(settings: Settings, peer: tuple[str, int], recorder: Recorder | None = None) -> TestClient:
    app = create_app(settings)
    if recorder is not None:
        app.add_middleware(Transported, transport=httpx.MockTransport(recorder))
    return TestClient(app, client=peer)


def test_the_flag_defaults_to_off(settings: Settings) -> None:
    assert Settings(_env_file=None).local_admin is False
    assert settings.local_admin_active is False


def test_without_the_flag_admin_routes_still_need_the_token(settings: Settings) -> None:
    with _client(settings, LOOPBACK) as client:
        for path in ADMIN_ROUTES:
            assert client.get(path).status_code == 401, path
            assert client.get(path, headers={"X-Admin-Token": settings.admin_token}).status_code == 200, path
        assert client.get("/api/ready").json()["local_admin"] is False


@pytest.mark.parametrize("peer", [LOOPBACK, LOOPBACK_V6, ("localhost", 50000)])
def test_with_the_flag_a_loopback_caller_needs_no_token(settings: Settings, peer: tuple[str, int]) -> None:
    with _client(_local(settings), peer) as client:
        for path in ADMIN_ROUTES:
            assert client.get(path).status_code == 200, path
        assert client.get("/api/admin/spans/req_000000000000").status_code == 404
        # The right token still works.
        assert client.get("/api/admin/threats", headers={"X-Admin-Token": settings.admin_token}).status_code == 200


@pytest.mark.parametrize("peer", [LAN, ("10.0.0.5", 50000), ("testclient", 50000)])
def test_with_the_flag_a_caller_that_is_not_loopback_is_refused(settings: Settings, peer: tuple[str, int]) -> None:
    with _client(_local(settings), peer) as client:
        for path in ADMIN_ROUTES:
            assert client.get(path).status_code == 401, path
        # A forwarded header does not make a remote caller local: only the direct peer counts.
        spoofed = {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1", "Forwarded": "for=127.0.0.1"}
        assert client.get("/api/admin/threats", headers=spoofed).status_code == 401
        assert client.get("/api/ready").json()["local_admin"] is False
        # The token still opens them from anywhere.
        assert client.get("/api/admin/threats", headers={"X-Admin-Token": settings.admin_token}).status_code == 200


def test_with_the_flag_a_wrong_token_is_still_refused(settings: Settings) -> None:
    with _client(_local(settings), LOOPBACK) as client:
        for path in ADMIN_ROUTES:
            assert client.get(path, headers={"X-Admin-Token": "not-the-admin-token"}).status_code == 401, path


def test_production_refuses_to_start_with_the_flag(settings: Settings) -> None:
    production = _local(
        settings,
        environment="production",
        auth_secret="a-long-random-production-secret-0123456789",
        admin_token="a-real-admin-token-value",
        byok_encryption_key="Hl0sKQ3pXx4nq0vYb2m5c9fJ8dT1aW6eRzUoLiNgPyA=",
    )
    assert production.local_admin_active is False
    with pytest.raises(RuntimeError, match="LOCAL_ADMIN") as excinfo:
        create_app(production)
    assert "development" in str(excinfo.value)


def test_production_refusal_names_the_flag_from_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LOCAL_ADMIN", "true")
    monkeypatch.setenv("AUTH_SECRET", "a-long-random-production-secret-0123456789")
    monkeypatch.setenv("ADMIN_TOKEN", "a-real-admin-token-value")
    monkeypatch.setenv("BYOK_ENCRYPTION_KEY", "Hl0sKQ3pXx4nq0vYb2m5c9fJ8dT1aW6eRzUoLiNgPyA=")
    loaded = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "env.db")
    assert loaded.local_admin is True
    with pytest.raises(RuntimeError, match="LOCAL_ADMIN"):
        loaded.check_secrets()


@pytest.mark.parametrize("environment", ["development", "test"])
def test_development_and_test_start_with_the_flag(settings: Settings, environment: str) -> None:
    local = _local(settings, environment=environment)
    assert local.local_admin_active is True
    create_app(local)


def test_ready_reports_the_mode_and_nothing_secret(settings: Settings) -> None:
    with _client(_local(settings), LOOPBACK) as client:
        body = client.get("/api/ready").json()
    assert body["local_admin"] is True
    text = repr(body)
    for secret in (settings.admin_token, settings.auth_secret):
        assert secret not in text


def test_a_guest_still_cannot_read_another_guests_trip(settings: Settings) -> None:
    with _client(_local(settings), LOOPBACK) as client:
        created = client.post("/api/journeys", json={"client_token": "guest-a"})
        assert created.status_code == 200, created.text
        trip = created.json()["session"]["id"]
        assert client.get(f"/api/journeys/{trip}", headers={"X-Client-Token": "guest-a"}).status_code == 200
        assert client.get(f"/api/journeys/{trip}", headers={"X-Client-Token": "guest-b"}).status_code == 404
        assert client.get(f"/api/journeys/{trip}").status_code in {401, 404}
        assert client.delete(f"/api/journeys/{trip}", headers={"X-Client-Token": "guest-b"}).status_code == 404
        assert client.get(f"/api/journeys/{trip}", headers={"X-Client-Token": "guest-a"}).status_code == 200


def test_server_key_routes_work_without_a_token_on_loopback(settings: Settings, recorder: Recorder) -> None:
    body = {"provider": "anthropic", "api_key": KEY, "model": ""}
    with override_default_transport(httpx.MockTransport(recorder)), _client(_local(settings), LOOPBACK, recorder) as client:
        saved = client.post("/api/llm/keys?scope=server", json=body)
        assert saved.status_code == 200, saved.text
        key_id = saved.json()["key"]["id"]
        store = client.app.state.container.store
        assert [row.owner for row in store.list_credentials(SERVER_OWNER)] == [SERVER_OWNER]

        listed = client.get("/api/llm/keys?scope=server").json()
        assert [row["id"] for row in listed["keys"]] == [key_id]
        assert listed["status"]["source"] == "server"

        # A guest still has only its own keys, and cannot reach the server's key by its id without the server scope.
        guest = {"X-Client-Token": "guest-a"}
        assert client.get("/api/llm/keys", headers=guest).json()["keys"] == []
        assert client.delete(f"/api/llm/keys/{key_id}", headers=guest).status_code == 404

        assert client.delete(f"/api/llm/keys/{key_id}?scope=server").status_code == 200
        assert store.list_credentials(SERVER_OWNER) == []


def test_server_key_routes_from_another_machine_still_need_the_token(settings: Settings) -> None:
    body = {"provider": "anthropic", "api_key": KEY, "model": ""}
    with _client(_local(settings), LAN) as client:
        assert client.post("/api/llm/keys?scope=server", json=body).status_code == 401
        assert client.get("/api/llm/keys?scope=server").status_code == 401
