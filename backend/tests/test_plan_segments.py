"""A plan is one or more city segments; the stay of a night is looked up by its date."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from plan_samples import START, make_plan, make_two_city_plan
from pydantic import ValidationError

from app.domain.models import TripPlan

_CLOCKS = {"created_at", "updated_at"}


def test_a_new_single_city_plan_gets_one_segment() -> None:
    plan = make_plan()

    assert [(s.id, s.destination.name, s.dates) for s in plan.segments] == [
        ("seg_1", "Kyoto", plan.dates)
    ]
    assert {day.segment_id for day in plan.days} == {"seg_1"}
    assert plan.hotels[0].segment_id == "seg_1"


def test_a_plan_stored_before_segments_loads_the_same_every_time() -> None:
    made = make_plan()
    old = made.model_dump(mode="json")
    old.pop("segments")
    for row in [*old["days"], *old["hotels"]]:
        row.pop("segment_id")

    first, second = TripPlan.model_validate(old), TripPlan.model_validate(old)

    assert first.segments[0].id == second.segments[0].id == "seg_1"
    assert first.model_dump(exclude=_CLOCKS) == second.model_dump(exclude=_CLOCKS)
    assert first.model_dump(exclude=_CLOCKS) == made.model_dump(exclude=_CLOCKS)


def test_a_single_segment_follows_the_trip_dates_and_destination() -> None:
    data = make_plan().model_dump(mode="json")
    data["segments"][0]["dates"] = {"start": "2020-01-01", "end": "2020-01-02"}
    data["segments"][0]["destination"]["name"] = "Elsewhere"

    plan = TripPlan.model_validate(data)

    assert plan.segments[0].dates == make_plan().dates
    assert plan.segments[0].destination.name == "Kyoto"


def test_the_day_two_cities_share_belongs_to_the_city_arrived_in() -> None:
    plan = make_two_city_plan()

    assert [plan.segment_of(day.date).id for day in plan.days] == [
        "seg_a",
        "seg_a",
        "seg_b",
        "seg_b",
        "seg_b",
    ]
    assert [day.segment_id for day in plan.days] == [
        "seg_a",
        "seg_a",
        "seg_b",
        "seg_b",
        "seg_b",
    ]
    assert [stay.segment_id for stay in plan.hotels] == ["seg_a", "seg_b"]


def test_a_single_city_plan_has_one_segment_for_any_date() -> None:
    plan = make_plan()
    assert plan.segment_of(date(2031, 1, 1)).id == "seg_1"


def test_the_stay_of_each_night_and_of_the_last_morning() -> None:
    plan = make_two_city_plan()

    assert [plan.stay_on(day.date).id for day in plan.days] == [
        "hotel_a",
        "hotel_a",
        "hotel_b",
        "hotel_b",
        "hotel_b",
    ]
    assert [stay.id for stay in plan.stays_in("seg_b")] == ["hotel_b"]
    assert plan.stays_in("seg_none") == []
    assert plan.first_stay.id == "hotel_a"


def test_a_night_with_no_stay_has_none() -> None:
    plan = make_two_city_plan()
    plan.hotels = plan.hotels[:1]

    assert plan.stay_on(date(2026, 12, 6)) is None
    assert plan.stay_on(date(2026, 12, 3)).id == "hotel_a"


def test_a_single_stay_whose_dates_drifted_is_still_the_stay() -> None:
    plan = make_plan()
    plan.hotels[0].check_in, plan.hotels[0].check_out = (
        date(2026, 11, 1),
        date(2026, 11, 2),
    )

    assert plan.stay_on(plan.days[0].date).id == "hotel_1"


def test_a_plan_with_no_stay() -> None:
    plan = make_plan(with_stay=False)
    assert plan.first_stay is None and plan.stay_on(START) is None


def test_segments_must_cover_the_trip_in_order() -> None:
    good = make_two_city_plan().model_dump(mode="json")

    gap = {**good, "segments": [dict(row) for row in good["segments"]]}
    gap["segments"][1] = {
        **gap["segments"][1],
        "dates": {"start": "2026-12-06", "end": "2026-12-07"},
    }
    late = {**good, "segments": [dict(row) for row in good["segments"]]}
    late["segments"][0] = {
        **late["segments"][0],
        "dates": {"start": "2026-12-04", "end": "2026-12-05"},
    }
    other = {**good, "destination": {"name": "Tokyo"}}

    for broken in (gap, late, other):
        with pytest.raises(ValidationError):
            TripPlan.model_validate(broken)
    assert TripPlan.model_validate(good).segments[1].id == "seg_b"


def test_no_code_outside_the_model_reads_the_hotels_by_position() -> None:
    offenders = [
        f"{path.as_posix()}:{number}"
        for path in Path("app").rglob("*.py")
        if path.name != "models.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\.hotels\[\d", line.split("#")[0]) and "``" not in line
    ]
    assert offenders == []


def _choose(plan: TripPlan, settings, **choice):
    import asyncio

    from app.application.choices import apply_choice
    from app.application.planner import Planner
    from app.infrastructure.providers.fx import FxService

    planner = Planner(None, settings, FxService())
    return asyncio.run(apply_choice(plan, planner=planner, context=None, **choice))


def _two_city_plan_with_an_osaka_option() -> TripPlan:
    plan = make_two_city_plan()
    plan.hotel_options = [
        plan.hotels[1].model_copy(update={"id": "opt_b2", "name": "Osaka Ryokan", "segment_id": "seg_b"})
    ]
    return plan


def test_a_choice_of_stay_replaces_the_stay_it_names_and_no_other(settings) -> None:
    out = _choose(
        _two_city_plan_with_an_osaka_option(), settings, kind="hotel", target_id="hotel_b", option_id="opt_b2"
    )

    assert [stay.id for stay in out.hotels] == ["hotel_a", "opt_b2"]
    chosen = out.hotels[1]
    assert (chosen.check_in, chosen.check_out, chosen.segment_id) == (
        date(2026, 12, 5),
        date(2026, 12, 7),
        "seg_b",
    )
    assert out.hotels[0].name == make_two_city_plan().hotels[0].name


def test_a_choice_of_stay_with_no_target_replaces_the_stay_of_the_options_city(settings) -> None:
    named = _choose(
        _two_city_plan_with_an_osaka_option(), settings, kind="hotel", target_id="hotel_b", option_id="opt_b2"
    )
    unnamed = _choose(
        _two_city_plan_with_an_osaka_option(), settings, kind="hotel", target_id=None, option_id="opt_b2"
    )

    assert [stay.model_dump(exclude={"fetched_at"}) for stay in unnamed.hotels] == [
        stay.model_dump(exclude={"fetched_at"}) for stay in named.hotels
    ]


def test_a_choice_naming_a_stay_that_is_not_chosen_is_refused(settings) -> None:
    from app.application.choices import ChoiceNotFound

    with pytest.raises(ChoiceNotFound, match="stay not found"):
        _choose(_two_city_plan_with_an_osaka_option(), settings, kind="hotel", target_id="nope", option_id="opt_b2")


def test_every_chosen_stay_is_among_the_options() -> None:
    from app.application.choices import normalise_hotel_options

    plan = _two_city_plan_with_an_osaka_option()
    normalise_hotel_options(plan)

    assert {"hotel_a", "hotel_b", "opt_b2"} <= {option.id for option in plan.hotel_options}
    assert plan.hotel_options[0].id == plan.first_stay.id
