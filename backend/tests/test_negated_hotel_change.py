"""A hotel change the traveller refuses is not made: "move the trip a week later, don't change the hotel".

`keep the hotel` was read as a keep, but a change word with a negation next to it ("don't change", "do not swap",
"never switch", "no need to change") still counted as a request for another hotel, so the stay was swapped.
"""

from __future__ import annotations

import pytest

from app.application.modification import _asks_for_another_hotel
from app.application.router import affected_parts_for
from app.config import Settings
from tests.test_modify_dates import WEEK_LATER, _edit

REFUSED = [
    "move the trip a week later, don't change the hotel",
    "shift the trip by two days, no need to change the hotel",
    "move the trip a week later but do not swap the hotel",
    "make it 5 days and don't replace the room",
    "change the dates but never switch my hotel",
    "please don't swap the hotel for another one, just move the trip a week later",
    "move the trip a week later without changing the hotel",
]


@pytest.mark.parametrize("message", REFUSED)
def test_a_hotel_change_the_traveller_refuses_is_not_asked_for(message: str) -> None:
    assert _asks_for_another_hotel(message, affected_parts_for(message)) is False


@pytest.mark.parametrize("message", REFUSED[:5])
def test_a_refused_hotel_change_leaves_the_hotel_while_the_trip_moves(settings: Settings, message: str) -> None:
    plan, updated, diff, _ = _edit(settings, message)

    assert updated.hotels[0].name == plan.hotels[0].name, message
    assert not any(change["kind"] == "hotel" for change in diff), message
    if "5 days" not in message:
        assert (updated.dates.start, updated.dates.end) == WEEK_LATER, message


@pytest.mark.parametrize(
    "message",
    [
        "swap the hotel and move the trip a week later",
        "move the trip a week later and don't forget to switch the hotel",
        "I don't like the hotel, swap it for another hotel",
        "change my hotel",
    ],
)
def test_a_hotel_change_that_is_asked_for_is_still_made(message: str) -> None:
    assert _asks_for_another_hotel(message, affected_parts_for(message)) is True
