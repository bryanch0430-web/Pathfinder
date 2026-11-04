"""The reply says what happened to a flight or a hotel an edit took out, not "changed to a new option".

Before, a diff whose flight or hotel had no "to" read "Flight changed from X to a new option" or "Hotel changed from X
to a new hotel", while the plan held none. A rebuild whose flight search came back empty (no error) left no gap for the
flight it dropped, so nothing on the plan said the fare was gone.
"""

from __future__ import annotations

from typing import Any

from app.application.orchestrator import _describe_diff
from app.config import Settings
from tests.test_modify_dates import WEEK_LATER, accept_the_garden_inn, no_flights
from tests.test_modify_targets import _modify


def test_a_flight_dropped_by_a_date_move_is_said_to_be_removed(settings: Settings) -> None:
    plan, updated, diff = _modify(settings, "move the trip a week later", ["dates"], new_range=WEEK_LATER, break_registry=no_flights)

    text = _describe_diff(diff, updated)

    assert plan.tickets and not updated.tickets
    assert "new option" not in text
    assert f"The flight {plan.tickets[0].name} was removed: no flight for the new dates came back." in text


def test_a_hotel_dropped_by_a_one_day_trim_is_said_to_be_removed(settings: Settings) -> None:
    plan, updated, diff = _modify(settings, "make it 1 days", ["dates"], new_days=1, prepare=accept_the_garden_inn)

    text = _describe_diff(diff, updated)

    assert plan.hotels and not updated.hotels
    assert "a new hotel" not in text
    assert "The hotel Kyoto Garden Inn was removed: a one-day trip has no overnight stay." in text


def test_a_flight_or_hotel_that_appears_is_said_to_be_added() -> None:
    class Plan:
        days = [object(), object()]

    text = _describe_diff(
        [{"kind": "hotel", "from": "", "to": "Kyoto Grand"}, {"kind": "flight", "from": "", "to": "PF100"}], Plan()
    )

    assert "A hotel was added: Kyoto Grand." in text and "A flight was added: PF100." in text


def test_a_flight_or_hotel_that_was_replaced_is_still_said_to_have_changed() -> None:
    class Plan:
        days = [object(), object()]

    text = _describe_diff(
        [{"kind": "hotel", "from": "A", "to": "B"}, {"kind": "flight", "from": "PF1", "to": "PF2"}], Plan()
    )

    assert "Hotel changed from A to B." in text and "Flight changed from PF1 to PF2." in text


class _EmptyFlights:
    """A flight source that answers, with nothing (no error)."""

    name = "empty-flights"
    available = True

    async def search(self, **_: Any):
        return []


def test_a_rebuild_whose_flight_search_comes_back_empty_leaves_a_gap_for_the_flight_it_dropped(settings: Settings) -> None:
    plan, updated, _ = _modify(
        settings,
        "make it 4 days",
        ["dates"],
        new_days=4,
        break_registry=lambda registry: setattr(registry, "flights", [_EmptyFlights()]),
    )

    assert plan.tickets and not updated.tickets
    assert [gap.message for gap in updated.data_quality.gaps if gap.section == "tickets"] == [
        "The earlier flight no longer fits the new dates and no flight for them came back."
    ]


def test_a_rebuild_that_found_a_flight_leaves_no_such_gap(settings: Settings) -> None:
    _, updated, _ = _modify(settings, "make it 4 days", ["dates"], new_days=4)

    assert updated.tickets
    assert not [gap for gap in updated.data_quality.gaps if gap.section == "tickets"]
