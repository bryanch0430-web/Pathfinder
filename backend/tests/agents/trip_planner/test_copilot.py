import json
import uuid
from unittest.mock import AsyncMock, patch

from conftest import TestingSessionLocal
from httpx import AsyncClient

from app.agents.trip_planner.copilot import engine
from app.agents.trip_planner.copilot.graph import INTERPRET_INSTRUCTION, RESPOND_INSTRUCTION
from app.agents.trip_planner.copilot.research import EXTRACT_INSTRUCTION
from app.agents.trip_planner.nodes.extract_itinerary_node import build_itinerary_graph
from app.core.auth import RequestIdentity
from app.schemas.trip import SendMessageRequest
from app.services.conversation import conversation_service
from app.services.llm.service import llm_service


def _copilot(budget=12000.0, **prefs):
    copilot = engine.new_copilot(destination="Tokyo", duration_days=3, currency="JPY",
                                 preferences={"pace": "balanced", **prefs}, places=[],
                                 budget_target=budget, overrides={})
    copilot["rates"]["transfer"]["transit"] = 300.0
    engine.merge_pool(copilot, [engine.make_item(raw, "community") for raw in [
        {"name": "teamLab Planets", "area": "Toyosu", "category": "experience", "tags": ["art"], "est_cost": 3800, "duration_hours": 2},
        {"name": "Nezu Museum", "area": "Aoyama", "category": "attraction", "tags": ["museum", "quiet"], "est_cost": 1300, "duration_hours": 2},
        {"name": "Golden Gai", "area": "Shinjuku", "category": "nightlife", "tags": ["bar", "nightlife"], "est_cost": 4000, "duration_hours": 3},
        {"name": "Shinjuku Gyoen", "area": "Shinjuku", "category": "nature", "tags": ["park", "quiet"], "est_cost": 500, "duration_hours": 2},
        {"name": "Omoide Yokocho", "area": "Shinjuku", "category": "food", "tags": ["street food"], "est_cost": 2500, "duration_hours": 1.5},
    ]])
    return copilot


def test_an_add_you_ask_for_goes_over_budget_with_a_heads_up_while_suggestions_stay_within_it():
    copilot = _copilot(budget=5000.0)
    events, blocked, _ = engine.apply_ops(copilot, [{"op": "add", "name": "teamlab planets"}])
    assert events == ["Added teamLab Planets to day 1"]
    events, blocked, _ = engine.apply_ops(copilot, [{"op": "add", "name": "Golden Gai"}])
    assert blocked == [] and [i["name"] for i in copilot["days"][0]["items"]] == ["teamLab Planets", "Golden Gai"]
    assert "3100 JPY over budget" in events[0]  # 3800 + 4000 + one 300 hop - 5000
    assert engine.budget_status(copilot)["over"] is True
    assert all(s["cost_delta"] <= 0 for s in engine.rank(copilot, 2))  # only free things still fit


def test_an_explicit_move_applies_even_onto_a_full_day():
    """Seen live (Goa): "move Tito's Lane to day 2" was refused three times because day 2 was 'packed'."""
    copilot = _copilot(budget=None)
    copilot["profile"]["pace"] = "relaxed"  # 6 h a day
    engine.apply_ops(copilot, [{"op": "add", "name": "Golden Gai", "day": 2}, {"op": "add", "name": "teamLab Planets", "day": 2},
                               {"op": "add", "name": "Nezu Museum", "day": 1}])
    events, blocked, _ = engine.apply_ops(copilot, [{"op": "move", "name": "nezu museum", "day": 2}])
    assert blocked == [] and copilot["days"][0]["items"] == []
    assert [i["name"] for i in copilot["days"][1]["items"]][-1] == "Nezu Museum"
    assert events[0].startswith("Moved Nezu Museum from day 1 to day 2") and "over the 6 h" in events[0]


def test_moving_a_place_that_isnt_planned_puts_it_on_that_day_and_misses_are_reported():
    copilot = _copilot()
    events, _, _ = engine.apply_ops(copilot, [{"op": "move", "name": "Shinjuku Gyoen", "day": 3},
                                              {"op": "remove", "name": "Tokyo Tower"}])
    assert copilot["days"][2]["items"][0]["name"] == "Shinjuku Gyoen"
    assert events == ["Added Shinjuku Gyoen to day 3", "Tokyo Tower wasn't in the plan"]


def test_hard_and_soft_preferences_shape_recommendations():
    copilot = _copilot(avoid=["nightlife"], interests=["quiet parks"])
    names = [item["name"] for item in engine.rank(copilot, 1, 5)]
    assert "Golden Gai" not in names  # hard avoid never suggested
    assert names[0] == "Shinjuku Gyoen"  # soft like + quiet ranks first
    engine.apply_ops(copilot, [{"op": "remove", "name": "Nezu Museum", "reject": True},
                               {"op": "pref", "kind": "dislike", "value": "quiet parks"}])
    names = [item["name"] for item in engine.rank(copilot, 1, 5)]
    assert "Nezu Museum" not in names and copilot["profile"]["soft"]["likes"] == []
    assert engine.apply_ops(copilot, [{"op": "add", "name": "Golden Gai"}])[1][0]["reasons"][0]["code"] == "hard_avoid"


def test_moving_a_plan_to_a_same_area_day_saves_the_transfer():
    copilot = _copilot()
    engine.apply_ops(copilot, [{"op": "add", "name": "Nezu Museum", "day": 1},
                               {"op": "add", "name": "Omoide Yokocho", "day": 1},
                               {"op": "add", "name": "Shinjuku Gyoen", "day": 2}])
    move = engine.move_suggestions(copilot)[0]
    assert (move["from_day"], move["to_day"], move["saves"], move["saves_minutes"]) in {
        (1, 2, 300.0, 35)}
    events, _, _ = engine.apply_ops(copilot, [{"op": "move", "name": move["name"], "day": 2}])
    assert events[0].endswith("saving ~300 JPY")


def test_a_full_day_warns_and_invalid_ops_are_ignored_safely():
    copilot = _copilot(budget=None)
    copilot["profile"]["pace"] = "relaxed"
    engine.apply_ops(copilot, [{"op": "add", "name": "Golden Gai"}, {"op": "add", "name": "teamLab Planets"}])
    events, blocked, _ = engine.apply_ops(copilot, [{"op": "add", "name": "Nezu Museum"},
                                                    "junk", {"op": "set_budget", "amount": "NaN"},
                                                    {"op": "add", "name": "x", "day": 99, "est_cost": -5}])
    assert blocked == [] and all("over the 6 h" in event for event in events)
    assert 1 not in [d for d in engine.other_days_that_fit(copilot, copilot["pool"][3], 2)]  # day 1 is over now
    assert copilot["budget"] is None
    assert copilot["days"][0]["items"][-1]["name"] == "x" and copilot["days"][0]["items"][-1]["est_cost"] is None


def test_copilot_days_render_into_the_shared_itinerary_graph():
    copilot = _copilot()
    copilot["days"][2]["base"] = "Hakone"
    engine.apply_ops(copilot, [{"op": "add", "name": "Nezu Museum"}])
    graph = build_itinerary_graph(markdown=engine.to_markdown(copilot), origin="Delhi", destination="Tokyo",
                                  duration_days=3, extracted=engine.to_extracted(copilot, "Delhi"))
    stops = [node for node in graph["nodes"] if node["kind"] == "stop"]
    assert [(s["name"], s["day_start"], s["day_end"]) for s in stops] == [("Tokyo", 1, 2), ("Hakone", 3, 3)]
    assert stops[0]["nearby_places"][0]["name"] == "Nezu Museum"
    assert len(graph["edges"]) == 3  # Delhi → Tokyo → Hakone → Delhi


async def test_the_interpreter_reads_the_last_exchange_and_uses_the_main_model():
    """Seen live: "Move it to day 5" moved the wrong place, because 'it' was only in the previous reply."""
    from app.agents.trip_planner.copilot.graph import interpret

    seen = {}

    async def fake(prompt, system_instruction=None, **kwargs):
        seen.update(prompt=prompt, kwargs=kwargs)
        return '{"ops": [{"op": "move", "name": "Golden Gai", "day": 3}]}'

    recent = [{"role": "traveler", "text": "move golden gai to day 2"},
              {"role": "you", "text": "Day 2 is busy. Want me to put Golden Gai on day 3 instead?"}]
    with patch.object(llm_service, "generate", new=fake):
        out = await interpret({"copilot": _copilot(), "user_message": "yes, move it there", "recent": recent})
    assert "move golden gai to day 2" in seen["prompt"]
    assert "Want me to put Golden Gai on day 3 instead?" in seen["prompt"]
    assert "model" not in seen["kwargs"]  # the main model, not the fast one
    assert out["ops"] == [{"op": "move", "name": "Golden Gai", "day": 3}]


async def _fake_generate(prompt, system_instruction=None, **_):
    if system_instruction == EXTRACT_INSTRUCTION:
        return json.dumps({"bases": [{"name": "Tokyo", "days": 3}],
                           "rates": {"transit": 300, "stay_per_night": 3000, "food_per_day": 2000},
                           "candidates": [{"name": "Nezu Museum", "base": "Tokyo", "area": "Aoyama",
                                           "category": "attraction", "tags": ["museum"], "est_cost": 1300,
                                           "duration_hours": 2, "why": "Quiet garden", "source": 1}]})
    if system_instruction == INTERPRET_INSTRUCTION:
        return json.dumps({"ops": [{"op": "pref", "kind": "avoid", "value": "museums"}, {"op": "next_day"}]})
    assert system_instruction == RESPOND_INSTRUCTION
    return "Here's day " + str(json.loads(prompt.split("FACTS:\n", 1)[1])["current_day"]["day"])


async def test_build_with_agent_turns_persist_state_budget_and_graph(client: AsyncClient):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = (await client.post("/api/trips", headers=headers)).json()["trip_id"]
    url = f"/api/trips/{trip_id}/messages"
    await client.post(url, headers=headers, json={"message_type": "UI_ACTION", "payload": {
        "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi", "destination": "Tokyo", "duration_days": 3}})
    posts = [{"title": "Tokyo tips", "url": "https://www.reddit.com/r/JapanTravel/x", "content": "Nezu is calm"}]
    with patch("app.agents.trip_planner.copilot.research.community_search", new=AsyncMock(return_value=posts)), \
            patch.object(llm_service, "generate", side_effect=_fake_generate):
        started = await client.post(url, headers=headers, json={"message_type": "UI_ACTION", "payload": {
            "action": "START_BUILD_WITH_AGENT", "budget_amount": 20000, "currency": "JPY"}})
        assert started.status_code == 200
        payload = started.json()["assistant_message"]["payload"]
        assert payload["kind"] == "ITINERARY_GRAPH" and payload["graph"]["nodes"]
        assert payload["copilot"]["last_suggestions"] == ["Nezu Museum"]
        assert payload["copilot"]["pool"][0]["basis"] == "community"
        # 3000 x 2 nights + 2000 x 3 days of food already committed before any activity
        assert payload["copilot"]["rates"]["stay_per_night"] == 3000
        assert started.json()["trip"]["status"] == "PLANNING"

        added = await client.post(url, headers=headers, json={"message_type": "UI_ACTION", "payload": {
            "action": "COPILOT_OPS", "ops": [{"op": "add", "name": "Nezu Museum"}]}})
        copilot = added.json()["assistant_message"]["payload"]["copilot"]
        assert copilot["days"][0]["items"][0]["name"] == "Nezu Museum"
        # The receipt under the reply is the engine's own record, not the model's words.
        assert added.json()["assistant_message"]["payload"]["changes"] == [
            {"status": "done", "text": "Added Nezu Museum to day 1"}]

        chat = await client.post(url, headers=headers, json={"message_type": "TEXT", "content": "no museums please"})
        copilot = chat.json()["assistant_message"]["payload"]["copilot"]
        assert chat.json()["assistant_message"]["content"] == "Here's day 2"
        assert copilot["profile"]["hard"]["avoid"] == ["museums"] and copilot["current_day"] == 2

        # PLANNING-status trips with a copilot stream through the graph, not a full re-plan.
        request = SendMessageRequest(message_type="UI_ACTION", payload={
            "action": "COPILOT_OPS", "ops": [{"op": "set_mode", "value": "walk"}]})
        async with TestingSessionLocal() as db:
            events = [json.loads(chunk[6:]) async for chunk in conversation_service.process_message_stream(
                db, uuid.UUID(trip_id), request, RequestIdentity(guest_id=headers["X-Guest-ID"]))]
        assert next(e for e in events if e["type"] == "copilot")["copilot"]["profile"]["travel_mode"] == "walk"

    budget = (await client.get(f"/api/trips/{trip_id}/budget", headers=headers)).json()
    assert (budget["currency"], float(budget["target_amount"])) == ("JPY", 20000.0)
    bad = await client.post(url, headers=headers, json={"message_type": "UI_ACTION", "payload": {
        "action": "START_BUILD_WITH_AGENT", "travel_mode": "teleport"}})
    assert bad.status_code == 422


async def test_switching_days_replays_held_replies_without_model_calls(client: AsyncClient):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = (await client.post("/api/trips", headers=headers)).json()["trip_id"]
    url = f"/api/trips/{trip_id}/messages"
    await client.post(url, headers=headers, json={"message_type": "UI_ACTION", "payload": {
        "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi", "destination": "Tokyo", "duration_days": 3}})
    fake = AsyncMock(side_effect=_fake_generate)
    posts = [{"title": "Tokyo tips", "url": "https://www.reddit.com/r/JapanTravel/x", "content": "Nezu is calm"}]
    ops = lambda *items, day=None: {"message_type": "UI_ACTION", "payload": {  # noqa: E731
        "action": "COPILOT_OPS", "ops": list(items), **({"copilot_day": day} if day else {})}}
    with patch("app.agents.trip_planner.copilot.research.community_search", new=AsyncMock(return_value=posts)), \
            patch.object(llm_service, "generate", new=fake):
        started = await client.post(url, headers=headers, json={"message_type": "UI_ACTION", "payload": {
            "action": "START_BUILD_WITH_AGENT", "budget_amount": 50000, "currency": "JPY"}})
        day_one_reply = started.json()["assistant_message"]["content"]
        copilot = started.json()["assistant_message"]["payload"]["copilot"]
        assert [d["suggestions"] for d in copilot["days"]] == [["Nezu Museum"]] * 3  # every day pre-ranked
        assert copilot["days"][0]["reply"] == day_one_reply

        # Planning day 2 first: the client sends the viewed day with the action.
        await client.post(url, headers=headers, json=ops({"op": "add", "name": "Nezu Museum"}, day=2))
        calls = fake.await_count
        back = await client.post(url, headers=headers, json=ops({"op": "goto_day", "day": 1}))
        assert fake.await_count == calls  # no model call for a day switch
        assert back.json()["assistant_message"]["content"] == day_one_reply
        copilot = back.json()["assistant_message"]["payload"]["copilot"]
        assert copilot["current_day"] == 1 and copilot["days"][1]["items"][0]["name"] == "Nezu Museum"

        # A day never discussed has nothing to replay, so it gets a real reply.
        fresh = await client.post(url, headers=headers, json=ops({"op": "goto_day", "day": 3}))
        assert fake.await_count == calls + 1
        assert fresh.json()["assistant_message"]["content"].startswith("Here's day 3")

    rows = (await client.get(f"/api/trips/{trip_id}/budget", headers=headers)).json()["items"]
    stay = next(row for row in rows if row["category"] == "stay" and row["is_current"])
    assert (stay["estimate_amount"], stay["estimate_note"]) == ("3000.00", "From your build-with-agent plan")


async def test_streamed_turn_updates_map_and_panel_before_the_reply(client: AsyncClient):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = (await client.post("/api/trips", headers=headers)).json()["trip_id"]
    await client.post(f"/api/trips/{trip_id}/messages", headers=headers, json={"message_type": "UI_ACTION", "payload": {
        "action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi", "destination": "Tokyo", "duration_days": 3}})
    posts = [{"title": "Tokyo tips", "url": "https://www.reddit.com/r/JapanTravel/x", "content": "Nezu is calm"}]
    prompts = []

    async def fake_stream(prompt, system_instruction=None, **_):
        prompts.append(system_instruction)
        for token in ("Nezu ", "is ", "lovely."):
            yield token

    request = SendMessageRequest(message_type="UI_ACTION", payload={
        "action": "START_BUILD_WITH_AGENT", "budget_amount": 50000, "currency": "JPY"})
    with patch("app.agents.trip_planner.copilot.research.community_search", new=AsyncMock(return_value=posts)), \
            patch.object(llm_service, "generate", side_effect=_fake_generate), \
            patch.object(llm_service, "generate_stream", new=fake_stream):
        async with TestingSessionLocal() as db:
            events = [json.loads(chunk[6:]) async for chunk in conversation_service.process_message_stream(
                db, uuid.UUID(trip_id), request, RequestIdentity(guest_id=headers["X-Guest-ID"]))]
    kinds = [event["type"] for event in events]
    assert kinds.index("graph") < kinds.index("copilot") < kinds.index("token")  # map + panel first
    assert "stage" in kinds[: kinds.index("graph")]
    assert "".join(e["delta"] for e in events if e["type"] == "token") == "Nezu is lovely."
    assert prompts == [RESPOND_INSTRUCTION]  # the reply itself is the streamed call
    done = events[-1]["assistant_message"]
    assert done["content"] == "Nezu is lovely."
    assert done["payload"]["copilot"]["days"][0]["reply"] == "Nezu is lovely."  # held for day switches


def test_preference_receipts_read_like_a_person_wrote_them():
    copilot = _copilot()
    events, _, _ = engine.apply_ops(copilot, [
        {"op": "pref", "kind": "avoid", "value": "museums"}, {"op": "pref", "kind": "must", "value": "Tokyo Tower"},
        {"op": "pref", "kind": "like", "value": "quiet gardens"}, {"op": "pref", "kind": "dislike", "value": "shopping"}])
    assert events == ["Rule saved: no museums", "Rule saved: Tokyo Tower is a must",
                      "Noted: you like quiet gardens", "Noted: you'd rather skip shopping"]


def test_a_change_request_that_changed_nothing_says_so_in_the_receipt():
    """Seen live: the interpreter dropped "Add the Tokyo National Museum" itself, so the reply refused with no receipt."""
    from app.agents.trip_planner.copilot.graph import INTERPRET_INSTRUCTION, turn_changes

    nothing = {"events": [], "blocked": []}
    assert turn_changes({**nothing, "user_message": "Add the Tokyo National Museum to day 2"}) == [
        {"status": "skipped", "text": "Nothing in the plan changed"}]
    assert turn_changes({**nothing, "user_message": "What should I add to day 2?"}) == []  # a question, not a request
    assert turn_changes({**nothing, "user_message": ""}) == []  # a day switch or a tap with nothing to report
    assert "even when it breaks a rule in PROFILE" in INTERPRET_INSTRUCTION  # the planner decides, not the interpreter


def test_a_reply_never_claims_a_change_the_planner_didnt_make():
    """Seen live (guide shot 17): Ootoya was added, but the reply also said "I've added Omide Yokocho"."""
    from app.agents.trip_planner.copilot.graph import ClaimGuard, guard_reply

    facts = {
        "events": ["Rule saved: no museum", "Added Ootoya to day 2"],
        "suggestions": [{"name": "Omide Yokocho"}, {"name": "Shiba Park"}],
        "blocked": [], "current_day": {"items": [{"name": "Mejiro Garden"}, {"name": "Ootoya"}]},
        "days_overview": [{"items": ["Kyu-Yasuda Garden"]}],
    }
    reply = ("Your no-museum rule is saved, and Ootoya is now on day 2. I've added Omide Yokocho for a local lunch "
             "today (≈¥2,000, 1 hr). With the rain forecast, pack an umbrella. Want me to add Shiba Park too? "
             "Shiba Park is nearby, and I couldn't add a museum for you.\n- I'll move Kyu-Yasuda Garden to day 3.\n"
             "Does that work for you?")
    assert guard_reply(reply, facts) == (
        "Your no-museum rule is saved, and Ootoya is now on day 2. With the rain forecast, pack an umbrella. "
        "Want me to add Shiba Park too? Shiba Park is nearby, and I couldn't add a museum for you.\n"
        "Does that work for you?")

    # Streaming gives the same result whatever the token boundaries are.
    guard = ClaimGuard(facts)
    streamed = "".join(guard.feed(reply[i:i + 7]) for i in range(0, len(reply), 7)) + guard.flush()
    assert streamed.strip() == guard_reply(reply, facts)
    # On a turn that changed nothing, any "I've moved it" is false even without a name.
    assert guard_reply("I've moved it to day 3. Anything else?", {"events": []}) == "Anything else?"


async def test_research_retries_on_the_main_model_when_the_fast_one_returns_nothing():
    """Seen live: the fast model returned broken JSON twice, and build-with-agent opened with nothing to suggest."""
    from app.agents.trip_planner.copilot.research import research

    models = []

    async def fake(prompt, system_instruction=None, **kwargs):
        models.append(kwargs.get("model"))
        if kwargs.get("model"):
            return '{"candidates": [{"name": "Nezu Mus'  # truncated
        return json.dumps({"rates": {}, "candidates": [{"name": "Nezu Museum", "base": "Tokyo", "area": "Aoyama",
                                                         "category": "attraction", "tags": [], "est_cost": 1300,
                                                         "duration_hours": 2, "why": "Quiet garden", "source": None}]})

    copilot = engine.new_copilot(destination="Tokyo", duration_days=3, currency="JPY", preferences={}, places=[],
                                 budget_target=None, overrides={})
    with patch("app.agents.trip_planner.copilot.research.community_search", new=AsyncMock(return_value=[])), \
            patch.object(llm_service, "generate", new=fake):
        added = await research(copilot, ["Tokyo 3 day itinerary advice"])
    assert added == 1 and copilot["pool"][0]["name"] == "Nezu Museum"
    assert models[0] and models[1] is None  # the fast model first, then the main one
