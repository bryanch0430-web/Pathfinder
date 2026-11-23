"""The human-handoff desk and the honesty of edit turns (all offline, no live model)."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from app.core.store import Store
from app.core.trace import TurnTrace
from app.domain.models import ChatRequest, Money, SourceRef, now_iso
from app.evidence.models import Bundle, Evidence
from app.plan.editor import propose_edit
from app.turn import orchestrator
from app.understand.schemas import EditDraft, EditOp, ParsedIntent
from test_editor import _context, _plan

HEADERS = {"X-Client-Token": "desk-tester"}


def _client(tmp_path) -> TestClient:
    settings = Settings(
        database_path=str(tmp_path / "desk.db"),
        sandbox=True,
        local_admin=True,
        admin_token="test-admin",
        secret_key="test-secret",
    )
    return TestClient(create_app(settings))


def _chat(client: TestClient, messages: int = 1) -> str:
    """A traveller chat with a few messages in it."""
    store = client.app.state.container.store
    session_id = client.post("/api/journeys", json={}, headers=HEADERS).json()["session"]["id"]
    for index in range(messages):
        asyncio.run(
            store.add_message(
                session_id,
                {"id": f"m{index}", "role": "user", "content": f"hello {index}", "created_at": now_iso(), "meta": {}},
            )
        )
    return session_id


class _NoModel:
    """Stands in for the model client: no key, so a turn never leaves the machine."""

    available = False
    model = "none"

    def __init__(self, settings, trace=None) -> None:
        pass

    async def aclose(self) -> None:
        return None


def _transcript(client: TestClient, session_id: str) -> list[dict]:
    return asyncio.run(client.app.state.container.store.list_messages(session_id))


def _traveller_turn(client: TestClient, session_id: str, text: str) -> str:
    c = client.app.state.container
    events = asyncio.run(
        orchestrator.collect_turn(
            orchestrator.Dependencies(settings=c.settings, store=c.store),
            ChatRequest(session_id=session_id, message=text, locale="en", review=False),
            session_id=session_id,
            user_message=text,
        )
    )
    return "".join(event.data.get("text", "") for event in events if event.event == "token")


# -- 1. the brief ------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"party_size": "abc"},
        {"party_size": 0},
        {"party_size": 99},
        {"budget": "lots"},
        {"budget": -5},
        {"preferences": "food"},
        {"preferences": [1, 2]},
        {"currency": "NOPE"},
        {"start_date": "not-a-date"},
        {"start_date": "2027-04-10", "end_date": "2027-04-01"},
    ],
)
def test_a_bad_brief_is_refused_and_the_desk_keeps_working(tmp_path, bad):
    with _client(tmp_path) as client:
        session_id = _chat(client)
        response = client.post(f"/api/admin/desk/{session_id}/brief", json=bad)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]
        assert client.get(f"/api/journeys/{session_id}", headers=HEADERS).status_code == 200
        assert client.get("/api/admin/desk").status_code == 200


def test_a_good_brief_is_saved_and_does_not_claim_a_plan(tmp_path):
    with _client(tmp_path) as client:
        session_id = _chat(client)
        response = client.post(
            f"/api/admin/desk/{session_id}/brief",
            json={
                "destination": "Kyoto",
                "party_size": "3",
                "budget": 1000,
                "start_date": "2027-04-10",
                "end_date": "2027-04-12",
                "preferences": ["food", "temples"],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["built"] is False and body["plan"] is None and body["reason"]
        context = client.get(f"/api/journeys/{session_id}", headers=HEADERS).json()["context"]
        assert (context["party_size"], context["days"], context["preferences"]) == (3, 3, ["food", "temples"])


# -- 2. edit turns tell the truth -------------------------------------------


class _FakeLLM:
    def __init__(self, draft: EditDraft) -> None:
        self.draft = draft

    async def json(self, **kwargs):
        return self.draft


def _hotel_bundle() -> Bundle:
    bundle = Bundle()
    bundle.add(
        [
            Evidence(
                ref="ho-2",
                kind="hotel",
                name="Cheaper Hotel",
                source_refs=[SourceRef(title="Cheaper Hotel", url="https://e.test", source="Test")],
                price=Money(amount=5000, currency="JPY"),
            )
        ],
        "test",
    )
    return bundle


def _locked_plan():
    plan = _plan()
    bundle = _hotel_bundle()
    from app.plan.editor import _apply

    _apply(EditOp(op="swap_hotel", ref="ho-2"), plan, _context(), bundle, [])
    plan.hotels[0].locked = True
    plan.days[0].items[0].locked = True
    return plan, bundle


def _edit(plan, bundle, draft):
    return asyncio.run(
        propose_edit(
            llm=_FakeLLM(draft),
            providers=None,
            settings=Settings(database_path="data/test.db"),
            context=_context(),
            plan=plan,
            bundle=bundle,
            message="swap hotel for a cheaper one and remove it",
            scope_label=None,
            scope_item_id=None,
        )
    )


def test_a_locked_edit_does_not_claim_changes_it_did_not_make():
    plan, bundle = _locked_plan()
    temple = plan.days[0].items[0]
    other_hotel = Evidence(
        ref="ho-3",
        kind="hotel",
        name="Third Hotel",
        source_refs=[SourceRef(title="Third", url="https://e.test", source="Test")],
    )
    bundle.add([other_hotel], "test")
    draft = EditDraft(
        summary="Swapped the hotel for a cheaper one and removed Temple.",
        detail="Changed both even though they were locked.",
        ops=[
            EditOp(op="swap_hotel", ref="ho-3"),
            EditOp(op="remove", item_id=temple.id),
            EditOp(op="set_field", field="party_size", value=2),  # reports success, changes nothing
        ],
    )
    outcome = _edit(plan, bundle, draft)
    assert outcome.changed is False
    from app.plan.editor import actual_changes

    assert actual_changes(plan, outcome.plan, _context(), outcome.context) == []
    assert any(item.id == temple.id for item in outcome.plan.days[0].items)
    text = " ".join(outcome.log).lower()
    assert "locked" in text and "nothing in the plan changed" in text
    assert "removed temple" not in text and "party size set" not in text


def test_a_partly_applied_edit_states_only_what_was_applied():
    plan, bundle = _locked_plan()
    temple = plan.days[0].items[0]
    draft = EditDraft(
        summary="Removed Temple and cut the budget.",
        detail="Changed both even though they were locked.",
        ops=[EditOp(op="remove", item_id=temple.id), EditOp(op="set_field", field="budget", value=50000)],
    )
    outcome = _edit(plan, bundle, draft)
    assert outcome.changed is True
    assert "removed" not in outcome.draft.summary.lower()
    assert "budget" in outcome.draft.summary.lower()
    assert "locked" in outcome.draft.detail.lower()
    assert any(item.id == temple.id for item in outcome.plan.days[0].items)


def test_wording_must_match_the_diff():
    from app.plan.editor import actual_changes, wording_fits

    old = _plan()
    new = _plan()
    new.days[1].items.pop()
    entries = actual_changes(old, new, _context(), _context())
    assert wording_fits("Removed Museum.", entries)
    assert not wording_fits("Added a museum.", entries)
    assert not wording_fits("Swapped the hotel.", entries)
    assert not wording_fits("Removed Temple.", [])


# -- 3. operator actions stay out of the traveller's transcript --------------


def test_desk_run_and_edit_on_a_held_chat_do_not_touch_the_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "LLMClient", _NoModel)
    with _client(tmp_path) as client:
        session_id = _chat(client)
        assert client.post(f"/api/admin/desk/{session_id}/hold").status_code == 200
        before = _transcript(client, session_id)
        run = client.post(f"/api/admin/desk/{session_id}/run", json={"message": "Build a Kyoto trip"})
        edit = client.post(f"/api/admin/desk/{session_id}/edit", json={"instruction": "Drop the museum"})
        for response in (run, edit):
            assert response.status_code == 200
            assert "with you now" not in response.json()["reply"]
            assert "queue" not in response.json()["reply"].lower()
        assert "plan" in run.json() and "plan" in edit.json()
        assert _transcript(client, session_id) == before
        # The traveller is still met by the operator.
        assert "with you now" in _traveller_turn(client, session_id, "hello?")


# -- 4. operator note vs planner status --------------------------------------


def test_the_operator_note_survives_the_next_planner_status(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "LLMClient", _NoModel)
    with _client(tmp_path) as client:
        session_id = _chat(client)
        client.post(f"/api/admin/desk/{session_id}/hold")
        assert (
            client.post(f"/api/admin/desk/{session_id}/note", json={"note": "Checking visa rules"}).status_code == 200
        )
        reply = _traveller_turn(client, session_id, "any news?")
        assert "Checking visa rules" in reply and "Reading your message" not in reply
        held = client.get(f"/api/journeys/{session_id}/held", headers=HEADERS).json()
        assert held["held"] is True and held["planner_note"] == "Checking visa rules"
        assert client.get(f"/api/admin/desk/{session_id}").json()["note"] == "Checking visa rules"
        client.post(f"/api/admin/desk/{session_id}/release")
        asyncio.run(client.app.state.container.store.touch_session(session_id, planner_note="Finding places"))
        assert (
            client.get(f"/api/journeys/{session_id}/held", headers=HEADERS).json()["planner_note"] == "Finding places"
        )
        assert client.get(f"/api/admin/desk/{session_id}").json()["note"] == "Checking visa rules"


def test_old_databases_gain_the_note_columns(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, client_token TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, held INTEGER NOT NULL DEFAULT 0, "
        "agent_name TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '')"
    )
    db.execute("INSERT INTO sessions (id, created_at, updated_at) VALUES ('s1', 'x', 'x')")
    db.commit()
    db.close()
    store = Store(path)
    store.init()
    row = asyncio.run(store.get_session("s1"))
    assert row["planner_status"] == "" and row["operator_note"] == ""


# -- 5. the configured handoff message and the desk signals ------------------


def test_the_configured_handoff_message_is_used_when_set(tmp_path):
    async def run(policy):
        store = Store(tmp_path / f"handoff-{len(list(tmp_path.iterdir()))}.db")
        await store.create_session(session_id="s1")
        sent: list[str] = []

        async def push(event, data):
            if event == "token":
                sent.append(data["text"])

        await orchestrator._handoff(push, store, "s1", TurnTrace(request_id="t"), "asked", policy)
        return "".join(sent)

    assert asyncio.run(run({"message": "A designer is on the way."})) == "A designer is on the way."
    assert "travel-designer queue" in asyncio.run(run({"message": "  "}))
    assert "travel-designer queue" in asyncio.run(run(None))


def test_frustration_threshold_zero_is_a_real_setting():
    class Kv:
        async def get_kv(self, key):
            return 0

    policy = {"phrases_on": True, "enabled": True, "stuck_on": False, "frustration_at": 0.0, "human_phrases": []}
    parsed = ParsedIntent(intent="modify", confidence=1.0).model_copy(update={"frustration": 0.1})
    assert asyncio.run(orchestrator._handoff_reason(Kv(), "s", parsed, policy, "hi")) == "frustration"


def test_the_desk_list_shows_real_stuck_and_frustration(tmp_path):
    with _client(tmp_path) as client:
        session_id = _chat(client)
        store = client.app.state.container.store
        asyncio.run(store.set_kv(f"stuck:{session_id}", 3))
        asyncio.run(store.set_kv(f"frustration:{session_id}", 0.9))
        chat = next(row for row in client.get("/api/admin/desk").json()["chats"] if row["session_id"] == session_id)
        assert chat["stuck"] is True and chat["frustration"] == 0.9 and chat["band"] == "hot"


# -- 6. bad input is a 4xx, never a 500 --------------------------------------


def test_desk_endpoints_reject_bad_input_with_a_4xx(tmp_path):
    with _client(tmp_path) as client:
        session_id = _chat(client)
        desk = f"/api/admin/desk/{session_id}"
        assert client.post(f"{desk}/say").status_code == 422
        assert client.post(f"{desk}/say", json={"text": ""}).status_code == 422
        assert client.post(f"{desk}/undo", json={"version": "abc"}).status_code == 422
        assert client.post(f"{desk}/plan", json={"items": "x"}).status_code == 422
        assert client.post(f"{desk}/layout", json={"items": [1]}).status_code == 422
        assert client.post(f"{desk}/run", json={}).status_code == 422
        bad_json = client.put("/api/admin/escalation", content="{bad", headers={"Content-Type": "application/json"})
        assert bad_json.status_code == 422


def test_escalation_settings_are_parsed_strictly(tmp_path):
    with _client(tmp_path) as client:
        saved = client.put(
            "/api/admin/escalation",
            json={
                "enabled": "false",
                "phrases_on": True,
                "frustration_at": 0,
                "human_phrases": [None, "live agent", 5, "  "],
                "message": "A person will be with you.",
            },
        )
        assert saved.status_code == 200
        policy = client.get("/api/admin/escalation").json()
        assert policy["enabled"] is False
        assert policy["frustration_at"] == 0
        assert policy["human_phrases"] == ["live agent"]
        assert policy["message"] == "A person will be with you."
        assert client.put("/api/admin/escalation", json={"enabled": "yes"}).status_code == 422
        assert client.put("/api/admin/escalation", json={"human_phrases": 7}).status_code == 422
        assert client.put("/api/admin/escalation", json={"frustration_at": "high"}).status_code == 422


def test_desk_actions_on_a_missing_chat_are_404_and_write_nothing(tmp_path):
    with _client(tmp_path) as client:
        store = client.app.state.container.store
        for action, body in (
            ("hold", None),
            ("release", None),
            ("note", {"note": "x"}),
            ("say", {"text": "hello"}),
            ("run", {"message": "hi"}),
            ("brief", {"destination": "Kyoto"}),
        ):
            response = client.post(f"/api/admin/desk/nope/{action}", json=body)
            assert response.status_code == 404, (action, response.text)
        assert asyncio.run(store.get_session("nope")) is None
        assert asyncio.run(store.list_messages("nope")) == []
