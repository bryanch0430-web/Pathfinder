"""Which sentences keep the hotel: a complaint in front of the keep is not a refusal, a curly apostrophe is an apostrophe.

`_keeps_the_hotel` looked 24 characters back for a negation, so "I don't like the area, but keep the hotel" was read
as a refused keep and the hotel was swapped. A negation typed with the curly apostrophe ("don’t") was not recognised
at all, neither in a keep nor in a refused change.
"""

from __future__ import annotations

import pytest

from app.application.modification import _asks_for_another_hotel, _keeps_the_hotel
from app.application.router import affected_parts_for
from app.config import Settings
from tests.test_hotel_swap import _swap

KEPT_AFTER_A_COMPLAINT = [
    "I don't like the area, but keep the hotel",
    "I don't like the area but keep the hotel",
    "I don't like the area so keep the hotel",
    "I don’t like the area, keep the hotel",
    "not happy with the food. Keep the same room",
    "the noise isn't great but the hotel stays the same",
    "don't change the dates and keep the hotel",
    "don't forget to keep the hotel",
    "don’t forget to keep the hotel",
]


@pytest.mark.parametrize("message", KEPT_AFTER_A_COMPLAINT)
def test_a_complaint_in_front_of_a_keep_does_not_refuse_it(message: str) -> None:
    assert _keeps_the_hotel(message) is True


REFUSED_KEEPS = [
    "I don't want to keep the hotel",
    "I don’t want to keep the hotel",
    "I don't really want to keep the hotel",
    "please don’t keep the same hotel",
    "I do not want the same room",
    "never keep the hotel",
    "I want a different hotel, not the same hotel",
    "I dont want to keep the hotel",
]


@pytest.mark.parametrize("message", REFUSED_KEEPS)
def test_a_negation_in_the_keeps_own_clause_still_refuses_it(message: str) -> None:
    assert _keeps_the_hotel(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "move the trip a week later, don’t change the hotel",
        "make it 5 days and don’t replace the room",
        "I’d rather not change the hotel",
        "there’s no need to switch the hotel",
    ],
)
def test_a_refused_change_written_with_a_curly_apostrophe_is_not_asked_for(message: str) -> None:
    assert _asks_for_another_hotel(message, affected_parts_for(message)) is False


@pytest.mark.parametrize(
    "message",
    ["I don’t like this hotel so swap it", "move the trip a week later and don’t forget to switch the hotel"],
)
def test_a_complaint_or_a_dont_forget_with_a_curly_apostrophe_still_asks_for_the_change(message: str) -> None:
    assert _asks_for_another_hotel(message, affected_parts_for(message)) is True


@pytest.mark.parametrize(
    ("message", "select"),
    [
        ("I don't like the area, but keep the hotel", True),
        ("I don't like the area, but keep {name}", False),
    ],
    ids=["chip", "named"],
)
def test_a_keep_after_a_complaint_leaves_the_selected_or_named_hotel(
    settings: Settings, message: str, select: bool
) -> None:
    before, after, diff = _swap(settings, message, select=select)

    assert [hotel.name for hotel in after.hotels] == [hotel.name for hotel in before.hotels], after.change_summary
    assert not any(change["kind"] == "hotel" for change in diff), diff


def test_a_refused_keep_with_a_curly_apostrophe_still_swaps_the_named_hotel(settings: Settings) -> None:
    before, after, _ = _swap(settings, "I don’t want to keep {name}, swap it", select=False)

    assert [hotel.name for hotel in after.hotels] != [hotel.name for hotel in before.hotels], after.change_summary
