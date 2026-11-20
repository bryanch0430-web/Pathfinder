"""System 1 router, the confidence gate, minimal edits, the tool allowlist, and the calendar."""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.routing.calendar_ics import document_to_ics
from app.routing.gate import apply_gate
from app.routing.jev import decision_from_jev
from app.routing.minimal import apply_minimal_change
from app.routing.models import Intent, RouteDecision, TripSnapshot
from app.routing.system1 import System1Router
from app.routing.toolbox import ToolRefused, Toolbox
from app.schemas.trip_document import DocBudget, DocDay, DocItem, DocLeg, TripDocument


def _plan(*days):
    return {"destination": "Kyoto", "days": [{"day": number, "base": "Kyoto", "items": [{"name": name}]} for number, name in days]}


def _snap(**kwargs):
    base = dict(destination="Kyoto", origin="Delhi", duration_days=3, has_plan=True)
    base.update(kwargs)
    return TripSnapshot(**base)


@pytest.mark.asyncio
async def test_a_question_about_the_plan_is_an_ask_with_no_model():
    router = System1Router(use_writer=False, jev=None)
    decision = await router.route(
        message="Is Okochi-Sanso worth it in the rain?",
        snapshot=_snap(), plan_summary=_plan((3, "Okochi-Sanso")),
    )
    assert decision.intent == Intent.ASK
    assert decision.source == "rules"
    assert decision.confidence >= 0.8
    assert decision.need_clarification is False


@pytest.mark.asyncio
async def test_add_on_day_2_is_a_minimal_modify_not_a_new_trip():
    router = System1Router(use_writer=False, jev=None)
    decision = await router.route(
        message="Add a temple on day 2",
        snapshot=_snap(), plan_summary=_plan((1, "Fushimi Inari"), (2, "Kinkaku-ji")),
    )
    assert decision.intent == Intent.MODIFY
    assert "day_2" in decision.affected_parts
    assert "attraction" in decision.affected_parts
    assert "dates" not in decision.affected_parts
    assert decision.public()["minimal"] is True


@pytest.mark.asyncio
async def test_yes_after_an_offer_modifies_and_a_bare_yes_does_not():
    router = System1Router(use_writer=False, jev=None)
    accepted = await router.route(
        message="yes", snapshot=_snap(), plan_summary=_plan((1, "Fushimi Inari")),
        last_reply="Want me to move Okochi-Sanso to day 1?",
    )
    assert accepted.intent == Intent.MODIFY and accepted.reason_code == "accept_offer"
    idle = await router.route(message="yes", snapshot=_snap(), plan_summary=_plan((1, "Fushimi Inari")))
    assert idle.intent == Intent.ASK and idle.reason_code == "no_pending_change"


@pytest.mark.asyncio
async def test_modify_without_a_plan_does_not_invent_one():
    router = System1Router(use_writer=False, jev=None)
    decision = await router.route(
        message="Swap the hotel", snapshot=_snap(has_plan=False, destination=None, origin=None, duration_days=None),
        plan_summary=None,
    )
    assert decision.intent == Intent.UNCLEAR
    assert decision.need_clarification is True


def test_the_gate_asks_when_jev_is_below_the_threshold():
    snapshot = _snap()
    raw = RouteDecision(
        intent=Intent.MODIFY, confidence=0.42, need_clarification=False, reason_code="jev_modify",
        affected_parts=["hotel"], related_to_plan=True, source="jev",
    )
    gated = apply_gate(raw, snapshot, _plan((1, "Fushimi Inari")), threshold=0.8, source="jev")
    assert gated.need_clarification is True
    assert gated.reason_code == "below_threshold_jev"
    assert gated.intent == Intent.MODIFY


def test_a_complete_context_and_a_low_score_still_plans_when_there_is_no_plan_yet():
    snapshot = TripSnapshot(destination="Kyoto", origin="Delhi", duration_days=4, has_plan=False)
    raw = RouteDecision(intent=Intent.UNCLEAR, confidence=0.4, need_clarification=True, reason_code="jev_unclear")
    gated = apply_gate(raw, snapshot, None, threshold=0.8, source="jev")
    assert gated.intent == Intent.PLAN
    assert gated.need_clarification is False
    assert gated.reason_code == "context_complete"


def test_jev_uses_the_probability_of_the_chosen_intent():
    decision = decision_from_jev(
        {"answers": {"intent": {"choice": "modify", "probabilities": {"modify": 0.91, "ask": 0.05, "plan": 0.03, "unclear": 0.01}},
                     "needs_detail": {"noul": 0.1}}},
        message="Change the hotel", snapshot=_snap(), plan_summary=_plan((1, "Fushimi Inari")),
    )
    assert decision is not None
    assert decision.intent == Intent.MODIFY
    assert decision.confidence == pytest.approx(0.91)
    assert decision.source == "jev"
    assert "hotel" in decision.affected_parts


def test_a_move_changes_the_destination_day_and_leaves_the_others():
    item = lambda name: {"name": name, "category": "sight", "time_of_day": None, "option": False}
    before = [
        {"day": 1, "base": "Kyoto", "items": [item("Fushimi Inari")]},
        {"day": 2, "base": "Kyoto", "items": [item("Kinkaku-ji")]},
        {"day": 3, "base": "Kyoto", "items": [item("Okochi-Sanso")]},
    ]
    after = [
        {"day": 1, "base": "Kyoto", "items": [item("Fushimi Inari"), item("Okochi-Sanso")]},
        {"day": 2, "base": "Osaka", "items": [item("Dotonbori")]},
        {"day": 3, "base": "Kyoto", "items": []},
    ]
    merged, graph, notes = apply_minimal_change(
        before, after, None, {"nodes": [{"name": "Kyoto", "nearby_places": [{"name": "Dotonbori"}, {"name": "Okochi-Sanso"}]}]},
        ["day_1", "attraction"], message="Move Okochi-Sanso to day 1",
    )
    assert notes == []
    assert [item["name"] for item in merged[0]["items"]] == ["Fushimi Inari", "Okochi-Sanso"]
    assert merged[1]["base"] == "Kyoto"
    assert [item["name"] for item in merged[1]["items"]] == ["Kinkaku-ji"]
    assert merged[2]["items"] == []
    assert [place["name"] for place in graph["nodes"][0]["nearby_places"]] == ["Okochi-Sanso"]


def test_a_hotel_edit_keeps_the_sights():
    item = lambda name, category="sight": {"name": name, "category": category, "time_of_day": None, "option": False}
    before = [{"day": 1, "base": "Kyoto", "items": [item("Fushimi Inari"), item("Old Inn", "stay")]}]
    after = [{"day": 1, "base": "Kyoto", "items": [item("Nijo Castle"), item("New Ryokan", "stay")]}]
    merged, _, _ = apply_minimal_change(before, after, None, None, ["hotel"], message="Change the hotel")
    names = [item["name"] for item in merged[0]["items"]]
    assert names == ["Fushimi Inari", "New Ryokan"]


@pytest.mark.asyncio
async def test_ask_cannot_search_hotels_and_clarify_cannot_search_at_all():
    ask = Toolbox("ask")
    with pytest.raises(ToolRefused):
        await ask.call("hotels.search", lambda: _never())
    clarify = Toolbox("clarify")
    with pytest.raises(ToolRefused):
        await clarify.call("web.search", lambda: _never())
    assert clarify.log[0]["status"] == "refused"


async def _never():
    raise AssertionError("refused tools must not run")


def test_calendar_includes_a_stay_and_a_leg_and_a_stable_uid():
    trip_id = uuid4()
    document = TripDocument(
        trip_id=trip_id, mode="one_shot", destination="Kyoto", origin="Delhi", duration_days=1,
        start_date=date(2026, 10, 12), end_date=date(2026, 10, 12),
        budget=DocBudget(currency="JPY"),
        days=[DocDay(day=1, date=date(2026, 10, 12), base="Kyoto", items=[
            DocItem(name="Fushimi Inari", category="sight", time_of_day="morning"),
            DocItem(name="Cross Hotel", category="stay"),
        ])],
        legs=[DocLeg(source="Delhi", target="Kyoto", mode="flight", duration="8h")],
    )
    ics = document_to_ics(document, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert ics is not None
    assert "SUMMARY:Fushimi Inari" in ics
    assert "SUMMARY:Stay: Cross Hotel" in ics
    assert "SUMMARY:Delhi → Kyoto" in ics
    assert "Estimated: no timetable was looked up." in ics
    assert f"UID:{trip_id}-d1-1@pathfinder" in ics
    assert document_to_ics(document.model_copy(update={"start_date": None})) is None
