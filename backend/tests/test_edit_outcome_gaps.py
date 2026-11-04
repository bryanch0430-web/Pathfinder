"""What an edit could not do is told in the reply; it is not kept as a data gap of the plan.

"Make it cheaper" when no cheaper hotel came back used to be stored as a gap about the hotels, and the
page then showed a data gap for a plan whose data is complete, on every later visit.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.application.validation import tidy_data_gaps
from app.config import Settings
from app.domain.models import DataGap, DateRange, DayPlan, Destination, EvalCase, FailureKind, TripPlan
from app.eval_efficiency import accepted_plan, case_context
from app.interface.api import create_app
from tests.test_modify_dates import WEEK_LATER, plan_event, reply_of, run_trip

NO_CHEAPER = "No cheaper verified hotel was returned; the current hotel stays."
SWAP = "Swap the hotel for the other option."


def _hotel_gaps(plan) -> list[str]:
    if isinstance(plan, dict):
        return [gap["message"] for gap in plan["data_quality"]["gaps"] if gap["section"] == "hotels"]
    return [gap.message for gap in plan.data_quality.gaps if gap.section == "hotels"]


def test_a_request_for_a_cheaper_hotel_that_finds_none_is_answered_and_not_kept_as_a_data_gap(settings: Settings) -> None:
    async def scenario(trip) -> None:
        await trip.turn(SWAP)

        events = await trip.turn("Make it cheaper")
        stored = trip.store.get_plan(trip.session_id)
        again = trip.store.get_plan(trip.session_id)

        assert reply_of(events) == NO_CHEAPER
        assert _hotel_gaps(stored) == []
        assert _hotel_gaps(plan_event(events)["plan"]) == []
        assert _hotel_gaps(again) == []

    run_trip(settings, scenario)


def test_a_cheaper_hotel_request_that_finds_none_while_the_trip_moves_says_so_in_the_reply(settings: Settings) -> None:
    async def scenario(trip) -> None:
        await trip.turn(SWAP)
        trip.save_form(*WEEK_LATER)

        events = await trip.turn("make it cheaper and move it to May")
        stored = trip.store.get_plan(trip.session_id)

        reply = reply_of(events)
        assert reply.startswith("Updated to version 3. The trip dates changed from ")
        assert reply.endswith(NO_CHEAPER)
        assert _hotel_gaps(stored) == []
        assert _hotel_gaps(plan_event(events)["plan"]) == []

    run_trip(settings, scenario)


def test_an_edit_that_changed_nothing_leaves_the_data_gaps_the_plan_already_had(settings: Settings) -> None:
    async def scenario(trip) -> None:
        await trip.turn(SWAP)
        kept = DataGap(section="meals", kind=FailureKind.NO_DATA, message="No vegetarian restaurant came back.")
        before = trip.store.get_plan(trip.session_id)
        before.data_quality.gaps = [kept]
        trip.store.save_plan(before, "Swap", replace=True)

        await trip.turn("Make it cheaper")

        assert [gap.message for gap in trip.store.get_plan(trip.session_id).data_quality.gaps] == [kept.message]

    run_trip(settings, scenario)


def test_the_outcome_of_an_edit_is_dropped_from_a_plan_that_already_carries_it() -> None:
    from app.domain.models import DayPlan, Destination, TripPlan, DateRange
    from datetime import date

    plan = TripPlan(
        session_id="s",
        destination=Destination(name="Kyoto"),
        dates=DateRange(start=date(2030, 1, 1), end=date(2030, 1, 1)),
        days=[DayPlan(date=date(2030, 1, 1), title="Day")],
    )
    plan.data_quality.gaps = [
        DataGap(section="hotels", kind=FailureKind.NO_DATA, message=NO_CHEAPER),
        DataGap(section="hotels", kind=FailureKind.NO_DATA, message="The hotel's price for the new dates could not be refreshed."),
    ]

    kept_on_write = [gap.message for gap in tidy_data_gaps(plan.model_copy(deep=True)).data_quality.gaps]
    kept_on_read = [gap.message for gap in tidy_data_gaps(plan.model_copy(deep=True), drop_edit_gaps=True).data_quality.gaps]

    assert kept_on_write == [NO_CHEAPER, "The hotel's price for the new dates could not be refreshed."]
    assert kept_on_read == ["The hotel's price for the new dates could not be refreshed."]


def test_a_trip_opened_with_the_outcome_stored_as_a_gap_shows_no_such_gap(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        guest = {"X-Client-Token": "guest-edit-outcome"}
        session_id = client.post("/api/journeys", json={"client_token": "guest-edit-outcome"}).json()["session"]["id"]
        context = case_context(EvalCase(id="o", dataset="design", input="", destination="Kyoto"), session_id)
        client.app.state.container.store.save_context(context)
        plan = asyncio.run(accepted_plan(settings, context))
        plan.data_quality.gaps.append(DataGap(section="hotels", kind=FailureKind.NO_DATA, message=NO_CHEAPER))
        client.app.state.container.store.save_plan(plan, "Swapped the hotel")

        opened = client.get(f"/api/journeys/{session_id}", headers=guest).json()["plan"]
        itinerary = client.get(f"/api/journeys/{session_id}/itinerary", headers=guest).json()["plan"]

    assert _hotel_gaps(opened) == [] and _hotel_gaps(itinerary) == []


# -- the other outcomes of an edit: a stop that was not found, not told apart, or already there -------------

#: (message, the stop selected before it, the route the router gives, how the reply begins)
OUTCOMES = [
    pytest.param(
        "Remove the museum on day 2",
        None,
        None,
        "No unlocked stop matched that removal, so the itinerary stayed as it was.",
        id="a removal that matched no stop",
    ),
    pytest.param(
        "Replace that one with something else",
        None,
        "modify",
        "I could not tell which stop you meant.",
        id="no stop that could be told apart",
    ),
    pytest.param(
        "Replace this with kiyomizu dera",
        "Gion",
        None,
        "Kiyomizu-dera is already in the plan on Day 1 at 09:00, so it cannot be added twice.",
        id="a stop that is already in the plan",
    ),
    pytest.param(
        "Replace this with an Italian restaurant",
        "Gion",
        None,
        "No verified italian restaurant came back, so Gion stayed as it was",
        id="no restaurant of the kind asked for",
    ),
    pytest.param(
        "Replace this with something else",
        "Gion",
        None,
        "No verified alternative for 'Gion' came back from the configured sources",
        id="no alternative for the selected stop",
    ),
]


def _stop_gaps(plan) -> list[str]:
    return [gap.message for gap in plan.data_quality.gaps if gap.section == "attractions"]


@pytest.mark.parametrize(("message", "selected", "route", "reply_begins"), OUTCOMES)
def test_an_edit_that_could_not_be_done_is_answered_and_not_kept_as_a_data_gap(
    settings: Settings, message: str, selected: str | None, route: str | None, reply_begins: str
) -> None:
    async def scenario(trip) -> None:
        stop = next((i.id for d in trip.plan.days for i in d.items if i.name == selected), None) if selected else None
        if route:
            trip.rig.llm.route = {"intent": route, "confidence": 0.95}

        events = await trip.turn(message, scope_item_id=stop)
        stored = trip.store.get_plan(trip.session_id)

        assert reply_of(events).startswith(reply_begins)
        assert stored.version == trip.plan.version, "nothing in the plan changed"
        assert _stop_gaps(stored) == []

        await trip.turn(SWAP)
        assert _stop_gaps(trip.store.get_plan(trip.session_id)) == [], "a later edit that works does not bring it back"

    run_trip(settings, scenario)


def test_the_gaps_such_edits_left_in_a_stored_plan_are_not_shown_but_the_plans_own_are() -> None:
    plan = TripPlan(
        session_id="s",
        destination=Destination(name="Kyoto"),
        dates=DateRange(start=date(2030, 1, 1), end=date(2030, 1, 1)),
        days=[DayPlan(date=date(2030, 1, 1), title="Day")],
    )
    outcomes = [
        "No unlocked stop matched that removal, so the itinerary stayed as it was.",
        "I could not tell which stop you meant. Name it or give its number in the day, or select it first; "
        "the itinerary stayed as it was.",
        "Gion is already in the plan on Day 1 at 09:00, so it cannot be added twice. Name a different replacement, "
        "or ask me to remove the existing one first.",
        "No verified italian restaurant came back, so Gion stayed as it was, so that stop stayed as it was.",
        "No verified alternative for 'Gion' came back from the configured sources, so that stop stayed as it was.",
    ]
    real = "Google Places timed out, so the list of attractions may be incomplete."
    plan.data_quality.gaps = [DataGap(section="attractions", kind=FailureKind.NO_DATA, message=m) for m in outcomes] + [
        DataGap(section="attractions", kind=FailureKind.TIMEOUT, message=real),
        DataGap(section="meals", kind=FailureKind.NO_DATA, message="No vegetarian restaurant came back from the place sources, so no meal stop is suggested."),
    ]

    shown = tidy_data_gaps(plan, drop_edit_gaps=True)

    assert [gap.message for gap in shown.data_quality.gaps] == [
        real,
        "No vegetarian restaurant came back from the place sources, so no meal stop is suggested.",
    ]
