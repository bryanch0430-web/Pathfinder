"""Trip brief v2 and the normalized trip document (plan.md, Session 1)."""

import json
import uuid
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from app.agents.trip_planner.nodes.extract_itinerary_node import EXTRACTION_INSTRUCTION, normalize_day_plan
from app.services.llm.service import llm_service
from tests.agents.trip_planner.test_copilot import _fake_generate

BRIEF = {
    "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi", "destination": "Kyoto", "duration_days": 3,
    "planning_preferences": {
        "pace": "relaxed", "interests": ["temples"], "avoid": [], "start_date": "2026-10-12",
        "adults": 2, "children": 1, "comfort": "comfortable", "travel_mode": "walk", "guide": "aoi",
    },
    "budget_amount": 150000, "currency": "JPY",
}


async def _trip(client: AsyncClient, headers: dict, brief: dict = BRIEF):
    trip_id = (await client.post("/api/trips", headers=headers)).json()["trip_id"]
    response = await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                 json={"message_type": "UI_ACTION", "payload": brief})
    return trip_id, response


async def test_brief_v2_is_stored_and_budget_lands_in_the_ledger(client: AsyncClient):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id, response = await _trip(client, headers)
    prefs = response.json()["trip"]["planning_preferences"]
    assert (prefs["start_date"], prefs["adults"], prefs["children"], prefs["comfort"], prefs["guide"]) == (
        "2026-10-12", 2, 1, "comfortable", "aoi")
    budget = (await client.get(f"/api/trips/{trip_id}/budget", headers=headers)).json()
    assert (budget["currency"], budget["target_amount"]) == ("JPY", "150000.00")

    for bad in ({"adults": 0}, {"guide": "Not A Slug!"}, {"start_date": "12/10/2026"}, {"comfort": "lavish"}):
        brief = {**BRIEF, "planning_preferences": {**BRIEF["planning_preferences"], **bad}}
        assert (await _trip(client, headers, brief))[1].status_code == 422


async def test_one_shot_document_has_dated_days_from_the_extracted_day_plan(client: AsyncClient):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id, _ = await _trip(client, headers)

    async def fake(prompt, system_instruction=None, **_):
        if system_instruction == EXTRACTION_INSTRUCTION:
            return json.dumps({"stops": [{"name": "Kyoto", "day_start": 1, "day_end": 3}], "days": [
                {"day": 2, "base": "Kyoto", "items": [{"name": "Fushimi Inari", "category": "sight",
                                                       "time_of_day": "morning"}]},
                {"day": 1, "base": "Kyoto", "items": [{"name": "Nishiki Market", "category": "FOOD",
                                                       "time_of_day": "noon"}]}]})
        return "| Day 1 | Kyoto | **Nishiki Market** |\n| Day 2 | Kyoto | **Fushimi Inari** |\n| Day 3 | Kyoto | Free |"

    route_state = {"route_points": {}, "route_times": {}, "route_queries": [], "route_requests": [],
                   "warnings": [{"day": 1, "severity": "unverified", "message": "Location not verified",
                                 "travel_hours": 1.17, "distance_km": 0, "basis": "distance estimate"}]}
    with patch.object(llm_service, "generate", side_effect=fake), \
            patch("app.services.route_reality.assess_itinerary", new=AsyncMock(return_value=route_state)):
        response = await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                     json={"message_type": "UI_ACTION", "payload": {"action": "GENERATE_FULL_ITINERARY"}})
    assert response.json()["assistant_message"]["payload"]["route_reality"]["warnings"][0]["day"] == 1
    doc = (await client.get(f"/api/trips/{trip_id}/document", headers=headers)).json()
    assert doc["mode"] == "one_shot"
    assert (doc["start_date"], doc["end_date"]) == ("2026-10-12", "2026-10-14")
    assert [(d["day"], d["date"], d["base"]) for d in doc["days"]] == [
        (1, "2026-10-12", "Kyoto"), (2, "2026-10-13", "Kyoto"), (3, "2026-10-14", "Kyoto")]
    assert doc["days"][0]["items"] == [{"name": "Nishiki Market", "category": "food", "time_of_day": None,
                                        "area": None, "est_cost": None, "duration_hours": None,
                                        "tip": None, "source_url": None, "option": False}]
    assert doc["days"][1]["items"][0]["time_of_day"] == "morning"
    assert doc["travelers"] == {"adults": 2, "children": 1} and doc["budget"]["target"] == 150000
    assert doc["graph"]["nodes"] and doc["legs"][0]["source"] == "Delhi"
    assert doc["days"][0]["travel_hours"] is not None
    assert doc["route_warnings"][0]["message"] == "Location not verified"

    forbidden = await client.get(f"/api/trips/{trip_id}/document", headers={"X-Guest-ID": str(uuid.uuid4())})
    assert forbidden.status_code == 403


async def test_agent_document_carries_costs_tips_and_party_size(client: AsyncClient):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    brief = {**BRIEF, "destination": "Tokyo", "budget_amount": 90000}
    trip_id, _ = await _trip(client, headers, brief)
    posts = [{"title": "Tokyo tips", "url": "https://www.reddit.com/r/JapanTravel/x", "content": "Nezu is calm"}]
    url = f"/api/trips/{trip_id}/messages"
    with patch("app.agents.trip_planner.copilot.research.community_search", new=AsyncMock(return_value=posts)), \
            patch.object(llm_service, "generate", side_effect=_fake_generate):
        started = await client.post(url, headers=headers, json={"message_type": "UI_ACTION", "payload": {
            "action": "START_BUILD_WITH_AGENT"}})
        copilot = started.json()["assistant_message"]["payload"]["copilot"]
        # One-click start: getting around, party size and comfort come from the brief.
        assert (copilot["profile"]["travel_mode"], copilot["profile"]["travelers"],
                copilot["profile"]["comfort"], copilot["budget"]) == ("walk", 3, "comfortable", 90000)
        added = await client.post(url, headers=headers, json={"message_type": "UI_ACTION", "payload": {
            "action": "COPILOT_OPS", "ops": [{"op": "add", "name": "Nezu Museum", "day": 1}]}})
    doc = (await client.get(f"/api/trips/{trip_id}/document", headers=headers)).json()
    ledger = (await client.get(f"/api/trips/{trip_id}/budget", headers=headers)).json()
    assert added.json()["assistant_message"]["payload"]["copilot"]["summary"]["committed"] == float(ledger["projected_total"])
    assert doc["budget"]["planned"] == float(ledger["projected_total"])
    assert doc["mode"] == "agent" and doc["status"] == "building"
    item = doc["days"][0]["items"][0]
    assert (item["name"], item["est_cost"], item["tip"], item["source_url"]) == (
        "Nezu Museum", 1300, "Quiet garden", "https://www.reddit.com/r/JapanTravel/x")
    assert doc["days"][0]["est_cost"] == 3900  # per-person estimate x 3 travelers
    # stay 3000/night x 2 rooms x 2 nights + food 2000 x 3 people x 3 days + the museum for 3
    assert doc["budget"]["planned"] == 3000 * 2 * 2 + 2000 * 3 * 3 + 3900


def test_day_plan_normalizer_covers_every_day_once():
    graph = {"nodes": [{"id": "stop-1", "kind": "stop", "name": "Osaka", "day_start": 1, "day_end": 1},
                       {"id": "stop-2", "kind": "stop", "name": "Kyoto", "day_start": 2, "day_end": 3}]}
    raw = [{"day": 2, "base": "", "items": [{"name": "Gion", "category": "sight", "option": True}, {"name": "gion"}]},
           {"day": 2, "base": "Dup", "items": []}, {"day": 9, "base": "Out of range"}, "junk"]
    days = normalize_day_plan(raw, 3, graph, "Japan")
    assert [(d["day"], d["base"], len(d["items"])) for d in days] == [(1, "Osaka", 0), (2, "Kyoto", 1), (3, "Kyoto", 0)]
    assert days[1]["items"][0]["option"] is True
    assert days[1]["items"][0]["start"] is None


def test_day_plan_keeps_clock_times_and_derives_the_part_of_day():
    days = normalize_day_plan(
        [{"day": 1, "base": "Kyoto", "items": [
            {"name": "Fushimi Inari", "category": "sight", "start": "9:00", "end": "11:00"},
            {"name": "Late ramen", "category": "food", "start": "19:30"},
            {"name": "Mystery", "category": "sight", "start": "nope", "end": "09:00"},
        ]}],
        1, None, "Kyoto",
    )
    assert [(item["name"], item["start"], item["end"], item["time_of_day"]) for item in days[0]["items"]] == [
        ("Fushimi Inari", "09:00", "11:00", "morning"),
        ("Late ramen", "19:30", None, "evening"),
        ("Mystery", None, None, None),
    ]
    assert normalize_day_plan(None, 2, None, "Japan") == [
        {"day": 1, "base": "Japan", "items": []}, {"day": 2, "base": "Japan", "items": []}]
