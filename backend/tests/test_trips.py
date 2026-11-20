import json
from unittest.mock import AsyncMock, patch
import uuid
import time
import pytest
import jwt
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from conftest import TestingSessionLocal
from app.models.trip import ConversationMessage


def create_mock_jwt(user_id: str, email: str = "test@pathfinder.ai") -> str:
    """Generate a mock JWT token for testing."""
    if not settings.SUPABASE_URL:
        settings.SUPABASE_URL = "https://mock.supabase.co"
    if not settings.SUPABASE_JWT_SECRET:
        settings.SUPABASE_JWT_SECRET = "test_secret_for_tests"
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "iss": f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1",
        "exp": int(time.time()) + 3600,
        "email": email,
        "role": "authenticated",
        "user_metadata": {"full_name": "Test User"},
    }
    secret = settings.SUPABASE_JWT_SECRET or "test_secret_for_tests"
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.asyncio
async def test_create_trip_anonymous(client: AsyncClient):
    """Verify POST /api/trips creates trip when neither header is provided."""
    response = await client.post("/api/trips")
    assert response.status_code == 201
    data = response.json()

    assert "trip_id" in data
    assert "session_id" in data
    assert "assistant_message" in data
    assert data["assistant_message"]["role"] == "ASSISTANT"
    assert len(data["assistant_message"]["content"]) > 0


@pytest.mark.asyncio
async def test_guest_identity_flow(client: AsyncClient):
    """Verify Guest A creates trip, accesses it, and Guest B is rejected with 403."""
    guest_a = str(uuid.uuid4())
    guest_b = str(uuid.uuid4())

    # 1. Guest A creates trip
    create_res = await client.post(
        "/api/trips",
        headers={"X-Guest-ID": guest_a},
    )
    assert create_res.status_code == 201
    trip_id = create_res.json()["trip_id"]

    # 2. Guest A accesses trip
    get_res_a = await client.get(
        f"/api/trips/{trip_id}",
        headers={"X-Guest-ID": guest_a},
    )
    assert get_res_a.status_code == 200
    assert get_res_a.json()["trip"]["guest_id"] == guest_a
    assert get_res_a.json()["trip"]["user_id"] is None

    # 3. Guest A sends message
    msg_res_a = await client.post(
        f"/api/trips/{trip_id}/messages",
        headers={"X-Guest-ID": guest_a},
        json={"message_type": "TEXT", "content": "Tokyo"},
    )
    assert msg_res_a.status_code == 200

    # 4. Guest B tries to access Guest A's trip -> 403 Forbidden
    get_res_b = await client.get(
        f"/api/trips/{trip_id}",
        headers={"X-Guest-ID": guest_b},
    )
    assert get_res_b.status_code == 403

    # 5. Guest B tries to send message to Guest A's trip -> 403 Forbidden
    msg_res_b = await client.post(
        f"/api/trips/{trip_id}/messages",
        headers={"X-Guest-ID": guest_b},
        json={"message_type": "TEXT", "content": "Kyoto"},
    )
    assert msg_res_b.status_code == 403


@pytest.mark.asyncio
async def test_route_metrics_is_guest_scoped_and_keyless_safe(client: AsyncClient):
    guest = str(uuid.uuid4())
    created = await client.post("/api/trips", headers={"X-Guest-ID": guest})
    trip_id = created.json()["trip_id"]
    payload = {"legs": [{"id": "leg-1", "from_lat": 35.68, "from_lon": 139.69,
                         "to_lat": 35.01, "to_lon": 135.76}]}
    with patch.object(settings, "ORS_API_KEY", ""):
        response = await client.post(f"/api/trips/{trip_id}/route-metrics",
                                     headers={"X-Guest-ID": guest}, json=payload)
    assert response.status_code == 200
    assert response.json() == {"provider": "unavailable", "legs": []}
    forbidden = await client.post(f"/api/trips/{trip_id}/route-metrics",
                                  headers={"X-Guest-ID": str(uuid.uuid4())}, json=payload)
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_existing_itinerary_graph_backfills_once_and_is_guest_scoped(client: AsyncClient):
    guest = str(uuid.uuid4())
    headers = {"X-Guest-ID": guest}
    created = await client.post("/api/trips", headers=headers)
    trip_id = created.json()["trip_id"]
    planned = await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                json={"message_type": "UI_ACTION", "payload": {
                                    "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi",
                                    "destination": "Japan", "duration_days": 5,
                                    "places_to_visit": ["Tokyo", "Kyoto"],
                                }})
    assert planned.status_code == 200
    assert planned.json()["assistant_message"]["payload"]["action"] == "SHOW_PLANNING_CHOICE"
    planned = await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                json={"message_type": "UI_ACTION", "payload": {
                                    "action": "GENERATE_FULL_ITINERARY",
                                }})
    assert planned.status_code == 200
    graph = planned.json()["assistant_message"]["payload"]["graph"]

    async with TestingSessionLocal() as db:
        result = await db.execute(select(ConversationMessage).where(
            ConversationMessage.id == uuid.UUID(planned.json()["assistant_message"]["id"])))
        message = result.scalar_one()
        message.payload = None
        await db.commit()

    with patch("app.agents.trip_planner.nodes.extract_itinerary_node.extract_itinerary",
               new_callable=AsyncMock, return_value={"itinerary_graph": graph}) as extract:
        first = await client.post(f"/api/trips/{trip_id}/itinerary-graph", headers=headers)
        second = await client.post(f"/api/trips/{trip_id}/itinerary-graph", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == graph
    extract.assert_awaited_once()
    forbidden = await client.post(f"/api/trips/{trip_id}/itinerary-graph",
                                  headers={"X-Guest-ID": str(uuid.uuid4())})
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_authenticated_user_identity_flow(client: AsyncClient):
    """Verify User A creates trip, accesses it, and User B / Guest are rejected with 403."""
    user_a_id = str(uuid.uuid4())
    user_b_id = str(uuid.uuid4())
    token_a = create_mock_jwt(user_a_id, "user_a@pathfinder.ai")
    token_b = create_mock_jwt(user_b_id, "user_b@pathfinder.ai")
    guest_uuid = str(uuid.uuid4())

    # 1. User A creates trip
    create_res = await client.post(
        "/api/trips",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert create_res.status_code == 201
    trip_id = create_res.json()["trip_id"]

    # 2. User A accesses trip
    get_res_a = await client.get(
        f"/api/trips/{trip_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert get_res_a.status_code == 200
    assert get_res_a.json()["trip"]["user_id"] == user_a_id
    assert get_res_a.json()["trip"]["guest_id"] is None

    # 3. User B tries to access User A's trip -> 403 Forbidden
    get_res_b = await client.get(
        f"/api/trips/{trip_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert get_res_b.status_code == 403

    # 4. Guest tries to access User A's trip -> 403 Forbidden
    get_res_guest = await client.get(
        f"/api/trips/{trip_id}",
        headers={"X-Guest-ID": guest_uuid},
    )
    assert get_res_guest.status_code == 403


@pytest.mark.asyncio
async def test_dual_identity_conflict_rejected(client: AsyncClient):
    """Verify request with BOTH Authorization and X-Guest-ID is rejected with 400."""
    user_id = str(uuid.uuid4())
    token = create_mock_jwt(user_id)
    guest_id = str(uuid.uuid4())

    res = await client.post(
        "/api/trips",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Guest-ID": guest_id,
        },
    )
    assert res.status_code == 400
    assert "both" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_invalid_guest_id_format_rejected(client: AsyncClient):
    """Verify malformed non-UUID X-Guest-ID header is rejected with 400."""
    res = await client.post(
        "/api/trips",
        headers={"X-Guest-ID": "invalid-guest-not-a-uuid"},
    )
    assert res.status_code == 400
    assert "invalid x-guest-id" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_form_onboarding_persists_places_and_plan(client: AsyncClient):
    guest_id = str(uuid.uuid4())
    headers = {"X-Guest-ID": guest_id}
    created = await client.post("/api/trips", headers=headers)
    assert created.status_code == 201
    assert created.json()["assistant_message"]["payload"] is None
    trip_id = created.json()["trip_id"]

    response = await client.post(
        f"/api/trips/{trip_id}/messages", headers=headers,
        json={"message_type": "UI_ACTION", "payload": {
            "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi",
            "destination": "Japan", "places_to_visit": ["Tokyo", "Kyoto"],
            "duration_days": 8,
            "planning_preferences": {
                "pace": "relaxed", "interests": ["Food", "Architecture"],
                "avoid": ["Early starts"],
            },
        }},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["trip"]["onboarding_status"] == "COMPLETE"
    assert data["trip"]["origin_text"] == "Delhi"
    assert data["trip"]["destination"] == "Japan"
    assert data["trip"]["places_to_visit"] == ["Tokyo", "Kyoto"]
    assert data["trip"]["duration_days"] == 8
    assert data["trip"]["planning_preferences"] == {
        "pace": "relaxed", "interests": ["Food", "Architecture"], "avoid": ["Early starts"],
        # brief v2 defaults when the form leaves them out
        "start_date": None, "adults": 1, "children": 0, "comfort": "mid_range",
        "travel_mode": "transit", "guide": None, "home_currency": None,
    }
    assert data["conversation"]["current_stage"] == "REVIEW"
    assert data["trip"]["status"] == "DRAFT"
    assert data["assistant_message"]["content"]
    assert data["assistant_message"]["payload"]["action"] == "SHOW_PLANNING_CHOICE"

    generated = await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                  json={"message_type": "UI_ACTION", "payload": {
                                      "action": "GENERATE_FULL_ITINERARY",
                                  }})
    assert generated.status_code == 200
    assert generated.json()["conversation"]["current_stage"] == "COMPLETE"
    assert generated.json()["assistant_message"]["payload"]["kind"] == "ITINERARY_GRAPH"

    late_edit = await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                  json={"message_type": "UI_ACTION", "payload": {
                                      "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi",
                                      "destination": "Japan", "duration_days": 8,
                                  }})
    assert late_edit.status_code == 409

    restored = await client.get(f"/api/trips/{trip_id}", headers=headers)
    assert restored.json()["trip"]["places_to_visit"] == ["Tokyo", "Kyoto"]
    assert restored.json()["trip"]["planning_preferences"]["avoid"] == ["Early starts"]
    messages = await client.get(f"/api/trips/{trip_id}/messages", headers=headers)
    assert len(messages.json()["messages"]) == 5


@pytest.mark.asyncio
async def test_text_before_form_submission_keeps_form(client: AsyncClient):
    created = await client.post("/api/trips")
    trip_id = created.json()["trip_id"]
    response = await client.post(
        f"/api/trips/{trip_id}/messages",
        json={"message_type": "TEXT", "content": "Japan for 10 days"},
    )
    assert response.status_code == 200
    assert response.json()["trip"]["destination"] == "Japan"
    assert response.json()["assistant_message"]["payload"]["action"] == "SHOW_ONBOARDING_FORM"


@pytest.mark.asyncio
async def test_invalid_form_is_rejected(client: AsyncClient):
    created = await client.post("/api/trips")
    trip_id = created.json()["trip_id"]
    response = await client.post(
        f"/api/trips/{trip_id}/messages",
        json={"message_type": "UI_ACTION", "payload": {
            "action": "SUBMIT_TRIP_ONBOARDING", "origin": " ",
            "destination": "Japan", "duration_days": 0,
            "places_to_visit": ["Tokyo"],
        }},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invalid_planning_preference_is_rejected(client: AsyncClient):
    created = await client.post("/api/trips")
    response = await client.post(
        f"/api/trips/{created.json()['trip_id']}/messages",
        json={"message_type": "UI_ACTION", "payload": {
            "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi",
            "destination": "Japan", "duration_days": 8,
            "planning_preferences": {"pace": "extreme", "interests": [], "avoid": []},
        }},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_planning_preferences_can_be_revised_before_generation(client: AsyncClient):
    created = await client.post("/api/trips")
    trip_id = created.json()["trip_id"]
    base = {"action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi",
            "destination": "Japan", "duration_days": 8, "places_to_visit": ["Kyoto"]}
    first = await client.post(f"/api/trips/{trip_id}/messages",
                              json={"message_type": "UI_ACTION", "payload": {
                                  **base, "planning_preferences": {"pace": "packed"}}})
    assert first.status_code == 200
    revised = await client.post(f"/api/trips/{trip_id}/messages",
                                json={"message_type": "UI_ACTION", "payload": {
                                    **base, "planning_preferences": {
                                        "pace": "relaxed", "interests": ["Food"],
                                        "avoid": ["Early starts"],
                                    }}})
    assert revised.status_code == 200
    assert revised.json()["trip"]["planning_preferences"]["pace"] == "relaxed"
    assert revised.json()["assistant_message"]["payload"]["action"] == "SHOW_PLANNING_CHOICE"


@pytest.mark.asyncio
async def test_stream_form_submission(client: AsyncClient):
    created = await client.post("/api/trips")
    trip_id = created.json()["trip_id"]
    with patch("app.core.database.async_session_maker", TestingSessionLocal):
        response = await client.post(
            f"/api/trips/{trip_id}/messages/stream",
            json={"message_type": "UI_ACTION", "payload": {
                "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi",
                "destination": "Japan", "duration_days": 5,
                "places_to_visit": ["Kyoto"],
            }},
        )
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    done = next(event for event in events if event["type"] == "done")
    assert done["trip"]["onboarding_status"] == "COMPLETE"
    assert done["trip"]["places_to_visit"] == ["Kyoto"]
    assert done["assistant_message"]["payload"]["action"] == "SHOW_PLANNING_CHOICE"
    assert any(event["type"] == "token" for event in events)

    with patch("app.core.database.async_session_maker", TestingSessionLocal):
        generated = await client.post(
            f"/api/trips/{trip_id}/messages/stream",
            json={"message_type": "UI_ACTION", "payload": {"action": "GENERATE_FULL_ITINERARY"}},
        )
    generated_events = [json.loads(line[6:]) for line in generated.text.splitlines() if line.startswith("data: ")]
    generated_done = next(event for event in generated_events if event["type"] == "done")
    assert generated_done["assistant_message"]["payload"]["graph"]["nodes"]
    assert "route_reality" in generated_done["assistant_message"]["payload"]
    assert any(event.get("label") == "Checking travel time" for event in generated_events)


@pytest.mark.asyncio
async def test_get_trip_state_and_messages(client: AsyncClient):
    created = await client.post("/api/trips")
    trip_id = created.json()["trip_id"]
    state = await client.get(f"/api/trips/{trip_id}")
    assert state.status_code == 200
    assert state.json()["trip"]["onboarding_status"] == "IN_PROGRESS"
    messages = await client.get(f"/api/trips/{trip_id}/messages")
    assert messages.status_code == 200
    assert messages.json()["messages"][0]["payload"] is None


@pytest.mark.asyncio
async def test_missing_trip_returns_404(client: AsyncClient):
    """Verify 404 for non-existent trip ID."""
    random_uuid = str(uuid.uuid4())
    res = await client.get(f"/api/trips/{random_uuid}")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_get_my_trips_unauthorized(client: AsyncClient):
    """Verify GET /api/trips/me returns 401 when unauthenticated."""
    res = await client.get("/api/trips/me")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_complete_first_message_still_shows_the_form_until_submitted(client: AsyncClient):
    """A first message with destination, days and origin must not skip the form (the client would hide it)."""
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = (await client.post("/api/trips", headers=headers)).json()["trip_id"]
    response = await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                 json={"message_type": "TEXT", "content": "Plan 3 days in Kyoto from Delhi"})
    body = response.json()
    assert body["assistant_message"]["payload"]["action"] == "SHOW_ONBOARDING_FORM"
    assert body["trip"]["onboarding_status"] == "IN_PROGRESS"


async def _plan_one_shot(client: AsyncClient, headers: dict) -> str:
    trip_id = (await client.post("/api/trips", headers=headers)).json()["trip_id"]
    url = f"/api/trips/{trip_id}/messages"
    await client.post(url, headers=headers, json={"message_type": "UI_ACTION", "payload": {
        "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi", "destination": "Kyoto", "duration_days": 3}})
    await client.post(url, headers=headers, json={"message_type": "UI_ACTION", "payload": {
        "action": "GENERATE_FULL_ITINERARY"}})
    return trip_id


async def _stream(trip_id: str, headers: dict, text: str) -> list[dict]:
    import json
    from app.core.auth import RequestIdentity
    from app.schemas.trip import SendMessageRequest
    from app.services.conversation import conversation_service
    from conftest import TestingSessionLocal

    async with TestingSessionLocal() as db:
        return [json.loads(chunk[6:]) async for chunk in conversation_service.process_message_stream(
            db, uuid.UUID(trip_id), SendMessageRequest(message_type="TEXT", content=text),
            RequestIdentity(guest_id=headers["X-Guest-ID"]))]


@pytest.mark.asyncio
@pytest.mark.parametrize("intent, message, instruction, redraws_map", [
    ("update_trip", "Swap day 2's afternoon for Okochi-Sanso", "REVISION_SYSTEM_INSTRUCTION", True),
    # An edit verb wins over the intent label (the old routing answered this and changed nothing).
    ("trip_question", "Can you move Okochi-Sanso to day 1?", "REVISION_SYSTEM_INSTRUCTION", True),
    ("trip_question", "Is Okochi-Sanso worth it in the rain?", "ANSWER_SYSTEM_INSTRUCTION", False),
])
async def test_one_shot_follow_ups_revise_or_answer_the_existing_plan(client: AsyncClient, intent, message, instruction, redraws_map):
    from unittest.mock import patch
    from app.agents.trip_planner.nodes import planning_trip_node
    from app.services.llm.service import llm_service

    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = await _plan_one_shot(client, headers)
    seen = []

    async def fake_stream(prompt, system_instruction=None, **_):
        seen.append((prompt, system_instruction))
        yield "| Day 1 | Kyoto | **Fushimi Inari** |\n| Day 2 | Kyoto | **Okochi-Sanso** |"

    async def fake_understand(state):
        return {"intent": intent}

    with patch.object(llm_service, "generate_stream", new=fake_stream), \
            patch("app.agents.trip_planner.nodes.understand_user_msg_node.understand_user_message", new=fake_understand), \
            patch("app.agents.trip_planner.nodes.planning_trip_node.execute_planning_subgraph") as research:
        events = await _stream(trip_id, headers, message)
    prompt, system = seen[0]
    assert system == getattr(planning_trip_node, instruction)
    assert "CURRENT ITINERARY:" in prompt and message in prompt
    research.assert_not_called()  # no fresh research for a follow-up
    assert any(e["type"] == "graph" for e in events) is redraws_map


def test_follow_up_routing_edits_on_edit_verbs_and_agreement_and_answers_the_rest():
    from app.services.conversation import _follow_up_mode

    assert _follow_up_mode("Is Fushimi Inari very crowded in the early morning?", "update_trip", False) == "answer"
    assert _follow_up_mode("thanks!", "casual_conversation", False) == "answer"
    assert _follow_up_mode("Can you add Arashiyama bamboo grove on day 2 morning?", "trip_question", False) == "revise"
    assert _follow_up_mode("Swap day 2's afternoon for Okochi-Sanso", None, False) == "revise"
    # "yes" is an edit only when the last reply offered one; otherwise there's nothing to apply.
    assert _follow_up_mode("yes please", "casual_conversation", True) == "revise"
    assert _follow_up_mode("Go ahead and finalize it", "planning_request", True) == "revise"
    assert _follow_up_mode("ok finalize it, update the studio", "planning_request", False) == "answer"
    assert _follow_up_mode("What's the plan for day 2?", "trip_question", False) == "answer"
    # Asking for advice gets an answer with one concrete offer; a request phrased as a question is a request.
    assert _follow_up_mode("Day 1 feels rushed. Is there anything you'd shift to another day?", "trip_question", False) == "answer"
    assert _follow_up_mode("Should I add Nara as a day trip?", "trip_question", False) == "answer"
    assert _follow_up_mode("Could you add Nara on day 2?", "trip_question", False) == "revise"
    assert _follow_up_mode("What if we move Nishiki Market to day 1?", "trip_question", False) == "revise"
    assert _follow_up_mode("How about we swap days 2 and 3?", "trip_question", False) == "revise"
    assert _follow_up_mode("Is it possible to move Nishiki Market to day 1?", "trip_question", False) == "revise"


@pytest.mark.asyncio
async def test_yes_after_an_offer_applies_the_offer_instead_of_offering_again(client: AsyncClient):
    """The loop seen live: "Want me to swap it in?" → "yes" → another offer, and nothing ever changed."""
    from unittest.mock import patch
    from app.agents.trip_planner.nodes.planning_trip_node import ANSWER_SYSTEM_INSTRUCTION, REVISION_SYSTEM_INSTRUCTION
    from app.services.llm.service import llm_service

    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = await _plan_one_shot(client, headers)
    seen = []

    async def fake_stream(prompt, system_instruction=None, **_):
        seen.append((prompt, system_instruction))
        yield ("It's lovely in the morning light. Want me to move Okochi-Sanso to day 1 morning?"
               if system_instruction == ANSWER_SYSTEM_INSTRUCTION else "| Day 1 | Kyoto | **Okochi-Sanso** |")

    intents = iter(["trip_question", "casual_conversation"])

    async def fake_understand(state):
        return {"intent": next(intents)}

    with patch.object(llm_service, "generate_stream", new=fake_stream), \
            patch("app.agents.trip_planner.nodes.understand_user_msg_node.understand_user_message", new=fake_understand):
        await _stream(trip_id, headers, "Is Okochi-Sanso nicer in the morning?")
        events = await _stream(trip_id, headers, "yes")
    prompt, system = seen[1]
    assert system == REVISION_SYSTEM_INSTRUCTION
    assert "YOUR LAST MESSAGE:\nIt's lovely in the morning light. Want me to move Okochi-Sanso to day 1 morning?" in prompt
    assert any(e["type"] == "graph" for e in events)


@pytest.mark.asyncio
async def test_a_day_number_is_never_read_as_the_trip_length(client: AsyncClient):
    """"Move X to day 2" read as a 2-day trip would cut the trip and redraft it from scratch."""
    from unittest.mock import patch
    from app.agents.trip_planner.nodes.planning_trip_node import REVISION_SYSTEM_INSTRUCTION
    from app.services.llm.service import llm_service

    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = await _plan_one_shot(client, headers)
    seen = []

    async def fake_stream(prompt, system_instruction=None, **_):
        seen.append(system_instruction)
        yield "| Day 2 | Kyoto | **Okochi-Sanso** |"

    async def fake_understand(state):
        return {"intent": "update_trip", "duration_days": 2}

    with patch.object(llm_service, "generate_stream", new=fake_stream), \
            patch("app.agents.trip_planner.nodes.understand_user_msg_node.understand_user_message", new=fake_understand):
        events = await _stream(trip_id, headers, "Move Okochi-Sanso from day 3 to day 2")
    assert events[-1]["trip"]["duration_days"] == 3
    assert seen == [REVISION_SYSTEM_INSTRUCTION]


@pytest.mark.asyncio
async def test_a_budget_in_the_chat_sets_the_trip_budget_without_rewriting_the_plan(client: AsyncClient):
    from unittest.mock import patch
    from app.agents.trip_planner.nodes.planning_trip_node import ANSWER_SYSTEM_INSTRUCTION
    from app.services.llm.service import llm_service

    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = await _plan_one_shot(client, headers)
    seen = []

    async def fake_stream(prompt, system_instruction=None, **_):
        seen.append((prompt, system_instruction))
        yield "Got it, 60,000 INR it is."

    async def fake_understand(state):
        return {"intent": "update_trip", "budget_amount": 60000.0, "budget_currency": "INR"}

    with patch.object(llm_service, "generate_stream", new=fake_stream), \
            patch("app.agents.trip_planner.nodes.understand_user_msg_node.understand_user_message", new=fake_understand):
        events = await _stream(trip_id, headers, "My budget is 60,000 rupees")
    assert [system for _, system in seen] == [ANSWER_SYSTEM_INSTRUCTION]
    assert "Budget target set to 60,000 INR" in seen[0][0]  # the reply knows what was saved
    assert events[-1]["assistant_message"]["payload"]["changes"] == [
        {"status": "done", "text": "Budget target set to 60,000 INR"}]
    budget = (await client.get(f"/api/trips/{trip_id}/budget", headers=headers)).json()
    assert (budget["currency"], float(budget["target_amount"])) == ("INR", 60000.0)


@pytest.mark.asyncio
async def test_a_revision_saves_a_receipt_of_what_changed_in_the_day_plan(client: AsyncClient):
    import json
    from unittest.mock import patch
    from sqlalchemy import select
    from app.agents.trip_planner.nodes.extract_itinerary_node import EXTRACTION_INSTRUCTION
    from app.models.trip import ConversationMessage, ConversationSession
    from app.services.llm.service import llm_service
    from conftest import TestingSessionLocal

    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = await _plan_one_shot(client, headers)
    item = lambda name: {"name": name, "category": "sight", "time_of_day": None, "option": False}  # noqa: E731
    before = [{"day": 1, "base": "Kyoto", "items": [item("Fushimi Inari")]},
              {"day": 2, "base": "Kyoto", "items": [item("Kinkaku-ji")]},
              {"day": 3, "base": "Kyoto", "items": [item("Okochi-Sanso")]}]
    async with TestingSessionLocal() as db:
        rows = (await db.execute(select(ConversationMessage).join(ConversationSession)
                                 .where(ConversationSession.trip_id == uuid.UUID(trip_id)))).scalars().all()
        latest = next(row for row in rows if (row.payload or {}).get("kind") == "ITINERARY_GRAPH")
        latest.payload = {**latest.payload, "day_plan": before}
        await db.commit()

    async def fake_stream(prompt, system_instruction=None, **_):
        yield "| Day 1 | Kyoto | **Fushimi Inari**, **Okochi-Sanso** |\n| Day 2 | Kyoto | **Kinkaku-ji** |\n| Day 3 | Kyoto | Free |"

    async def fake_generate(prompt, system_instruction=None, **_):
        assert system_instruction == EXTRACTION_INSTRUCTION
        return json.dumps({"stops": [], "days": [
            {"day": 1, "base": "Kyoto", "items": [{"name": "Fushimi Inari"}, {"name": "Okochi-Sanso"}]},
            {"day": 2, "base": "Kyoto", "items": [{"name": "Kinkaku-ji"}]}, {"day": 3, "base": "Kyoto", "items": []}]})

    async def fake_understand(state):
        return {"intent": "update_trip"}

    with patch.object(llm_service, "generate_stream", new=fake_stream), patch.object(llm_service, "generate", new=fake_generate), \
            patch("app.agents.trip_planner.nodes.understand_user_msg_node.understand_user_message", new=fake_understand):
        events = await _stream(trip_id, headers, "Move Okochi-Sanso to day 1")
    assert events[-1]["assistant_message"]["payload"]["changes"] == [
        {"status": "done", "text": "Moved Okochi-Sanso from day 3 to day 1"}]


def test_day_plan_changes_are_computed_from_the_saved_plans():
    from app.agents.trip_planner.nodes.extract_itinerary_node import day_plan_changes

    item = lambda name: {"name": name, "category": "sight", "time_of_day": None, "option": False}  # noqa: E731
    before = [{"day": 1, "base": "Kyoto", "items": [item("Fushimi Inari"), item("Nishiki Market")]},
              {"day": 2, "base": "Kyoto", "items": [item("Kinkaku-ji")]}]
    after = [{"day": 1, "base": "Kyoto", "items": [item("fushimi inari")]},
             {"day": 2, "base": "Nara", "items": [item("Todai-ji"), item("Nishiki Market")]}]
    assert day_plan_changes(before, after) == [
        {"status": "done", "text": "Moved Nishiki Market from day 1 to day 2"},
        {"status": "done", "text": "Added Todai-ji to day 2"},
        {"status": "done", "text": "Removed Kinkaku-ji from day 2"},
        {"status": "done", "text": "Day 2 now based in Nara (was Kyoto)"},
    ]
    assert day_plan_changes(before, before) == []
    # Seen live: the same places came back as "Kiyomizu‑dera" (U+2011) and "Vermillion Café".
    retyped = [{"day": 1, "base": "Kyoto", "items": [item("Fushimi Inari"), item("Nishiki  Market")]},
               {"day": 2, "base": "Kyoto", "items": [item("Kinkaku‑ji")]}]
    assert day_plan_changes(before, retyped) == []
    assert day_plan_changes([{"day": 1, "base": "Kyoto", "items": [item("Vermillion Cafe")]}],
                            [{"day": 1, "base": "Kyoto", "items": [item("Vermillion Café")]}]) == []
    assert day_plan_changes([{"day": 1, "base": "Kyoto", "items": [item("Kinkaku-ji (Golden Pavilion)"), item("Gion district")]}],
                            [{"day": 1, "base": "Kyoto", "items": [item("Kinkaku-ji"), item("Gion")]}]) == []


@pytest.mark.asyncio
async def test_a_revision_extracts_days_with_the_previous_plans_place_names():
    """Re-extracting from scratch split and merged the same places differently each time."""
    from unittest.mock import patch
    from app.agents.trip_planner.nodes.extract_itinerary_node import extract_itinerary
    from app.services.llm.service import llm_service

    seen = []

    async def fake(prompt, system_instruction=None, **_):
        seen.append(prompt)
        return '{"stops": [], "days": []}'

    with patch.object(llm_service, "generate", new=fake):
        await extract_itinerary({"assistant_response": "| Day 1 | Kyoto | **Sannenzaka and Ninenzaka** |",
                                 "destination": "Kyoto", "duration_days": 1,
                                 "known_places": ["Sannenzaka", "Ninenzaka"]})
    assert "KNOWN PLACES" in seen[0] and '"Sannenzaka", "Ninenzaka"' in seen[0]


@pytest.mark.asyncio
async def test_extraction_retries_an_empty_reply_and_reads_json_after_prose():
    """Seen live: one empty extraction reply meant the studio kept the old days after a real change."""
    from unittest.mock import patch
    from app.agents.trip_planner.nodes.extract_itinerary_node import extract_itinerary
    from app.services.llm.service import llm_service

    replies = iter(["", 'Here is the JSON:\n{"stops": [], "days": [{"day": 1, "base": "Kyoto", "items": [{"name": "Nishiki Market"}]}]}'])

    async def flaky(prompt, system_instruction=None, **_):
        return next(replies)

    with patch.object(llm_service, "generate", new=flaky):
        result = await extract_itinerary({"assistant_response": "| Day 1 | Kyoto | **Nishiki Market** |",
                                          "destination": "Kyoto", "duration_days": 1})
    assert result["day_plan"][0]["items"][0]["name"] == "Nishiki Market"


@pytest.mark.asyncio
async def test_understanding_extracts_a_budget_and_its_currency():
    from unittest.mock import patch
    from app.agents.trip_planner.nodes.understand_user_msg_node import understand_user_message
    from app.services.llm.service import llm_service

    async def fake(prompt, system_instruction=None, **_):
        return '{"intent": "update_trip", "destination": null, "duration_days": null, "origin": null, ' \
               '"user_name": null, "budget_amount": "60,000", "budget_currency": "inr"}'

    with patch.object(llm_service, "generate", new=fake):
        result = await understand_user_message({"user_message": "keep it under 60k rupees"})
    assert (result["budget_amount"], result["budget_currency"]) == (60000.0, "INR")


@pytest.mark.asyncio
async def test_a_turn_lists_your_message_before_the_reply_even_with_equal_timestamps(client: AsyncClient):
    """Both rows of a turn can get the same timestamp; reloading the chat then showed the reply first."""
    from datetime import datetime, timezone
    from app.models.enums import MessageRole
    from app.models.trip import ConversationMessage
    from app.repositories.conversation import ConversationRepository
    from conftest import TestingSessionLocal

    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = (await client.post("/api/trips", headers=headers)).json()["trip_id"]
    async with TestingSessionLocal() as db:
        session = await ConversationRepository().get_active_session_by_trip_id(db, uuid.UUID(trip_id))
        stamp = datetime.now(timezone.utc)
        db.add(ConversationMessage(session_id=session.id, role=MessageRole.ASSISTANT, content="the reply", created_at=stamp))
        db.add(ConversationMessage(session_id=session.id, role=MessageRole.USER, content="my question", created_at=stamp))
        await db.commit()
    rows = (await client.get(f"/api/trips/{trip_id}/messages", headers=headers)).json()["messages"]
    assert [row["content"] for row in rows[-2:]] == ["my question", "the reply"]


@pytest.mark.asyncio
async def test_a_revision_keeps_the_day_plan_when_extraction_fails(client: AsyncClient):
    """Both LLM providers busy during extraction must not wipe the studio's days (seen live: a Gemini 503)."""
    from unittest.mock import patch
    from sqlalchemy import select
    from app.models.trip import ConversationMessage, ConversationSession
    from app.services.budget import latest_itinerary_payload
    from app.services.llm.service import llm_service
    from conftest import TestingSessionLocal

    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = await _plan_one_shot(client, headers)
    plan = [{"day": d, "base": "Kyoto", "items": [{"name": f"Place {d}", "category": "sight", "time_of_day": None, "option": False}]}
            for d in (1, 2, 3)]
    async with TestingSessionLocal() as db:  # a known good day plan on the latest itinerary message
        rows = (await db.execute(select(ConversationMessage).join(ConversationSession)
                                 .where(ConversationSession.trip_id == uuid.UUID(trip_id)))).scalars().all()
        latest = next(row for row in sorted(rows, key=lambda row: row.created_at, reverse=True)
                      if (row.payload or {}).get("kind") == "ITINERARY_GRAPH")
        latest.payload = {**latest.payload, "day_plan": plan}
        await db.commit()

    async def fake_stream(prompt, system_instruction=None, **_):
        yield "| Day 1 | Kyoto | **Fushimi Inari** |\n| Day 2 | Kyoto | **Okochi-Sanso** |"

    async def busy(*_, **__):
        raise RuntimeError("503 UNAVAILABLE")

    async def fake_understand(state):
        return {"intent": "update_trip"}

    with patch.object(llm_service, "generate_stream", new=fake_stream), patch.object(llm_service, "generate", new=busy), \
            patch("app.agents.trip_planner.nodes.understand_user_msg_node.understand_user_message", new=fake_understand):
        await _stream(trip_id, headers, "Swap day 2's afternoon for Okochi-Sanso")
    async with TestingSessionLocal() as db:
        assert (await latest_itinerary_payload(db, uuid.UUID(trip_id)))["day_plan"] == plan


@pytest.mark.asyncio
async def test_extraction_without_days_gives_no_day_plan():
    from unittest.mock import patch
    from app.agents.trip_planner.nodes.extract_itinerary_node import extract_itinerary
    from app.services.llm.service import llm_service

    async def busy(*_, **__):
        raise RuntimeError("503 UNAVAILABLE")

    with patch.object(llm_service, "generate", new=busy):
        result = await extract_itinerary({"assistant_response": "| Day 1 | Kyoto | **Fushimi Inari** |",
                                          "destination": "Kyoto", "duration_days": 2, "origin": "Delhi"})
    assert result["itinerary_graph"] and result["day_plan"] is None


def test_the_brief_intro_says_what_is_still_needed_and_that_the_rest_is_optional():
    from app.agents.trip_planner.nodes.onboarding_form_node import show_onboarding_form

    asked = show_onboarding_form({"destination": "Japan", "duration_days": 5})["assistant_response"]
    assert "Japan for 5 days" in asked and "where you're travelling from" in asked and "optional" in asked
    assert "how many days" not in asked  # already known, so not asked again
    nothing_known = show_onboarding_form({})["assistant_response"]
    assert all(part in nothing_known for part in ("where you're going", "how many days", "where you're travelling from"))
    assert "not on Earth" not in asked
    mars = show_onboarding_form({"destination": "Mars"})["assistant_response"]
    assert "Mars is not on Earth" in mars and "proof of concept" in mars


@pytest.mark.asyncio
async def test_a_new_trip_greets_a_signed_in_traveler_by_first_name():
    from app.agents.trip_planner.graph import trip_planner_graph

    named = await trip_planner_graph.ainvoke({"user_message": "", "user_name": "Rohit Maity"})
    assert named["assistant_response"].startswith("Hi Rohit, I'm Pathfinder.")
    guest = await trip_planner_graph.ainvoke({"user_message": "", "user_name": None})
    assert guest["assistant_response"].startswith("Hi, I'm Pathfinder.")
