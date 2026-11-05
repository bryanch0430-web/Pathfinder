"""A question or an edit never moves a trip that has a plan to the place it names.

The understanding step writes a place a sentence names into the trip form, and the page's Generate (`Re-plan with the
updated details.`) reads the form: after "is Nara worth a day trip?" the form said Nara and Generate planned a new trip to
Nara, with the accepted stay and every locked or confirmed stop gone and the earlier trip no longer served. The same
happened for a phrase an edit leaves behind ("Change the dinner on day 3 to sushi" stored the destination Sushi) and for
a plan made for the text typed in the form ("Kyoto Japan") once a question named the city. A message that plans a trip,
and a trip that has no plan yet, still take the place from the sentence.
"""

from __future__ import annotations

import asyncio

import pytest

from app.application.router import REPLAN_MESSAGE
from app.config import Settings
from app.domain.models import EvalCase, TripPlan
from app.eval_efficiency import build_rig, case_context
from app.eval_metering import RunLedger
from tests.test_modify_dates import WEEK_LATER, Trip, accept_the_garden_inn, run_trip


def _protect(plan: TripPlan) -> None:
    """The stay the traveller accepted, a locked stop and a confirmed one."""
    accept_the_garden_inn(plan)
    plan.days[1].items[0].locked = True
    plan.days[0].items[0].confirmed = True


def _marks(plan: TripPlan) -> list[tuple[int, str, bool, bool]]:
    return [
        (number, item.name, item.locked, item.confirmed)
        for number, day in enumerate(plan.days, 1)
        for item in day.items
        if item.locked or item.confirmed
    ]


def _route(events: list) -> dict:
    return next(event.data for event in events if event.event == "route")


def _stay(plan: TripPlan) -> list[tuple[str, bool, bool]]:
    return [(hotel.name, hotel.confirmed, hotel.locked) for hotel in plan.hotels]


#: sentences that name a place (a city, or a phrase the reader takes for one) and are not a request for a trip there
CHAT_ABOUT_ANOTHER_PLACE = [
    "is Nara worth a day trip?",
    "how far is Osaka from Kyoto?",
    "what is the weather like in Tokyo?",
    "Change the dinner on day 3 to sushi",
    "Swap the hotel for something closer to the station",
    "Forget the day trip, we will stay in the city",
    "Is Venice worth visiting in winter?",
    "Keep it under 1000 EUR in total",
]


@pytest.mark.parametrize("chat", CHAT_ABOUT_ANOTHER_PLACE)
def test_generate_after_a_chat_about_another_place_is_still_an_edit_of_the_trip(settings: Settings, chat: str) -> None:
    async def scenario(trip: Trip) -> None:
        stay, marks = _stay(trip.plan), _marks(trip.plan)
        assert stay == [("Kyoto Garden Inn", True, True)] and len(marks) == 2

        await trip.turn(chat)

        form = trip.store.get_context(trip.session_id)
        assert form.destination == "Kyoto", "the trip form still names the place the plan is for"
        before = trip.store.get_plan(trip.session_id)
        form.party_size = 4  # the traveller changes the party size in the form and presses Generate
        trip.store.save_context(form)

        events = await trip.turn(REPLAN_MESSAGE)

        assert _route(events)["reason_code"] != "replan_destination", _route(events)
        latest = trip.store.get_plan(trip.session_id)
        assert latest.trip_id == trip.plan.trip_id, "the earlier trip is still the trip"
        assert (latest.version, latest.party_size, latest.destination.name) == (before.version + 1, 4, "Kyoto")
        assert _stay(latest) == stay and _marks(latest) == marks

    run_trip(settings, scenario, prepare=_protect)


def test_a_question_that_names_the_city_in_other_words_does_not_make_generate_a_new_trip(settings: Settings) -> None:
    """The plan was made for the text typed in the form ("Kyoto Japan"); the form is not rewritten to the city a question
    names, and the two spellings are one place when the dates are moved and Generate is pressed."""

    def prepare(plan: TripPlan) -> None:
        _protect(plan)
        plan.destination.name = "Kyoto Japan"

    async def scenario(trip: Trip) -> None:
        stay, marks = _stay(trip.plan), _marks(trip.plan)
        form = trip.store.get_context(trip.session_id)
        form.destination = "Kyoto Japan"
        trip.store.save_context(form)

        await trip.turn("what is the weather like in Kyoto?")
        assert trip.store.get_context(trip.session_id).destination == "Kyoto Japan"
        trip.save_form(*WEEK_LATER)
        events = await trip.turn(REPLAN_MESSAGE)

        assert _route(events)["reason_code"] == "replan_dates", _route(events)
        latest = trip.store.get_plan(trip.session_id)
        assert latest.trip_id == trip.plan.trip_id and latest.version == 2
        assert (latest.dates.start, latest.dates.end) == WEEK_LATER
        assert _stay(latest) == stay and _marks(latest) == marks

    run_trip(settings, scenario, prepare=prepare)


@pytest.mark.parametrize("length", ["let's do 4 days", "only 2 days", "4 nights please"])
def test_a_plain_length_after_a_question_about_another_place_is_still_an_edit(settings: Settings, length: str) -> None:
    async def scenario(trip: Trip) -> None:
        await trip.turn("is Nara worth a visit?")
        events = await trip.turn(length)

        route = _route(events)
        assert (route["intent"], route["reason_code"]) == ("modify", "change_current_plan"), route
        latest = trip.store.get_plan(trip.session_id)
        assert latest.trip_id == trip.plan.trip_id and latest.destination.name == "Kyoto"
        assert _stay(latest) == _stay(trip.plan) and _marks(latest) == _marks(trip.plan)
        assert trip.store.get_context(trip.session_id).destination == "Kyoto"

    run_trip(settings, scenario, prepare=_protect)


def test_a_question_about_days_in_another_place_leaves_the_dates_of_the_form(settings: Settings) -> None:
    """The dates and the length the understanding step works out from a place that is then not planned are put back, or
    the next Generate would shorten the trip to the two days the question mentioned."""

    async def scenario(trip: Trip) -> None:
        before = trip.store.get_context(trip.session_id)
        kept = (before.destination, before.start_date, before.end_date, before.days)

        await trip.turn("is Nara worth a 2 day trip?")

        after = trip.store.get_context(trip.session_id)
        assert (after.destination, after.start_date, after.end_date, after.days) == kept
        events = await trip.turn(REPLAN_MESSAGE)
        assert _route(events)["reason_code"] != "replan_dates", _route(events)
        assert len(trip.store.get_plan(trip.session_id).days) == len(trip.plan.days)

    run_trip(settings, scenario, prepare=_protect)


def test_a_message_that_plans_a_trip_elsewhere_still_takes_the_place_from_the_sentence(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        events = await trip.turn("Plan a new trip to Tokyo")

        assert _route(events)["intent"] == "plan"
        assert trip.store.get_context(trip.session_id).destination == "Tokyo"
        assert trip.store.get_plan(trip.session_id).destination.name == "Tokyo"

    run_trip(settings, scenario, prepare=_protect)


class _FormEditedWhileRouting:
    """A router that lets the traveller save the trip form (another place in it) while the message is being routed."""

    def __init__(self, router, trip: Trip, destination: str) -> None:
        self.router, self.trip, self.destination = router, trip, destination

    async def route(self, **kwargs):
        form = self.trip.store.get_context(self.trip.session_id)
        form.destination = self.destination
        self.trip.store.save_context(form)
        return await self.router.route(**kwargs)


def test_a_form_the_traveller_changed_while_the_message_was_worked_on_is_not_put_back(settings: Settings) -> None:
    """The destination is put back only while the form still holds what the message left there."""

    async def scenario(trip: Trip) -> None:
        trip.rig.turns.router = _FormEditedWhileRouting(trip.rig.turns.router, trip, "Osaka")

        await trip.turn("is Nara worth a day trip?")

        assert trip.store.get_context(trip.session_id).destination == "Osaka"

    run_trip(settings, scenario)


def test_a_trip_that_has_no_plan_yet_takes_the_place_from_any_sentence(settings: Settings) -> None:
    async def main() -> None:
        ledger = RunLedger()
        try:
            rig = build_rig(settings, "Kyoto", ledger)
            session = ledger.store.create_session("guest-no-plan")
            form = case_context(EvalCase(id="e", dataset="design", input="", destination="Kyoto"), session.id)
            ledger.store.save_context(form)

            events = [event async for event in rig.turns.handle_turn(session_id=session.id, message="is Nara worth a day trip?")]

            assert not [event for event in events if event.event == "error"]
            assert ledger.store.get_context(session.id).destination == "Nara"
        finally:
            ledger.close()

    asyncio.run(main())
