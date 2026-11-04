"""A request for another or a cheaper hotel together with a change of the trip's length is applied to the rebuilt plan.

Before, the rebuild took the planner's first candidate: "make it cheaper" picked a dearer hotel, "swap the hotel" kept
the same one, and the confirmed and locked marks of a hotel that stayed were lost. The same-length path already had the
rules (another hotel than the current one, a lower rate for "cheaper", otherwise the current one stays).
"""

from __future__ import annotations

from datetime import timedelta

from app.config import Settings
from tests.test_modify_dates import START, _edit, accept_the_garden_inn

FOUR_DAYS = (START, START + timedelta(days=3))
NO_CHEAPER = "No cheaper verified hotel was returned; the current hotel stays."


def _hotel_gaps(plan) -> list[str]:
    return [gap.message for gap in plan.data_quality.gaps if gap.section == "hotels"]


def test_make_it_cheaper_with_a_longer_trip_does_not_pick_a_dearer_hotel(settings: Settings) -> None:
    plan, updated, diff, _ = _edit(
        settings,
        "make it cheaper and change the dates",
        form=FOUR_DAYS,
        prepare=lambda p: accept_the_garden_inn(p, confirmed=False, locked=False),
    )

    assert len(updated.days) == 4
    stay = updated.hotels[0]
    assert (stay.name, stay.price_per_night.amount) == ("Kyoto Garden Inn", 90), "nothing was cheaper: it stays"
    assert not any(change["kind"] == "hotel" for change in diff)
    assert NO_CHEAPER in _hotel_gaps(updated)


def test_a_confirmed_and_locked_hotel_stays_when_nothing_cheaper_came_back(settings: Settings) -> None:
    _, updated, _, _ = _edit(
        settings, "make it cheaper and change the dates", form=FOUR_DAYS, prepare=accept_the_garden_inn
    )

    stay = updated.hotels[0]
    assert (stay.name, stay.confirmed, stay.locked) == ("Kyoto Garden Inn", True, True)
    assert (stay.check_in, stay.check_out) == FOUR_DAYS


def test_swap_the_hotel_with_a_longer_trip_swaps_it(settings: Settings) -> None:
    plan, updated, diff, _ = _edit(settings, "swap the hotel and change the dates", form=FOUR_DAYS)

    assert len(updated.days) == 4
    assert plan.hotels[0].name == "The Kyoto Grand"
    assert updated.hotels[0].name == "Kyoto Garden Inn"
    assert (updated.hotels[0].check_in, updated.hotels[0].check_out) == FOUR_DAYS
    assert any(change["kind"] == "hotel" and change["to"] == "Kyoto Garden Inn" for change in diff)


def test_a_hotel_the_traveller_asked_to_swap_is_swapped_even_when_it_was_accepted(settings: Settings) -> None:
    _, updated, _, _ = _edit(settings, "swap the hotel and change the dates", form=FOUR_DAYS, prepare=accept_the_garden_inn)

    assert updated.hotels[0].name == "The Kyoto Grand"
    assert (updated.hotels[0].confirmed, updated.hotels[0].locked) == (False, False)


def test_a_cheaper_hotel_with_a_longer_trip_is_taken_when_one_came_back(settings: Settings) -> None:
    plan, updated, _, _ = _edit(settings, "make it cheaper and change the dates", form=FOUR_DAYS)

    assert plan.hotels[0].name == "The Kyoto Grand"
    assert (updated.hotels[0].name, updated.hotels[0].price_per_night.amount) == ("Kyoto Garden Inn", 90)
    assert NO_CHEAPER not in _hotel_gaps(updated)
