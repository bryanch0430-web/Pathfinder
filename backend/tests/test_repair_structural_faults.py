"""A repair can fix a plan that holds one id twice, or a forecast under another day's date.

RA-A7: both are reported by `validate_plan` and both fields are put back from the original after a repair, so the
writer's correction was overwritten and the turn ended in `plan_invalid` however many times it was asked. The id is
now made distinct by code and the forecast goes to the day of its own date; nothing is invented, and every other fact
is still the original's.
"""

from __future__ import annotations

from app.application.validation import validate_plan
from app.config import Settings
from tests.test_repair_guard import _accepted, _payload, _plan, _refused

DUPLICATE = "duplicate item id"
WRONG_DATE = "weather attached to the wrong date"


def _with_a_shared_id(settings: Settings):
    plan = _plan(settings)
    shared = plan.days[0].items[0].id
    plan.days[1].items[0].id = shared
    assert any(DUPLICATE in error for error in validate_plan(plan, settings))
    return plan, shared


def test_a_repair_makes_the_ids_of_rows_that_share_one_distinct(settings: Settings) -> None:
    plan, shared = _with_a_shared_id(settings)
    reply = _payload(plan)
    reply["days"][1]["items"][0]["id"] = "writer_chose_this"

    repaired = _accepted(settings, plan, reply)

    assert validate_plan(repaired, settings) == []
    assert repaired.days[0].items[0].id == shared, "the first row keeps the id"
    assert repaired.days[1].items[0].id == f"{shared}-2"
    assert [item.name for _, item in repaired.iter_items()] == [item.name for _, item in plan.iter_items()]


def test_a_new_id_is_not_one_the_plan_already_uses(settings: Settings) -> None:
    plan, shared = _with_a_shared_id(settings)
    plan.days[2].items[0].id = f"{shared}-2"
    reply = _payload(plan)
    reply["days"][1]["items"][0]["id"] = "writer_chose_this"

    repaired = _accepted(settings, plan, reply)

    ids = [item.id for _, item in repaired.iter_items()]
    assert len(ids) == len(set(ids)) and f"{shared}-3" in ids


def test_a_writer_that_leaves_the_id_shared_is_still_refused(settings: Settings) -> None:
    plan, _ = _with_a_shared_id(settings)

    _refused(settings, plan, _payload(plan), "repair output was not a valid TripPlan")


def test_a_repair_still_cannot_relabel_a_row_whose_id_is_not_shared(settings: Settings) -> None:
    plan = _plan(settings)
    reply = _payload(plan)
    reply["days"][0]["items"][0]["id"] = "writer_relabelled_this"

    repaired = _accepted(settings, plan, reply)

    assert repaired.days[0].items[0].id == plan.days[0].items[0].id


def test_a_repair_moves_a_forecast_to_the_day_it_is_for(settings: Settings) -> None:
    plan = _plan(settings)
    first, second = plan.days[0].weather, plan.days[1].weather
    assert first is not None and second is not None
    plan.days[0].weather, plan.days[1].weather = second, first
    assert any(WRONG_DATE in error for error in validate_plan(plan, settings))

    repaired = _accepted(settings, plan, _payload(plan))

    assert validate_plan(repaired, settings) == []
    assert repaired.days[0].weather == first and repaired.days[1].weather == second
    assert repaired.data_quality.gaps == plan.data_quality.gaps, "both days have a forecast, so there is nothing to say"


def test_a_forecast_with_no_day_to_go_to_is_dropped_and_the_gap_says_why(settings: Settings) -> None:
    plan = _plan(settings)
    stray = plan.days[1].weather
    assert stray is not None
    stray = stray.model_copy(update={"date": plan.dates.end.replace(year=plan.dates.end.year + 1)})
    plan.days[1].weather = stray
    assert any(WRONG_DATE in error for error in validate_plan(plan, settings))

    repaired = _accepted(settings, plan, _payload(plan))

    assert validate_plan(repaired, settings) == []
    assert repaired.days[1].weather is None
    assert [day.weather for day in repaired.days if day.date != plan.days[1].date] == [
        day.weather for day in plan.days if day.date != plan.days[1].date
    ]
    [gap] = [gap for gap in repaired.data_quality.gaps if gap.section == "weather" and gap not in plan.data_quality.gaps]
    assert gap.retryable and str(plan.days[1].date) in gap.message


def test_a_forecast_whose_day_already_has_one_does_not_replace_it(settings: Settings) -> None:
    plan = _plan(settings)
    third = plan.days[2].weather
    assert third is not None
    plan.days[0].weather = third.model_copy()  # day 1 holds day 3's forecast, and day 3 has its own
    assert any(WRONG_DATE in error for error in validate_plan(plan, settings))

    repaired = _accepted(settings, plan, _payload(plan))

    assert repaired.days[2].weather == third
    assert repaired.days[0].weather is None
    assert any(gap.section == "weather" and gap.retryable for gap in repaired.data_quality.gaps)


def test_a_repair_still_cannot_change_a_forecast_that_is_on_its_own_day(settings: Settings) -> None:
    plan = _plan(settings)
    plan.days[1].items[0].id = plan.days[0].items[0].id  # a repair is called for
    reply = _payload(plan)
    reply["days"][1]["items"][0]["id"] = "writer_chose_this"
    reply["days"][0]["weather"]["high_c"] = 45.0

    repaired = _accepted(settings, plan, reply)

    assert repaired.days[0].weather == plan.days[0].weather
