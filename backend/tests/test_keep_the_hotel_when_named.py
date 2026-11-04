""""Keep the hotel" is a keep even when the hotel is the one selected or named.

A hotel the traveller points at (the stay selected, or its name in the sentence) was always taken as the one to
swap, whatever the sentence said about it: "keep this hotel" and "keep Kyoto Garden Inn" put another hotel in the plan.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from tests.test_hotel_swap import _swap


@pytest.mark.parametrize(
    ("message", "select"),
    [
        ("keep this hotel", True),
        ("keep the hotel", True),
        ("keep {name}", False),
        ("keep the hotel ({name})", False),
        ("I want to stick with {name}", False),
        ("{name} stays the same", False),
    ],
    ids=["chip-this", "chip-the", "named", "named-in-brackets", "stick-with", "stays-the-same"],
)
def test_a_hotel_the_traveller_keeps_is_not_swapped_because_it_is_selected_or_named(
    settings: Settings, message: str, select: bool
) -> None:
    before, after, diff = _swap(settings, message, select=select)

    assert [hotel.name for hotel in after.hotels] == [hotel.name for hotel in before.hotels], after.change_summary
    assert not any(change["kind"] == "hotel" for change in diff), diff


@pytest.mark.parametrize(
    ("message", "select"),
    [
        ("swap this hotel, keep the same area", True),
        ("keep the dates but swap {name} for another hotel", False),
        ("I do not want to keep {name}, swap it", False),
    ],
    ids=["swap-with-a-keep-clause", "keep-dates-swap-named", "refused-keep"],
)
def test_a_swap_that_mentions_something_else_to_keep_is_still_a_swap(
    settings: Settings, message: str, select: bool
) -> None:
    before, after, diff = _swap(settings, message, select=select)

    assert [hotel.name for hotel in after.hotels] != [hotel.name for hotel in before.hotels], after.change_summary
