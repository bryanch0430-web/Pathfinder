"""The place a plan was made for and the place the trip form names are one destination when one spells it longer.

The plan keeps the destination as it was typed ("Kyoto Japan"); a question that names the city makes the understanding
step write the canonical name ("Kyoto") into the form. The page's Generate compared only the part before the first
comma, so "Kyoto Japan" and "Kyoto" were two places: moving the dates in the form and pressing Generate planned a new
trip to Kyoto, and the accepted stay and every locked or confirmed stop were gone. "Paris, France" and "Paris, Texas"
share a first part and are two places.
"""

from __future__ import annotations

import pytest

from app.application.router import REPLAN_MESSAGE
from app.config import Settings
from app.domain.models import TripPlan
from tests.test_modify_dates import WEEK_LATER, Trip, accept_the_garden_inn, run_trip


def _protect(typed: str):
    def prepare(plan: TripPlan) -> None:
        accept_the_garden_inn(plan)
        plan.days[1].items[0].locked = True
        plan.days[0].items[0].confirmed = True
        plan.destination.name = typed

    return prepare


def _marks(plan: TripPlan) -> list[tuple[int, str, bool, bool]]:
    return [
        (number, item.name, item.locked, item.confirmed)
        for number, day in enumerate(plan.days, 1)
        for item in day.items
        if item.locked or item.confirmed
    ]


@pytest.mark.parametrize("typed", ["Kyoto Japan", "Kyoto, Japan", "kyoto"])
def test_moving_the_dates_after_a_question_that_names_the_city_is_an_edit_of_the_same_trip(
    settings: Settings, typed: str
) -> None:
    async def scenario(trip: Trip) -> None:
        stay = [(hotel.name, hotel.confirmed, hotel.locked) for hotel in trip.plan.hotels]
        marks = _marks(trip.plan)
        form = trip.store.get_context(trip.session_id)
        form.destination = typed
        trip.store.save_context(form)

        await trip.turn("what is the weather like in Kyoto?")  # the understanding step spells the city its own way
        trip.save_form(*WEEK_LATER)
        events = await trip.turn(REPLAN_MESSAGE)

        route = next(event.data for event in events if event.event == "route")
        assert route["reason_code"] == "replan_dates", route
        latest = trip.store.get_plan(trip.session_id)
        assert latest.trip_id == trip.plan.trip_id and latest.version == 2
        assert (latest.dates.start, latest.dates.end) == WEEK_LATER
        assert [(hotel.name, hotel.confirmed, hotel.locked) for hotel in latest.hotels] == stay
        assert _marks(latest) == marks

    run_trip(settings, scenario, prepare=_protect(typed))


def test_the_other_region_of_a_place_with_the_same_first_part_is_a_new_trip(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        form = trip.store.get_context(trip.session_id)
        form.destination = "Paris, Texas"
        trip.store.save_context(form)

        events = await trip.turn(REPLAN_MESSAGE)

        route = next(event.data for event in events if event.event == "route")
        assert (route["intent"], route["reason_code"]) == ("plan", "replan_destination"), route
        assert trip.store.get_plan(trip.session_id).trip_id != trip.plan.trip_id

    run_trip(settings, scenario, prepare=_protect("Paris, France"))
