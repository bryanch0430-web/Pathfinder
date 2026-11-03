"""What an edit could not do is told in the reply; it is not kept as a data gap of the plan.

"Make it cheaper" when no cheaper hotel came back used to be stored as a gap about the hotels, and the
page then showed a data gap for a plan whose data is complete, on every later visit.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.application.validation import tidy_data_gaps
from app.config import Settings
from app.domain.models import DataGap, EvalCase, FailureKind
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
