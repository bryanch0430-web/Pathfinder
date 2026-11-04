"""A request for other dates or another length is about the whole trip, whatever row is selected or named in it.

While the trip form holds the plan's own dates, chat cannot move the trip and says so. Before, a hotel or stop chip,
or a hotel or stop named in the sentence, turned the request into an edit of that row: "keep this hotel but move to
March" swapped the hotel and "make it 4 days, Senso-ji must stay" replaced Senso-ji.
"""

from __future__ import annotations

import pytest

from app.application.router import asks_for_trip_dates
from app.config import Settings
from tests.test_modify_dates import CHAT_DATES_REPLY, _edit

WHOLE_TRIP = [
    "shift the trip by a week",
    "change the dates",
    "make it 5 days",
    "move the trip to March",
    "keep this hotel but move to March",
    "keep the hotel but change the dates",
    "two days later",
]


@pytest.mark.parametrize("message", WHOLE_TRIP)
def test_a_date_request_with_the_hotel_selected_is_about_the_trip(settings: Settings, message: str) -> None:
    plan, updated, diff, _ = _edit(settings, message, form=None, scope_item_id="hotel_h1")

    assert updated.change_summary == CHAT_DATES_REPLY and not diff, (message, updated.change_summary)
    assert updated.hotels[0].name == plan.hotels[0].name


@pytest.mark.parametrize("message", WHOLE_TRIP)
def test_a_date_request_with_a_stop_selected_is_about_the_trip(settings: Settings, message: str) -> None:
    plan, updated, diff, _ = _edit(settings, message, form=None, scope_item_id="item_p1")

    assert updated.change_summary == CHAT_DATES_REPLY and not diff, (message, updated.change_summary)
    assert [(i.id, i.name) for d in updated.days for i in d.items] == [(i.id, i.name) for d in plan.days for i in d.items]


@pytest.mark.parametrize(
    "template",
    [
        "keep The Kyoto Grand, move the trip a week later",
        "I want The Kyoto Grand but shift the trip by a week",
        "make it 4 days, {stop} must stay",
        "move the trip a week later but keep {stop}",
    ],
)
def test_a_date_request_that_names_a_row_is_about_the_trip(settings: Settings, template: str) -> None:
    from tests.test_modify_targets import _build

    stop = _build(settings)[4].days[0].items[0].name
    message = template.format(stop=stop)

    plan, updated, diff, _ = _edit(settings, message, form=None)

    assert updated.change_summary == CHAT_DATES_REPLY and not diff, (message, updated.change_summary)
    assert updated.hotels[0].name == plan.hotels[0].name


def test_moving_a_row_is_still_an_edit_of_that_row(settings: Settings) -> None:
    """"it" and a named stop are the row to move: only a request that talks about the trip or its dates is not."""
    for message, scope in (
        ("push the temple back a day", None),
        ("move Gion a day later", None),
        ("move it a day later", "item_p1"),
        ("move this a day later", "item_p1"),
    ):
        _, updated, _, _ = _edit(settings, message, form=None, scope_item_id=scope)

        assert updated.change_summary != CHAT_DATES_REPLY, message


@pytest.mark.parametrize("message", WHOLE_TRIP[:-1] + ["end the trip on day 2", "only 2 days"])
def test_these_are_about_the_whole_trip(message: str) -> None:
    assert asks_for_trip_dates(message) is True


@pytest.mark.parametrize("message", ["move it a day later", "push the temple back a day", "move Gion a day later"])
def test_a_span_that_may_move_one_row_is_not_by_itself_about_the_trip(message: str) -> None:
    assert asks_for_trip_dates(message) is False
