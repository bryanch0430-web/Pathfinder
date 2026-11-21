"""Admin gate, prompt overrides, hotel references, and one-step plan restore."""
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.enums import ConversationSessionStatus, ConversationStage, MessageRole, MessageType, OnboardingStatus, TripStatus
from app.models.trip import ConversationMessage, ConversationSession, Trip
from app.models.user import User
from app.routing.models import Intent, RouteDecision
from app.routing.tools import hotel_context, hotels_from_wikitext
from app.services.admin_settings import active_instruction
from app.core.database import get_db
from app.main import app
from app.services.local_auth import hash_password, issue_token


async def _account(admin: bool) -> tuple[str, str]:
    user = User(
        email=f"{'admin' if admin else 'traveler'}-{uuid.uuid4().hex[:8]}@pathfinder.test",
        password_hash=hash_password("secretsecret"),
        full_name="Admin" if admin else "Traveler",
        is_admin=admin,
    )
    generator = app.dependency_overrides[get_db]()
    session = await generator.__anext__()
    try:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    finally:
        await generator.aclose()
    token, _ = issue_token(user)
    return user.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_admin_settings_are_closed_to_a_traveller(client: AsyncClient):
    _, token = await _account(False)
    response = await client.get("/api/admin/settings", headers=_auth(token))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_save_the_gate_and_a_prompt(client: AsyncClient):
    _, token = await _account(True)
    saved = await client.put(
        "/api/admin/settings",
        headers=_auth(token),
        json={"router_threshold": 0.66, "rules_first": False},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["router_threshold"] == 0.66
    assert body["rules_first"] is False

    from app.routing.system1 import build_router
    router = build_router()
    assert router.threshold == 0.66
    assert router.rules_first is False

    prompt = await client.put(
        "/api/admin/prompts/plan",
        headers=_auth(token),
        json={"body": "TEST INSTRUCTION FOR THE DRAFT"},
    )
    assert prompt.status_code == 200
    assert active_instruction("plan", "code default") == "TEST INSTRUCTION FOR THE DRAFT"

    reset = await client.delete("/api/admin/prompts/plan", headers=_auth(token))
    assert reset.status_code == 200
    assert active_instruction("plan", "code default") == "code default"


def test_wikivoyage_sleep_section_names_three_hotels():
    text = "== See ==\n* [[Temple]]\n== Sleep ==\n* [[Ryokan One]]\n* [[Inn Two|note]]\n* [[Hotel Three]]\n* [[Hotel Four]]\n== Eat ==\n"
    assert hotels_from_wikitext(text) == ["Ryokan One", "Inn Two", "Hotel Three"]


@pytest.mark.asyncio
async def test_hotel_context_uses_wikivoyage_without_a_serp_key():
    os.environ["PATHFINDER_DRAFT_LOOKUPS"] = "1"
    payload = {"parse": {"wikitext": {"*": "== Sleep ==\n* [[Kyoto Inn]]\n== Eat ==\n"}}}
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    client = AsyncMock()
    client.get.return_value = response
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    try:
        with patch("app.routing.tools.httpx.AsyncClient", return_value=client):
            text = await hotel_context("Kyoto")
    finally:
        os.environ.pop("PATHFINDER_DRAFT_LOOKUPS", None)
    assert "Kyoto Inn" in text
    assert "Wikivoyage" in text


def test_any_three_letter_currency_is_accepted():
    from app.schemas.trip import OnboardingFormSubmission
    saved = OnboardingFormSubmission(origin="Hong Kong", destination="Taipei", duration_days=3, currency="twd", planning_preferences={"home_currency": "krw"})
    assert saved.currency == "TWD"
    assert saved.planning_preferences.home_currency == "KRW"


def test_mars_to_jupiter_is_named_and_still_planned():
    from app.agents.trip_planner.nodes.planning_trip_node import build_plan_generation_prompt
    prompt = build_plan_generation_prompt({}, "Jupiter", 4, origin="Mars")
    assert "not on Earth" in prompt
    assert "Mars" in prompt and "Jupiter" in prompt
    assert "proof of concept" in prompt


def test_a_draft_prompt_keeps_hotel_references():
    from app.agents.trip_planner.nodes.planning_trip_node import build_plan_generation_prompt
    prompt = build_plan_generation_prompt(
        {"lookups": "Hotel references (Wikivoyage, check before booking): Kyoto Inn"},
        "Kyoto", 3,
    )
    assert "Kyoto Inn" in prompt
    assert "Do not invent a rate" in prompt


def test_route_tells_the_chat_which_slots_are_missing():
    decision = RouteDecision(
        intent=Intent.PLAN, confidence=0.4, need_clarification=True,
        reason_code="missing_origin", missing_slots=["origin"],
    )
    assert decision.public()["missing_slots"] == ["origin"]


@pytest.mark.asyncio
async def test_undo_restores_the_previous_plan(client: AsyncClient):
    user_id, token = await _account(True)
    trip = Trip(
        user_id=user_id, destination="Kyoto", origin_text="Hong Kong", duration_days=3,
        status=TripStatus.COMPLETED, onboarding_status=OnboardingStatus.COMPLETE, currency="HKD",
        places_to_visit=[], planning_preferences={},
    )
    generator = app.dependency_overrides[get_db]()
    session = await generator.__anext__()
    try:
        session.add(trip)
        await session.flush()
        conversation = ConversationSession(
            trip_id=trip.id, status=ConversationSessionStatus.COMPLETED, current_stage=ConversationStage.COMPLETE,
        )
        session.add(conversation)
        await session.flush()
        session.add(ConversationMessage(
            session_id=conversation.id, role=MessageRole.ASSISTANT, message_type=MessageType.TEXT,
            content="New draft",
            payload={
                "kind": "ITINERARY_GRAPH",
                "graph": {"version": 8, "nodes": [{"id": "new"}], "edges": []},
                "previous": {"graph": {"version": 8, "nodes": [{"id": "old"}], "edges": []}, "day_plan": [{"day": 1}]},
            },
        ))
        await session.commit()
        trip_id = str(trip.id)
    finally:
        await generator.aclose()

    marked = await client.post(f"/api/trips/{trip_id}/feedback", headers=_auth(token), json={"useful": True})
    assert marked.status_code == 200
    assert marked.json()["useful"] is True

    restored = await client.post(f"/api/trips/{trip_id}/undo", headers=_auth(token))
    assert restored.status_code == 200
    assert restored.json()["payload"]["graph"]["nodes"][0]["id"] == "old"
