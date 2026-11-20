"""The router upgrades change a turn, a plan, or a model copy."""
import uuid

import pytest
from httpx import AsyncClient

from app.desk.policy import asks_for_a_person, note_unchanged_edit
from app.desk.suite import offline_cases
from app.planning.upgrades import (
    SerpApiRing,
    apply_flight_window,
    fill_asked_slot,
    flight_window,
    mask_for_model,
    move_far_stops,
    move_stop,
    place_decision,
    render_plan,
    restore_locks,
    review_days,
    stitch_cities,
    worker_block,
)
from app.security.pii import mask_pii


def test_a_full_draft_uses_the_same_named_places_as_add_to_day():
    from app.planning.ground import fill_vague_stops, is_vague
    assert is_vague("Dotonbori street-food wander")
    assert not is_vague("Fly Hong Kong to Osaka")
    assert is_vague("Temples & traditional experiences")
    assert is_vague("Modern art afternoon")
    assert is_vague("A major modern art museum or a gallery")
    assert not is_vague("Senso-ji")
    assert not is_vague("OKO - Fun Okonomiyaki Bar")
    assert not is_vague("Osaka Castle")
    days = [{"day": 1, "base": "Osaka", "items": [
        {"name": "Fly Hong Kong to Osaka", "category": "transit"},
        {"name": "Hotel check-in (Namba area)", "category": "stay"},
        {"name": "Dotonbori street-food wander", "category": "food"},
        {"name": "Dinner in the Dotonbori lanes", "category": "food"},
    ]}]
    listings = {"Osaka": [
        {"name": "Endo Sushi", "tab": "food", "source_url": "https://en.wikivoyage.org/wiki/Osaka", "blurb": "Sushi in the market.", "hours": "10:00–14:00"},
        {"name": "Kuromon Market", "tab": "food"},
        {"name": "Hotel Nikko Osaka", "tab": "stay"},
        {"name": "Osaka Castle", "tab": "see"},
    ]}
    filled = fill_vague_stops(days, listings)
    names = [item["name"] for item in filled[0]["items"]]
    assert names[0] == "Fly Hong Kong to Osaka"
    assert names[1:] == ["Hotel Nikko Osaka", "Endo Sushi", "Kuromon Market"]
    assert filled[0]["items"][2]["source_url"].endswith("/Osaka")
    themed = [{"day": 2, "base": "Osaka", "items": [
        {"name": "Temples & traditional experiences", "category": "sight"},
        {"name": "OKO - Fun Okonomiyaki Bar", "category": "food"},
    ]}]
    kept = fill_vague_stops(themed, listings, only_unmappable=True)
    assert [item["name"] for item in kept[0]["items"]] == ["Osaka Castle", "OKO - Fun Okonomiyaki Bar"]
    home = [{"day": 8, "base": "Hakone", "items": [
        {"name": "Hakone Shrine", "category": "sight"},
        {"name": "Return to Tokyo", "category": "transit"},
    ]}]
    stayed = fill_vague_stops(home, {
        "Hakone": [{"name": "Hakone Shrine", "tab": "see"}],
        "Tokyo": [{"name": "Park Hotel Tokyo", "tab": "stay", "source_url": "https://maps.google.com/tokyo"}],
    }, only_unmappable=True)
    assert [item["name"] for item in stayed[0]["items"]] == ["Hakone Shrine", "Park Hotel Tokyo"]
    assert stayed[0]["base"] == "Tokyo"
    assert stayed[0]["items"][1]["category"] == "stay"
    from datetime import date
    from app.planning.tickets import attach_trip_tickets
    booked, note = attach_trip_tickets(
        [{"day": 1, "base": "Osaka", "items": [{"name": "Flight from Hong Kong to Osaka", "category": "transit"}]},
         {"day": 10, "base": "Tokyo", "items": [{"name": "Senso-ji", "category": "sight"}]}],
        origin="Hong Kong", destination="Japan", start=date(2026, 11, 30), duration_days=10,
    )
    from urllib.parse import unquote
    outbound_url = unquote(booked[0]["items"][0]["source_url"])
    inbound_url = unquote(booked[-1]["items"][-1]["source_url"])
    assert outbound_url.startswith("https://www.google.com/travel/flights")
    assert "One way flights from Hong Kong to Osaka" in outbound_url
    assert "One way flights from Tokyo to Hong Kong" in inbound_url
    assert booked[-1]["items"][-1]["name"].startswith("Flight Tokyo")
    assert note.index("Ticket outbound") < note.index("Ticket inbound")
    assert "\n\n" in note and "Nothing is reserved" in note and "30 Nov" in note and "09 Dec" in note
    from app.planning.ground import schedule_text, stop_facts
    shown = [{"day": 1, "base": "Osaka", "items": [
        {"name": "Blarney Stone Umeda", "start": "18:00", "source_url": "https://en.wikivoyage.org/wiki/Osaka", "tip": "A bar in Umeda."},
        {"name": "Hanadako", "start": "19:30"},
    ]}]
    text = schedule_text(shown)
    assert "Blarney Stone Umeda" in text and "Hanadako" in text and "already on the trip" in text
    facts = stop_facts(shown, "Blarney Stone Umeda")
    assert "day 1" in facts and "already on the plan" in facts and "Umeda" in facts


def test_swapping_a_restaurant_changes_that_stop_only():
    from app.planning.swap import apply_swap, avoid_word, find_target, named_target, pick_other, wants_swap
    days = [{"day": 2, "base": "Osaka", "items": [
        {"name": "Endo Sushi", "category": "food", "start": "13:00"},
        {"name": "Osaka Museum of History", "category": "sight"},
    ]}]
    assert wants_swap("swap it to Blarney Stone Umeda")
    assert wants_swap("please change to something other than sushi")
    assert wants_swap("can change to another restaurant?")
    assert not wants_swap("what is this?")
    assert not wants_swap("didn't see any changes")
    assert not wants_swap("move the museum to the morning")
    assert named_target("swap it to Blarney Stone Umeda") == "Blarney Stone Umeda"
    assert named_target("change to another restaurant") is None
    assert avoid_word("please change to something other than sushi") == "sushi"
    target = find_target(days, "swap it to Blarney Stone Umeda", None, "Endo Sushi is already on day 2")
    assert target == (0, 0)
    updated = apply_swap(days, *target, {
        "name": "Blarney Stone Umeda", "source_url": "https://maps.google.com/?q=blarney",
        "blurb": "A pub in Umeda.", "hours": "Monday: 5 PM – 11 PM",
    })
    assert updated[0]["items"][0]["name"] == "Blarney Stone Umeda"
    assert "maps.google.com" in updated[0]["items"][0]["source_url"]
    assert updated[0]["items"][1]["name"] == "Osaka Museum of History"
    assert days[0]["items"][0]["name"] == "Endo Sushi"
    picked = pick_other(
        [{"name": "Endo Sushi", "blurb": "sushi counter"}, {"name": "Kani Doraku", "blurb": "crab restaurant"}],
        current="Endo Sushi", avoid="sushi", used=set(),
    )
    assert picked["name"] == "Kani Doraku"


def test_swap_back_restores_the_place_the_last_swap_removed():
    from app.planning.swap import brief_hours, named_target, restore_swap, wants_restore
    assert wants_restore("i mean this, can you swap it back to what you proposed")
    assert named_target("swap it back to what you proposed") is None
    assert named_target("swap it back to Dotonbori") == "Dotonbori"
    week = "; ".join(f"{day}: 12:00 – 3:30 PM, 5:00 PM – 2:30 AM" for day in
                      ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))
    assert brief_hours(week) == "Every day 12:00 – 3:30 PM, 5:00 PM – 2:30 AM"
    days = [
        {"day": 1, "base": "Osaka", "items": [{"name": "The Fire", "category": "food", "start": "12:00"}]},
        {"day": 2, "base": "Osaka", "items": [{"name": "Kobe Beef WANOMIYA", "category": "food", "start": "18:00"}]},
    ]
    previous = [
        {"day": 1, "base": "Osaka", "items": [{"name": "The Fire", "category": "food", "start": "12:00"}]},
        {"day": 2, "base": "Osaka", "items": [{"name": "Dotonbori", "category": "food", "start": "18:00", "tip": "Street food.", "source_url": "https://maps.google.com/?q=dotonbori"}]},
    ]
    context = "Swapped Dotonbori for Kobe Beef WANOMIYA on day 2. Japanese. Source: https://maps.google.com/?cid=10009493317624886686"
    restored = restore_swap(days, previous, context)
    assert restored["new"] == "Dotonbori"
    assert restored["days"][0]["items"][0]["name"] == "The Fire"
    assert restored["days"][1]["items"][0]["name"] == "Dotonbori"
    assert restored["days"][1]["items"][0]["source_url"].endswith("dotonbori")
    assert "what you proposed" not in restored["text"]


def test_please_add_a_place_is_not_a_yes_to_finalize():
    from app.services.conversation import _bare_agreement
    assert _bare_agreement("Yes, finalize")
    assert _bare_agreement("please")
    assert not _bare_agreement("Please add Endo Sushi to day 1.")


def test_a_booking_or_weather_question_is_answered_not_rewritten():
    from datetime import date
    from app.routing.facts import asked_day, fact_topics, listing_answer
    from app.services.conversation import _follow_up_mode
    assert fact_topics("我間餐廳洗唔洗book定") == {"booking"}
    assert fact_topics("1/1開唔開") == {"hours"}
    assert fact_topics("day 2 weather") == {"weather"}
    assert _follow_up_mode("day 2 weather?", "update_trip", False) == "answer"
    assert _follow_up_mode("move the museum to the morning", None, False) == "revise"
    assert asked_day("day 2 weather", 5) == 2
    booked = listing_answer("Nishiki", {"reservable": True, "hours": ["Monday: 11:00–21:00"] * 7}, date(2027, 1, 1), {"booking", "hours"})
    assert "takes reservations" in booked and "doesn't make the booking" not in booked and "I don't make the booking" in booked
    assert "Friday" in booked or "Usual hours" in booked
    unknown = listing_answer("Nishiki", None, date(2027, 1, 1), {"booking", "hours"})
    assert "don't have a live listing" in unknown and "don't book" in unknown
    assert asks_for_a_person("I want to talk to a live agent")
    assert asks_for_a_person("请转人工")
    assert not asks_for_a_person("Please don't talk to a live agent")
    assert not asks_for_a_person("Plan 3 days in Kyoto")


def test_the_third_unchanged_edit_waits_for_a_person():
    desk = {"status": "auto", "reason": "", "stuck": 0, "last_words": []}
    for _ in range(2):
        desk = note_unchanged_edit(desk, "move the museum again", unchanged=True)
        assert desk["status"] == "auto"
    desk = note_unchanged_edit(desk, "move the museum again", unchanged=True)
    assert desk["status"] == "queued" and desk["reason"] == "stuck"
    cleared = note_unchanged_edit(desk, "add a temple", unchanged=False)
    assert cleared["stuck"] == 0


def test_personal_details_are_masked_and_a_bad_card_is_not():
    masked = mask_pii("Email a@b.co, call 9123 4567, id A123456(3), card 4111111111111111.")
    assert "[EMAIL]" in masked and "[PHONE]" in masked and "[HKID]" in masked and "[CARD ****1111]" in masked
    assert mask_pii("card 4111111111111112") == "card 4111111111111112"
    assert "Nezu" in mask_for_model("what is this?", "Nezu Museum")


def test_a_short_answer_fills_the_asked_slot_and_a_new_trip_does_not():
    assert fill_asked_slot("Hong Kong", "I just need where you're travelling from.", destination="Kyoto", origin=None) == {"origin": "Hong Kong"}
    assert fill_asked_slot("Plan 3 days in Hong Kong", "I just need where you're travelling from.", destination="Kyoto", origin=None) == {}


def test_an_unknown_place_waits_and_a_known_city_does_not():
    assert place_decision("Kyoto", None, "Kyoto, Japan") == ("Kyoto", None)
    stored, question = place_decision("Heaven", "Kyoto", "Heaven, Texas")
    assert stored == "Kyoto" and question and "Heaven, Texas" in question


def test_evidence_locks_flights_cities_and_the_post_draft_checks():
    block = worker_block(
        hotels=[{"rank": 1, "name": "Ryokan", "url": "https://example.test/ryokan", "picture": "inn.jpg"}],
        meals=[{"slot": "lunch", "name": "Nishiki", "backups": ["Pontocho", "Market"]}],
    )
    assert "https://example.test/ryokan" in block and "Pontocho" in block and "Do not invent a price" in block
    window = flight_window("22:45", "16:00")
    assert window["day1_open"] == "00:45" and window["last_close"] == "12:30"
    opened = apply_flight_window([{"day": 1, "items": [{"name": "Temple", "start": "08:00"}]}], window)
    assert opened[0]["items"][0]["start"] == "00:45"
    days = [
        {"day": 1, "items": [{"name": "Far", "lat": 0.0, "lon": 0.0}, {"name": "Near", "lat": 0.2, "lon": 0.0}]},
        {"day": 2, "items": [{"name": "There", "lat": 0.01, "lon": 0.0}]},
    ]
    assert any(note.startswith("Moved Far") for note in move_far_stops(days))
    assert days[1]["items"][-1]["name"] == "Far"
    stitched = stitch_cities([{"city": "Kyoto", "days": [{"day": 9, "items": []}]}, {"city": "Osaka", "days": [{"day": 1, "items": []}]}])
    assert [(day["day"], day["city"]) for day in stitched] == [(1, "Kyoto"), (2, "Osaka")]
    restored = restore_locks(
        [{"day": 1, "items": [{"name": "Locked Inn"}]}],
        [{"day": 1, "items": [{"name": "Other Inn"}]}],
        [{"name": "Locked Inn", "day": 1, "kind": "stay"}],
    )
    assert {item["name"] for item in restored[0]["items"]} == {"Other Inn", "Locked Inn"}
    notes = review_days(
        [{"day": 1, "items": [{"name": "City Park", "category": "sight", "minutes": 20, "price": 12, "city": "Osaka"}]}],
        known={"City Park"}, city="Kyoto",
    )
    assert any("minutes" in note for note in notes) and any("Osaka" in note for note in notes) and any("no meal" in note for note in notes)
    assert render_plan("Walk to Fushimi", "zh-Hant") == "步行 to Fushimi"
    assert render_plan("Walk to Fushimi", "zh-Hant", {"Fushimi": "伏見"}) == "步行 to 伏見"
    ring = SerpApiRing(["spent", "ready"])
    assert ring.retire_if_quota("the account has run out of searches") == "ready"
    assert ring.exhausted == ["spent"]
    moved = move_stop([{"day": 1, "items": [{"name": "Temple", "start": "09:00"}, {"name": "Lunch", "start": "12:00"}]}], day=1, name="Temple", start="15:00")
    assert moved[0]["items"][0]["start"] == "15:00" and moved[0]["items"][1]["start"] == "12:00"


def test_the_draft_prompt_keeps_sourced_options_and_a_lock():
    from app.agents.trip_planner.nodes.planning_trip_node import build_followup_prompt, build_plan_generation_prompt
    prompt = build_plan_generation_prompt(
        {"workers": worker_block(hotels=[{"rank": 1, "name": "Ryokan", "url": "https://example.test/ryokan"}])},
        "Kyoto", 2, origin="Hong Kong",
    )
    assert "https://example.test/ryokan" in prompt and "Do not invent a price" in prompt
    follow = build_followup_prompt("Day 1 Temple", "change lunch", "Kyoto", 2, "Hong Kong", {"locks": [{"kind": "stay", "name": "Locked Inn", "day": 1}]})
    assert "LOCKED" in follow and "Locked Inn" in follow


def test_offline_cases_all_pass():
    cases = offline_cases()
    failed = [case["name"] for case in cases if not case["passed"]]
    assert not failed


@pytest.mark.asyncio
async def test_asking_for_a_person_queues_the_chat_and_skips_the_planner(client: AsyncClient):
    from app.core.database import get_db
    from app.main import app
    from app.models.user import User
    from app.services.local_auth import hash_password, issue_token

    guest = {"X-Guest-ID": str(uuid.uuid4())}
    trip_id = (await client.post("/api/trips", headers=guest)).json()["trip_id"]
    asked = await client.post(
        f"/api/trips/{trip_id}/messages", headers=guest,
        json={"message_type": "TEXT", "content": "I want to talk to a live agent"},
    )
    assert asked.status_code == 200
    body = asked.json()["assistant_message"]
    assert body["payload"]["kind"] == "DESK"
    assert "person will take this chat" in body["content"]
    opened = None

    user = User(email=f"desk-{uuid.uuid4().hex[:8]}@pathfinder.test", password_hash=hash_password("secretsecret"), full_name="Desk", is_admin=True)
    generator = app.dependency_overrides[get_db]()
    session = await generator.__anext__()
    try:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    finally:
        await generator.aclose()
    admin = {"Authorization": f"Bearer {issue_token(user)[0]}"}

    queue = await client.get("/api/admin/desk", headers=admin)
    assert queue.status_code == 200
    assert any(row["trip_id"] == trip_id and row["reason"] == "asked_for_a_person" and row["lane"] == "waiting" for row in queue.json()["chats"])
    detail = await client.get(f"/api/admin/desk/{trip_id}", headers=admin)
    assert detail.status_code == 200
    assert any(row["speaker"] == "traveller" and "live agent" in row["content"] for row in detail.json()["messages"])

    held = await client.post(f"/api/admin/desk/{trip_id}/take", headers=admin)
    assert held.json()["status"] == "handling"
    waiting = await client.post(f"/api/trips/{trip_id}/messages", headers=guest, json={"message_type": "TEXT", "content": "hello"})
    assert "person has this chat" in waiting.json()["assistant_message"]["content"]
    said = await client.post(f"/api/admin/desk/{trip_id}/say", headers=admin, json={"text": "I can help with the route."})
    assert said.json()["payload"]["speaker"] == "person"
    released = await client.post(f"/api/admin/desk/{trip_id}/release", headers=admin)
    assert released.json()["status"] == "auto"

    report = await client.get("/api/admin/eval", headers=admin)
    assert report.status_code == 200 and report.json()["passed"] == report.json()["total"]
    missed = await client.get("/api/admin/errors", headers=admin)
    assert missed.status_code == 200
