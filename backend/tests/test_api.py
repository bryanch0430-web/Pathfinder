from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings


def _client(tmp_path, *, local_admin: bool = True) -> TestClient:
    settings = Settings(
        database_path=str(tmp_path / "api.db"),
        sandbox=True,
        local_admin=local_admin,
        admin_token="test-admin",
        secret_key="test-secret",
    )
    return TestClient(create_app(settings))


def test_ready_reports_the_version(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/api/ready")
        assert response.status_code == 200
        assert response.json()["version"] == "5.0.0"


def test_journey_lifecycle(tmp_path):
    with _client(tmp_path) as client:
        headers = {"X-Client-Token": "tester"}
        created = client.post("/api/journeys", json={}, headers=headers)
        assert created.status_code == 200
        session_id = created.json()["session"]["id"]
        assert created.json()["context"]["currency"] == "HKD"

        state = client.get(f"/api/journeys/{session_id}", headers=headers)
        assert state.status_code == 200
        assert state.json()["plan"] is None
        assert state.json()["messages"] == []

        patched = client.patch(
            f"/api/journeys/{session_id}/state",
            json={"destination": "Kyoto", "days": 3, "party_size": 2, "budget": 250000, "currency": "JPY"},
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["context"]["destination"] == "Kyoto"

        revisions = client.get(f"/api/journeys/{session_id}/revisions", headers=headers)
        assert revisions.status_code == 200
        assert revisions.json()["versions"] == []


def test_another_traveller_cannot_open_the_trip(tmp_path):
    with _client(tmp_path, local_admin=False) as client:
        session_id = client.post("/api/journeys", json={}, headers={"X-Client-Token": "owner"}).json()["session"]["id"]
        assert client.get(f"/api/journeys/{session_id}", headers={"X-Client-Token": "other"}).status_code == 403
        assert client.get("/api/journeys/missing", headers={"X-Client-Token": "owner"}).status_code == 404


def test_admin_routes_need_the_token(tmp_path):
    with _client(tmp_path, local_admin=False) as client:
        assert client.get("/api/admin/prompts").status_code == 403
        assert client.get("/api/admin/prompts", headers={"X-Admin-Token": "test-admin"}).status_code == 200
        assert client.get("/api/admin/prompts", headers={"X-Admin-Token": "wrong"}).status_code == 403


def test_runtime_shape_for_the_settings_page(tmp_path):
    with _client(tmp_path) as client:
        config = client.get("/api/runtime").json()
        for key in ("thresholds", "writer", "redaction", "providers", "maps", "capabilities", "data_mode"):
            assert key in config
        assert "temperature" in config["writer"]
        assert set(config["thresholds"]) == {"router", "max_repairs", "turn_deadline"}
        assert config["capabilities"]["jev"] is True
        assert config["capabilities"]["purpose_models"] is True
        assert "reviewer" not in config


def test_writer_call_settings_persist(tmp_path):
    with _client(tmp_path) as client:
        client.put("/api/admin/runtime", json={"writer_call": {"temperature": 0.4, "max_tokens": 1234}})
        config = client.get("/api/runtime").json()
        assert config["writer"]["temperature"] == 0.4
        assert config["writer"]["max_tokens"] == 1234


def test_models_and_orchestrator_roundtrip(tmp_path):
    with _client(tmp_path) as client:
        client.put(
            "/api/admin/runtime",
            json={
                "purpose_models": {"plan": "planner-model", "verify": "verifier-model"},
                "orchestrator": {"mode": "jev", "jev": {"model": "jev-test", "url": "https://jev.test/systemone"}},
            },
        )
        config = client.get("/api/runtime").json()
        assert config["models"]["purposes"]["plan"] == "planner-model"
        assert config["orchestrator"]["mode"] == "jev"
        assert config["orchestrator"]["jev"]["model"] == "jev-test"
        assert config["orchestrator"]["jev"]["url"] == "https://jev.test/systemone"


def test_per_purpose_model_wins_over_the_writer():
    from app.config import get_settings
    from app.core.llm import LLMClient

    settings = get_settings().model_copy(
        update={"llm_model": "default-model", "openai_model": "default-model", "purpose_models": {"plan": "planner-model"}}
    )
    client = LLMClient(settings)
    assert client.model_for("plan") == "planner-model"
    assert client.model_for("ask") == "default-model"
    assert client.model_for("plan", "explicit") == "explicit"
