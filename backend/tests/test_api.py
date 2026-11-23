from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from app.core.currencies import normalise_currency
from app.domain.models import RoutePayload, TripContext
from app.understand.pipeline import missing_for_plan


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
                "purpose_temperatures": {"plan": 0.5},
                "purpose_reasoning": {"verify": "high"},
                "purpose_max_tokens": {"plan": 9000},
                "orchestrator": {"mode": "jev", "jev": {"model": "jev-test", "url": "https://jev.test/systemone"}},
            },
        )
        config = client.get("/api/runtime").json()
        assert config["models"]["purposes"]["plan"] == "planner-model"
        assert config["models"]["temperatures"]["plan"] == 0.5
        assert config["models"]["reasoning_by_purpose"]["verify"] == "high"
        assert config["models"]["max_tokens_by_purpose"]["plan"] == 9000
        assert config["models"]["effective_max_tokens"]["plan"] == 9000
        assert config["models"]["effective_max_tokens"]["understand"] == 4096
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
    assert client.temperature_for("ask") == settings.llm_temperature
    assert client.reasoning_for("verify") == "none"
    tuned = settings.model_copy(
        update={"purpose_temperatures": {"plan": 0.9}, "purpose_reasoning": {"plan": "high"}}
    )
    tuned_client = LLMClient(tuned)
    assert tuned_client.temperature_for("plan") == 0.9
    assert tuned_client.reasoning_for("plan") == "high"
    assert client.max_tokens_for("plan", 12000) == 12000
    assert client.max_tokens_for("plan") == 12000
    assert client.max_tokens_for("understand") == settings.llm_max_tokens
    capped = settings.model_copy(update={"purpose_max_tokens": {"plan": 5000}})
    assert LLMClient(capped).max_tokens_for("plan", 12000) == 5000


def test_human_phrase_hands_off_even_when_automatic_is_off():
    import asyncio
    from app.understand.schemas import ParsedIntent
    from app.turn.orchestrator import _handoff_reason

    class Store:
        async def get_kv(self, key):
            return 0

    parsed = ParsedIntent(intent="ask", confidence=1.0)
    policy = {"enabled": False, "stuck_on": True, "human_phrases": ["live agent"]}
    assert asyncio.run(_handoff_reason(Store(), "s", parsed, policy, "can I talk to a live agent?")) == "asked"
    assert asyncio.run(_handoff_reason(Store(), "s", parsed, policy, "just saying hi")) == ""
    assert asyncio.run(_handoff_reason(Store(), "s", parsed, policy, "I do not want a live agent")) == ""
    off = {**policy, "phrases_on": False}
    assert asyncio.run(_handoff_reason(Store(), "s", parsed, off, "can I talk to a live agent?")) == "disabled"
    asked = parsed.model_copy(update={"wants_human": True})
    assert asyncio.run(_handoff_reason(Store(), "s", asked, off, "hello")) == "disabled"


def test_stuck_and_automatic_are_independent():
    import asyncio
    from app.understand.schemas import ParsedIntent
    from app.turn.orchestrator import _handoff_reason

    class Store:
        async def get_kv(self, key):
            return 3

    parsed = ParsedIntent(intent="modify", confidence=1.0)
    stuck_only = {"phrases_on": True, "enabled": False, "stuck_on": True, "stuck_after": 3, "human_phrases": []}
    assert asyncio.run(_handoff_reason(Store(), "s", parsed, stuck_only, "change it")) == "stuck"

    no_stuck = {"phrases_on": True, "enabled": True, "stuck_on": False, "stuck_after": 3, "frustration_at": 0.5, "human_phrases": []}
    angry = parsed.model_copy(update={"frustration": 0.9})
    assert asyncio.run(_handoff_reason(Store(), "s", angry, no_stuck, "change it")) == "frustration"


def test_clear_memory_removes_preferences_and_saved_trips(tmp_path):
    with _client(tmp_path) as client:
        headers = {"X-Client-Token": "tester"}
        saved = client.patch(
            "/api/traveller",
            json={"client_token": "tester", "preferences": ["vegetarian", "slow pace"]},
            headers=headers,
        )
        assert saved.status_code == 200
        assert saved.json()["profile"]["preferences"] == ["vegetarian", "slow pace"]

        # A finalised trip writes this row; insert it directly so the test does not
        # need to drive a whole planning turn just to exercise the clear button.
        db = sqlite3.connect(tmp_path / "api.db")
        db.execute(
            "INSERT INTO wishlist (id, session_id, identity, destination, summary, stars, body, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("saved-1", "session-1", "guest:tester", "Kyoto", "A saved trip", 5, json.dumps({}), "2026-11-23T00:00:00+00:00"),
        )
        db.commit()
        db.close()

        cleared = client.post("/api/recall/clear", json={"client_token": "tester"}, headers=headers)
        assert cleared.status_code == 200
        assert cleared.json() == {"cleared": True, "saved_trips": 1, "preferences": 2}

        profile = client.get("/api/traveller", headers=headers).json()
        assert profile["preferences"] == []
        assert profile["saved_trips"] == 0

        # Idempotent: a second press reports nothing left, not an error.
        again = client.post("/api/recall/clear", json={"client_token": "tester"}, headers=headers)
        assert again.status_code == 200
        assert again.json() == {"cleared": True, "saved_trips": 0, "preferences": 0}


def test_a_new_plan_asks_for_party_size_but_not_budget():
    context = TripContext(
        session_id="party-check", destination="Japan", start_date="2026-11-30", days=10, currency="HKD"
    )
    assert missing_for_plan(context) == ["party_size"]

    context.party_size = 2
    assert missing_for_plan(context) == []


def test_currency_must_be_a_real_iso_code(tmp_path):
    assert normalise_currency("twd") == "TWD"
    assert normalise_currency("NTD") == "TWD"
    assert normalise_currency("not a currency") == ""

    with _client(tmp_path) as client:
        headers = {"X-Client-Token": "currency-tester"}
        session_id = client.post("/api/journeys", json={}, headers=headers).json()["session"]["id"]
        bad = client.patch(
            f"/api/journeys/{session_id}/state", json={"currency": "NOPE"}, headers=headers
        )
        assert bad.status_code == 400
        good = client.patch(
            f"/api/journeys/{session_id}/state", json={"currency": "twd"}, headers=headers
        )
        assert good.status_code == 200
        assert good.json()["context"]["currency"] == "TWD"


def test_currency_ambiguity_clarify_always_has_a_currency_field():
    from app.understand.pipeline import _clarify
    from app.understand.schemas import ParsedIntent

    parsed = ParsedIntent(
        intent="modify",
        question="I think you mean HKD (Hong Kong dollars). Should I convert your 12,000 TWD budget to HKD, or keep 12,000 and treat it as HKD?",
        missing_slots=["budget"],
    )
    route = RoutePayload(intent="modify", need_clarification=True, missing_slots=["budget"])
    clarify = _clarify(parsed, TripContext(session_id="currency-clarify", currency="TWD"), None, route)
    assert "currency" in clarify.slots
    assert "HKD" in clarify.options["currency"]
    assert "TWD" in clarify.options["currency"]
