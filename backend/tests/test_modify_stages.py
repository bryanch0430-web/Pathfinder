"""The modify path's stages, tested as pure functions (robustness step 2).

``modification.py`` calls these in order; each test here pins one stage without a store, a model
or a toolbox, so a change to the order or the rules shows up in one small test.
"""

from __future__ import annotations

import random
from datetime import date

from app.application.modify_stages import (
    agents_needed_for_edit,
    apply_date_or_budget_change,
    apply_weather_results,
    clean_parts,
    gaps_for,
    merge_agent_results,
    preserve_locked_and_confirmed,
    select_agents_for_parts,
)
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    DataGap,
    DateRange,
    FailureKind,
    TicketOption,
    TripPlan,
    WeatherDay,
)
from scripts.seed_platform_demo import DESTINATIONS, _plan

SESSION = "sess_stages"


def _plan_fixture() -> TripPlan:
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    plan = _plan(random.Random(1), place, 1)
    return plan.model_copy(update={"session_id": SESSION, "version": 1})


def _agent(name: AgentName, failures: list[DataGap] | None = None, payload: dict | None = None) -> AgentResult:
    return AgentResult(
        agent=name,
        status=AgentStatus.OK if payload else AgentStatus.UNAVAILABLE,
        payload=payload or {},
        failures=failures or [],
    )


def test_only_the_parts_a_section_names_are_selected() -> None:
    assert select_agents_for_parts(["hotel"], dates_move=False) == {AgentName.HOTEL}
    assert select_agents_for_parts(["attraction"], dates_move=False) == {AgentName.ATTRACTION}
    assert select_agents_for_parts(["ticket"], dates_move=False) == {AgentName.TICKET}
    assert select_agents_for_parts(["weather"], dates_move=False) == {AgentName.WEATHER}


def test_a_day_part_is_a_scope_not_an_agent() -> None:
    assert clean_parts(["day_2", "hotel", "meal"]) == ["day_2", "hotel"]
    assert select_agents_for_parts(["day_2"], dates_move=False) == {AgentName.ATTRACTION}


def test_dates_only_rerun_the_calendar_agents_when_the_dates_move() -> None:
    assert select_agents_for_parts(["dates"], dates_move=False) == {AgentName.ATTRACTION}
    assert select_agents_for_parts(["dates"], dates_move=True) == {
        AgentName.WEATHER,
        AgentName.HOTEL,
        AgentName.TICKET,
    }


def test_a_moved_trip_keeps_the_attraction_agent_only_when_a_stop_was_named() -> None:
    assert select_agents_for_parts(["dates", "attraction"], dates_move=True) == {
        AgentName.WEATHER,
        AgentName.HOTEL,
        AgentName.TICKET,
        AgentName.ATTRACTION,
    }


def test_a_form_change_prices_the_stay_and_the_flights_but_searches_no_stop() -> None:
    names = select_agents_for_parts(["attraction"], dates_move=False, form_changes=["party"])
    assert names == {AgentName.HOTEL, AgentName.TICKET}


def test_an_empty_part_list_still_means_the_attraction_agent() -> None:
    assert select_agents_for_parts([], dates_move=False) == {AgentName.ATTRACTION}
    # A form message that prices nothing (only the dates changed) runs no agent at all.
    assert select_agents_for_parts([], dates_move=False, form_changes=["dates"]) == set()


def test_a_pace_rule_needs_no_agent_and_a_remove_needs_no_places() -> None:
    names = {AgentName.ATTRACTION, AgentName.HOTEL}
    assert agents_needed_for_edit(names, shape_requested=True) == set()
    assert agents_needed_for_edit(names, remove_requested=True, add_requested=False) == {AgentName.HOTEL}
    assert agents_needed_for_edit(names, remove_requested=True, add_requested=True) == names
    assert agents_needed_for_edit(names, move_requested=True) == set()


def test_a_merge_clears_the_refetched_section_and_keeps_the_other_gaps() -> None:
    plan = _plan_fixture()
    plan.data_quality.gaps = [
        DataGap(section="weather", kind=FailureKind.NO_DATA, message="old weather gap"),
        DataGap(section="tickets", kind=FailureKind.NO_DATA, message="old ticket gap"),
    ]

    merged = merge_agent_results(
        plan,
        {"weather": _agent(AgentName.WEATHER, failures=[_gap("weather", "still no forecast")])},
        refreshed_sections={"weather"},
    )

    messages = [gap.message for gap in merged.data_quality.gaps]
    assert "old weather gap" not in messages
    assert "old ticket gap" in messages, "a section that did not run keeps its own gap"
    assert "still no forecast" in messages


def test_a_failure_is_added_once() -> None:
    plan = _plan_fixture()
    failure = _gap("attractions", "no places")
    merged = merge_agent_results(
        plan, {"attraction": _agent(AgentName.ATTRACTION, failures=[failure])}, refreshed_sections={"attractions"}
    )
    merged = merge_agent_results(
        merged, {"attraction": _agent(AgentName.ATTRACTION, failures=[failure])}, refreshed_sections=set()
    )

    assert [gap.message for gap in merged.data_quality.gaps].count("no places") == 1


def _gap(section: str, message: str) -> DataGap:
    return DataGap(section=section, kind=FailureKind.NO_DATA, message=message)


def test_weather_results_land_on_their_own_days_only() -> None:
    plan = _plan_fixture()
    first = plan.days[0].date
    payload = {
        "weather": _agent(
            AgentName.WEATHER,
            payload={"days": [{"date": first.isoformat(), "status": "ok", "summary": "Clear", "high_c": 30.0}]},
        )
    }

    updated = apply_weather_results(plan, payload)

    assert updated.days[0].weather is not None and updated.days[0].weather.summary == "Clear"
    assert updated.days[1].weather is None


def test_date_range_shift_moves_the_days_the_stay_and_clears_the_old_forecast() -> None:
    plan = _plan_fixture()
    plan.days[0].weather = WeatherDay(date=plan.days[0].date, summary="Sunny", high_c=30.0)
    plan.tickets = [
        TicketOption(
            mode="flight",
            name="Old flight",
            origin="HKG",
            destination="KIX",
            depart_at=plan.dates.start,
        )
    ]
    new_range = DateRange(start=date(2026, 9, 1), end=date(2026, 9, 3))

    updated = apply_date_or_budget_change(plan, new_range=new_range, today=date(2026, 7, 1))

    assert updated.dates == new_range
    assert [day.date for day in updated.days] == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    assert updated.days[0].weather is None, "a forecast for the old date is not the new date's"
    assert updated.first_stay.check_in == new_range.start
    assert updated.first_stay.check_out == new_range.end
    assert updated.tickets == [], "a flight for the old dates no longer fits"


def test_no_range_leaves_the_plan_alone() -> None:
    plan = _plan_fixture()
    updated = apply_date_or_budget_change(plan, new_range=None)

    assert updated.dates == plan.dates
    assert [day.date for day in updated.days] == [day.date for day in plan.days]


def test_a_locked_stop_comes_back_into_a_rebuilt_plan() -> None:
    plan = _plan_fixture()
    kept = plan.days[0].items[0]
    kept.locked = True
    rebuilt = plan.model_copy(deep=True)
    for day in rebuilt.days:
        day.items = [item for item in day.items if item.id != kept.id]

    notes = preserve_locked_and_confirmed(plan, rebuilt)

    back = [item for day in rebuilt.days for item in day.items if item.name == kept.name]
    assert back, "the locked stop was put back"
    assert any("locked stop" in note for note in notes)


def test_gaps_for_returns_each_failure_once() -> None:
    failure = _gap("tickets", "no fare")
    found = gaps_for(
        {
            "ticket": _agent(AgentName.TICKET, failures=[failure]),
            "hotel": _agent(AgentName.HOTEL, failures=[failure]),
        }
    )
    assert found == [failure]
