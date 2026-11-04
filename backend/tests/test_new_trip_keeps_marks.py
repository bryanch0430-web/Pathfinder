"""A new trip built over a saved plan carries the traveller's locked and confirmed rows when it is for the same place.

Before, "plan a new trip to Kyoto" on a Kyoto trip with an accepted hotel and a locked stop started a plan that had
neither: the hotel was the planner's own pick and the locked stop was gone, with no word about it. A trip to another
place has nothing to carry (a Kyoto temple is not a stop of a Tokyo trip).
"""

from __future__ import annotations

from app.config import Settings
from app.domain.models import TripPlan
from tests.test_modify_dates import Trip, accept_the_garden_inn, run_trip


def _lock(held: dict[str, str]):
    def prepare(plan: TripPlan) -> None:
        accept_the_garden_inn(plan)
        stop = plan.days[1].items[0]
        stop.locked = True
        held["name"] = stop.name

    return prepare


def test_a_new_trip_to_the_same_place_keeps_the_accepted_hotel_and_the_locked_stop(settings: Settings) -> None:
    held: dict[str, str] = {}

    async def scenario(trip: Trip) -> None:
        events = await trip.turn("Plan a new trip to Kyoto")

        assert not [event for event in events if event.event == "error"]
        route = next(event.data for event in events if event.event == "route")
        assert route["intent"] == "plan"
        latest = trip.store.get_plan(trip.session_id)
        assert latest.trip_id != trip.plan.trip_id and latest.version == 1, "it is a new trip, not an edit"
        assert (latest.hotels[0].name, latest.hotels[0].confirmed, latest.hotels[0].locked) == (
            "Kyoto Garden Inn",
            True,
            True,
        )
        assert (latest.hotels[0].check_in, latest.hotels[0].check_out) == (latest.dates.start, latest.dates.end)
        locked = [item.name for day in latest.days for item in day.items if item.locked]
        assert locked == [held["name"]], "the locked stop is in the new trip once, and still locked"

    run_trip(settings, scenario, prepare=_lock(held))


def test_a_new_trip_to_another_place_carries_nothing_over(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        await trip.turn("Plan another trip to Tokyo")

        latest = trip.store.get_plan(trip.session_id)
        assert latest.trip_id != trip.plan.trip_id and latest.destination.name == "Tokyo"
        assert not [hotel for hotel in latest.hotels if hotel.confirmed or hotel.locked]
        assert not [item for day in latest.days for item in day.items if item.locked or item.confirmed]

    run_trip(settings, scenario, prepare=_lock({}))


def test_a_new_trip_over_a_plan_with_nothing_protected_is_a_plain_new_trip(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        await trip.turn("Plan a new trip to Kyoto")

        latest = trip.store.get_plan(trip.session_id)
        assert latest.trip_id != trip.plan.trip_id
        assert not [gap for gap in latest.data_quality.gaps if gap.message.startswith("Kept your")]
        assert not [hotel for hotel in latest.hotels if hotel.confirmed or hotel.locked]

    run_trip(settings, scenario)
