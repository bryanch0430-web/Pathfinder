"""A new plan searches for a hotel whenever its dates make a night: a start with a number of days counts as much as an end."""

from __future__ import annotations

from datetime import date

from app.application.planning import agents_for_new_plan
from app.domain.models import AgentName, TripContext

BASE = {AgentName.ATTRACTION, AgentName.WEATHER}


def test_a_start_date_and_a_number_of_days_with_no_end_date_searches_for_a_hotel() -> (
    None
):
    context = TripContext(
        session_id="s", destination="Kyoto", start_date=date(2027, 3, 8), days=4
    )

    assert context.end_date is None
    assert agents_for_new_plan(context) == BASE | {AgentName.HOTEL}


def test_a_start_and_an_end_date_still_searches_for_a_hotel() -> None:
    context = TripContext(
        session_id="s",
        destination="Kyoto",
        start_date=date(2027, 3, 8),
        end_date=date(2027, 3, 11),
    )

    assert agents_for_new_plan(context) == BASE | {AgentName.HOTEL}


def test_no_dates_is_as_before() -> None:
    assert agents_for_new_plan(TripContext(session_id="s", destination="Kyoto")) == BASE
    assert (
        agents_for_new_plan(TripContext(session_id="s", destination="Kyoto", days=4))
        == BASE
    )


def test_a_one_day_trip_has_no_night_whichever_way_it_is_given() -> None:
    by_days = TripContext(
        session_id="s", destination="Kyoto", start_date=date(2027, 3, 8), days=1
    )
    by_end = TripContext(
        session_id="s",
        destination="Kyoto",
        start_date=date(2027, 3, 8),
        end_date=date(2027, 3, 8),
    )

    assert agents_for_new_plan(by_days) == BASE == agents_for_new_plan(by_end)


def test_an_origin_still_adds_the_flight_search_and_no_origin_still_does_not() -> None:
    days = TripContext(
        session_id="s", destination="Kyoto", start_date=date(2027, 3, 8), days=4
    )

    assert AgentName.TICKET not in agents_for_new_plan(days)
    assert AgentName.TICKET in agents_for_new_plan(
        days.model_copy(update={"origin": "Hong Kong"})
    )


def test_dates_that_do_not_make_a_range_search_for_no_hotel_and_do_not_raise() -> None:
    backwards = TripContext(
        session_id="s",
        destination="Kyoto",
        start_date=date(2027, 3, 8),
        end_date=date(2027, 3, 1),
    )
    too_long = TripContext(
        session_id="s", destination="Kyoto", start_date=date(2027, 3, 8), days=60
    ).model_copy(update={"end_date": date(2027, 9, 8)})

    assert agents_for_new_plan(backwards) == BASE
    assert agents_for_new_plan(too_long) == BASE
