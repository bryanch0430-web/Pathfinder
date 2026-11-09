"""A polite request to change something is a change, not a question.

Seen live on a Bangkok plan with the hotel row selected: "can change", "can change the hotel" and "help me change the
hotel" were each answered "This message reads as a question, not a change". The router sent them to the edit, the
writer read the politeness as a question (`is_question: true`), and the rule that turns an imperative with a selected
row into a change only knew the stops of the days: a selected hotel is not one of them.

- With the hotel selected, a polite change request swaps it for another verified hotel and the reply names both.
  With no other verified hotel the reply says so honestly, and the plan is unchanged.
- A message with no "?" that has a change verb is never refused as a question, whatever the writer says.
- "can change" with nothing selected and no part named asks one short question instead of refusing.
- Real questions stay questions: "can I change the hotel later?" is not an edit, nor is "why this hotel?".

Offline: the rig of tests/test_edit_conversation.py with scripted writer replies.
"""

from __future__ import annotations

import pytest

from app.application.modification import NO_CHEAPER_HOTEL, WHICH_EDIT_QUESTION
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import PlanInvalidError
from app.evaluation import SandboxHotels
from app.infrastructure.observability import NoopTracer
from tests.test_edit_conversation import DeepPlaces, Trip, event_of, ids, reply_of, run_trip, why_of, writer

REFUSAL = "reads as a question"
POLITE = ["can change", "can change the hotel", "help me change the hotel", "could you change this hotel", "pls change hotel"]
#: what the live writer made of "help me change the hotel" and "can change the hotel"
LIVE_DRAFTS = [
    {"description": "asking if the hotel can be changed", "affected_parts": ["hotel"], "is_question": True, "confidence": 0.7},
    {"description": "", "affected_parts": [], "is_question": True, "confidence": 0.9},
]


def said(events: list) -> str:
    """Everything the turn said: the reply, and the message of an error event (the canned refusal is sent as one)."""
    error = event_of(events, "error") or {}
    return reply_of(events) + str(error.get("message") or "")


class _OnlyTheCurrentStay(SandboxHotels):
    """The hotel source returns the plan's own stay and no other."""

    keep: str = ""

    async def search(self, **kwargs):
        rows = await super().search(**kwargs)
        return [row for row in rows if row.name == self.keep]


@pytest.mark.parametrize("draft", LIVE_DRAFTS, ids=["hotel-0.7", "nothing-0.9"])
@pytest.mark.parametrize("message", POLITE)
def test_a_polite_change_with_the_hotel_selected_swaps_the_hotel(settings: Settings, message: str, draft: dict) -> None:
    async def scenario(trip: Trip) -> None:
        writer(trip.llm, "modify.extract", draft)
        trip.llm.route = {"intent": "modify", "confidence": 0.95}
        old = trip.plan.hotels[0]

        events = await trip.turn(message, scope_item_id=old.id)
        stored = trip.store.get_plan(trip.session_id)
        reply = reply_of(events)

        assert REFUSAL not in said(events), said(events)
        assert event_of(events, "error") is None, events
        assert stored.version == trip.plan.version + 1, reply
        assert stored.hotels[0].name != old.name, reply
        assert ids(stored) == ids(trip.plan), "only the hotel changed"
        assert old.name in reply and stored.hotels[0].name in reply, reply

    run_trip(settings, scenario)


@pytest.mark.parametrize("message", POLITE)
def test_a_polite_change_with_no_other_hotel_says_so_honestly(settings: Settings, message: str) -> None:
    hotels = _OnlyTheCurrentStay("Hong Kong")

    async def scenario(trip: Trip) -> None:
        writer(trip.llm, "modify.extract", LIVE_DRAFTS[0])
        trip.llm.route = {"intent": "modify", "confidence": 0.95}
        old = trip.plan.hotels[0]
        hotels.keep = old.name

        events = await trip.turn(message, scope_item_id=old.id)
        stored = trip.store.get_plan(trip.session_id)
        reply = reply_of(events)

        assert REFUSAL not in said(events), said(events)
        assert event_of(events, "error") is None, said(events)
        assert reply == NO_CHEAPER_HOTEL, reply
        assert stored.hotels[0].name == old.name
        assert stored.version == trip.plan.version and ids(stored) == ids(trip.plan)

    run_trip(settings, scenario, hotels=hotels)


def test_can_change_with_nothing_selected_asks_one_question(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        writer(trip.llm, "modify.extract", LIVE_DRAFTS[1])
        trip.llm.route = {"intent": "modify", "confidence": 0.95}

        events = await trip.turn("can change")
        reply = reply_of(events)

        assert REFUSAL not in said(events), said(events)
        card = event_of(events, "clarify")
        assert card is not None and card["question"] == WHICH_EDIT_QUESTION, events
        assert reply == WHICH_EDIT_QUESTION
        assert trip.store.get_plan(trip.session_id).version == trip.plan.version

    run_trip(settings, scenario)


async def _modify(trip: Trip, message: str, *, scope_item_id: str | None, route_parts: list[str]):
    return await trip.rig.modify_path.run(
        plan=trip.plan,
        context=trip.store.get_context(trip.session_id),
        message=message,
        route_parts=route_parts,
        toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="polite"),
        tracer=NoopTracer(),
        emit=lambda *_: None,
        scope_item_id=scope_item_id,
    )


@pytest.mark.parametrize("message", POLITE[1:])
def test_a_change_verb_without_a_question_mark_is_never_refused_as_a_question(settings: Settings, message: str) -> None:
    """The general rule, at the edit itself: nothing selected, the writer says "question", the router names the hotel."""

    async def scenario(trip: Trip) -> None:
        writer(trip.llm, "modify.extract", LIVE_DRAFTS[0])
        old = trip.plan.hotels[0]

        updated, diff = await _modify(trip, message, scope_item_id=None, route_parts=["hotel"])

        assert updated.hotels[0].name != old.name, updated.change_summary
        assert any(change["kind"] == "hotel" for change in diff), diff

    run_trip(settings, scenario)


def test_a_real_question_about_changing_the_selected_hotel_stays_a_question(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        writer(trip.llm, "modify.extract", {"description": "", "affected_parts": [], "is_question": True, "confidence": 0.9})

        with pytest.raises(PlanInvalidError, match=REFUSAL):
            await _modify(trip, "can I change the hotel later?", scope_item_id=trip.plan.hotels[0].id, route_parts=["hotel"])

    run_trip(settings, scenario)


def test_a_real_question_turn_with_the_hotel_selected_leaves_the_plan_alone(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        writer(trip.llm, "modify.extract", {"description": "", "affected_parts": [], "is_question": True, "confidence": 0.9})
        old = trip.plan.hotels[0]

        events = await trip.turn("can I change the hotel later?", scope_item_id=old.id)
        stored = trip.store.get_plan(trip.session_id)

        assert stored.version == trip.plan.version and stored.hotels[0].name == old.name, reply_of(events)

    run_trip(settings, scenario)


def test_why_this_hotel_is_not_an_edit(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        writer(trip.llm, "modify.extract", {"description": "", "affected_parts": [], "is_question": True, "confidence": 0.9})
        old = trip.plan.hotels[0]

        with pytest.raises(PlanInvalidError, match=REFUSAL):
            await _modify(trip, "why this hotel?", scope_item_id=old.id, route_parts=["hotel"])
        events = await trip.turn("why this hotel?", scope_item_id=old.id)
        stored = trip.store.get_plan(trip.session_id)

        assert stored.version == trip.plan.version and stored.hotels[0].name == old.name, reply_of(events)

    run_trip(settings, scenario)


def test_help_me_change_this_with_a_stop_selected_is_still_the_stop_edit(settings: Settings) -> None:
    """The selected stop's own path, as "replace this with something else" takes it: only that stop may change (this
    rig has no verified alternative for it, so the reply says so), and the hotel is not touched."""

    async def scenario(trip: Trip) -> None:
        writer(trip.llm, "modify.extract", LIVE_DRAFTS[1])
        trip.llm.route = {"intent": "modify", "confidence": 0.95}
        selected = trip.stop_on(2)

        events = await trip.turn("help me change this", scope_item_id=selected.id)
        stored = trip.store.get_plan(trip.session_id)
        reply = reply_of(events)

        assert REFUSAL not in said(events), said(events)
        assert "A stop was selected, so only that stop could change." in why_of(events), why_of(events)
        assert f"No verified alternative for '{selected.name}'" in reply, reply
        assert stored.hotels[0].name == trip.plan.hotels[0].name, "the hotel is not touched"
        assert ids(stored) == ids(trip.plan)

    run_trip(settings, scenario, places=DeepPlaces())
