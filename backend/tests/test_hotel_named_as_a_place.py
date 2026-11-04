"""A stay named as a place is not the stay to swap: "add a cafe near Kyoto Garden Inn" adds the cafe.

The name of a stay in a sentence made the stay the edit's target whatever the sentence asked, so a stay the traveller
had accepted (confirmed and locked) was swapped for another by "add a cafe near Kyoto Garden Inn", "Kyoto Garden Inn is
fine, add a temple on day 2" or "I like Kyoto Garden Inn, remove Gion", and the rest of the sentence was never done.
The stay is the target only when a change is aimed at it ("swap Kyoto Garden Inn", "Kyoto Garden Inn is too noisy, swap
it") or when it is the stop selected on the page.
"""

from __future__ import annotations

import pytest

from app.application.modification import _stay_as_word
from app.config import Settings
from tests.test_hotel_swap import _swap
from tests.test_modify_dates import Trip, accept_the_garden_inn, plan_event, reply_of, run_trip

NAME = "Kyoto Garden Inn"

AS_A_PLACE = [
    "add a cafe near Kyoto Garden Inn",
    "Kyoto Garden Inn is fine, add a temple on day 2",
    "move Gion closer to Kyoto Garden Inn",
    "I like Kyoto Garden Inn, remove Gion",
    "thanks for Kyoto Garden Inn, now add ramen",
    "add another temple near Kyoto Garden Inn",
    "remove Gion, Kyoto Garden Inn is perfect",
]


@pytest.mark.parametrize("message", AS_A_PLACE)
def test_a_stay_the_sentence_only_names_as_a_place_is_left_as_it_is(settings: Settings, message: str) -> None:
    async def scenario(trip: Trip) -> None:
        events = await trip.turn(message)

        assert not [event for event in events if event.event == "error"], message
        latest = trip.store.get_plan(trip.session_id)
        stay = latest.hotels[0]
        assert (stay.name, stay.confirmed, stay.locked) == (NAME, True, True), reply_of(events)
        assert not [change for change in plan_event(events)["diff"] if change["kind"] == "hotel"], reply_of(events)
        assert "Hotel changed" not in reply_of(events)

    run_trip(settings, scenario, prepare=accept_the_garden_inn)


def test_the_rest_of_a_sentence_that_names_a_stay_as_a_place_is_done(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        events = await trip.turn("I like Kyoto Garden Inn, remove Gion")

        latest = trip.store.get_plan(trip.session_id)
        assert "Gion" in [item.name for day in trip.plan.days for item in day.items]
        assert "Gion" not in [item.name for day in latest.days for item in day.items], reply_of(events)
        assert latest.hotels[0].name == NAME

    run_trip(settings, scenario, prepare=accept_the_garden_inn)


def test_the_words_of_a_stays_name_are_not_wishes_for_the_stops_added_near_it(settings: Settings) -> None:
    """"Garden" in Kyoto Garden Inn is not a garden to add."""

    async def scenario(trip: Trip) -> None:
        events = await trip.turn("add a cafe near Kyoto Garden Inn")

        latest = trip.store.get_plan(trip.session_id)
        gaps = [gap.message for gap in latest.data_quality.gaps]
        assert not [text for text in gaps if "garden" in text.lower()], reply_of(events)
        assert "garden" not in reply_of(events).lower()

    run_trip(settings, scenario, prepare=accept_the_garden_inn)


AIMED_AT_THE_STAY = [
    "swap Kyoto Garden Inn for another hotel",
    "change Kyoto Garden Inn",
    "replace Kyoto Garden Inn",
    "Kyoto Garden Inn is too noisy, swap it",
    "I do not want to keep Kyoto Garden Inn, swap it",
    "upgrade Kyoto Garden Inn",
    "change Kyoto Garden Inn to a different hotel",
    "switch from Kyoto Garden Inn to another hotel",
]


@pytest.mark.parametrize("message", AIMED_AT_THE_STAY)
def test_a_stay_the_sentence_asks_to_change_is_still_swapped(settings: Settings, message: str) -> None:
    async def scenario(trip: Trip) -> None:
        events = await trip.turn(message)

        latest = trip.store.get_plan(trip.session_id)
        assert latest.hotels[0].name != NAME, reply_of(events)
        assert any(change["kind"] == "hotel" for change in plan_event(events)["diff"]), reply_of(events)

    run_trip(settings, scenario, prepare=accept_the_garden_inn)


REFUSED = ["don't change Kyoto Garden Inn", "no need to swap Kyoto Garden Inn", "never replace Kyoto Garden Inn"]


@pytest.mark.parametrize("message", REFUSED)
def test_a_change_of_the_named_stay_that_the_sentence_refuses_is_not_made(settings: Settings, message: str) -> None:
    async def scenario(trip: Trip) -> None:
        await trip.turn(message)

        assert trip.store.get_plan(trip.session_id).hotels[0].name == NAME

    run_trip(settings, scenario, prepare=accept_the_garden_inn)


def test_the_selected_stay_is_the_target_whatever_the_sentence_names(settings: Settings) -> None:
    """The stay selected on the page is the one the sentence is about ("swap it", "this")."""
    before, after, diff = _swap(settings, "swap this hotel for another one", select=True)

    assert [hotel.name for hotel in after.hotels] != [hotel.name for hotel in before.hotels]
    assert any(change["kind"] == "hotel" for change in diff)


def test_a_stop_selected_on_the_page_is_what_it_and_this_mean_even_when_a_stay_is_named(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        stop = trip.plan.days[0].items[0]

        events = await trip.turn("swap this for something near Kyoto Garden Inn", scope_item_id=stop.id)

        latest = trip.store.get_plan(trip.session_id)
        assert latest.hotels[0].name == NAME, reply_of(events)
        assert not [change for change in plan_event(events)["diff"] if change["kind"] == "hotel"], reply_of(events)

    run_trip(settings, scenario, prepare=accept_the_garden_inn)


def test_a_stays_name_is_read_as_the_word_hotel() -> None:
    assert _stay_as_word("add a cafe near Kyoto Garden Inn", NAME) == "add a cafe near the hotel"
