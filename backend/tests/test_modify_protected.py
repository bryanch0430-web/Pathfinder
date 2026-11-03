"""Locked and confirmed stops when a trip changes length and the plan is built again.

A stop the traveller locked is never dropped, planned twice or moved to another day, and the plan that comes
back has to be one the store can read again: stops that overlap make a plan that fails to load.
Offline: the sandbox providers; `_modify` (test_modify_targets) runs one edit on a Kyoto plan.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import pytest

from app.application.modification import ModifyPath
from app.application.toolbox import Toolbox
from app.application.validation import validate_plan
from app.config import Settings
from app.domain.models import PlanItem, TransferLeg, TripPlan
from app.infrastructure.observability import NoopTracer
from app.infrastructure.store import SQLiteStore
from tests.test_modify_targets import _build, _modify


def _custom(plan: TripPlan, day: int, name: str, start: str, end: str, ident: str, **marks: bool) -> PlanItem:
    """A stop of the traveller's own on day `day` (1-based), a copy of the plan's first stop under another name."""
    stop = plan.days[0].items[0].model_copy(deep=True)
    stop.id, stop.name, stop.start, stop.end, stop.transfer = ident, name, start, end, None
    stop.locked, stop.confirmed = marks.get("locked", True), marks.get("confirmed", False)
    plan.days[day - 1].items.append(stop)
    plan.days[day - 1].items.sort(key=lambda item: item.start)
    return stop


def _where(plan: TripPlan, name: str) -> list[int]:
    """The days (1-based) a stop of that name is on."""
    return [number for number, day in enumerate(plan.days, start=1) if any(item.name == name for item in day.items)]


def _overlaps(plan: TripPlan) -> list[str]:
    found = []
    for number, day in enumerate(plan.days, start=1):
        ordered = sorted(day.items, key=lambda item: item.start)
        found += [f"day {number}: {a.name} / {b.name}" for a, b in zip(ordered, ordered[1:]) if b.start < a.end]
    return found


def _kept_texts(plan: TripPlan) -> list[str]:
    return [gap.message for gap in plan.data_quality.gaps if gap.message.startswith("Kept your")]


def _lock_every_stop(plan: TripPlan) -> None:
    for day in plan.days:
        for item in day.items:
            item.locked = True


# -- G11: a locked stop keeps its day ----------------------------------------------------------------


@pytest.mark.parametrize("old_days,new_days", [(3, 4), (4, 5), (3, 2), (4, 3), (5, 3), (5, 4), (4, 2)])
def test_every_locked_stop_stays_on_its_day_when_the_trip_changes_length(
    settings: Settings, old_days: int, new_days: int
) -> None:
    held: dict[str, int] = {}

    def lock(plan: TripPlan) -> None:
        for number, day in enumerate(plan.days, start=1):
            for item in day.items:
                item.locked = True
                held[item.name] = number

    _, updated, _ = _modify(
        settings, f"make it {new_days} days", ["dates"], days=old_days, new_days=new_days, prepare=lock
    )

    assert len(updated.days) == new_days
    for name, day in held.items():
        assert _where(updated, name) == [min(day, new_days)], f"{name} was on day {day}"
    assert _overlaps(updated) == []
    assert validate_plan(updated, settings) == []


# -- G12: stops of the same name -----------------------------------------------------------------------


def test_two_locked_stops_of_one_name_are_both_kept(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        _custom(plan, 1, "Hotel breakfast", "07:30", "08:15", "item_bk1")
        _custom(plan, 2, "Hotel breakfast", "07:30", "08:15", "item_bk2")

    plan, updated, diff = _modify(settings, "make it 4 days", ["dates"], days=5, new_days=4, prepare=prepare)

    assert _where(updated, "Hotel breakfast") == [1, 2]
    assert all(item.locked for day in updated.days for item in day.items if item.name == "Hotel breakfast")
    assert not [change for change in diff if change["kind"] == "removed" and change["name"] == "Hotel breakfast"]
    assert _overlaps(updated) == []


def test_a_locked_and_a_confirmed_stop_of_one_name_keep_their_own_marks(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        _custom(plan, 4, "Evening walk", "20:00", "21:00", "item_w1", locked=True, confirmed=False)
        _custom(plan, 5, "Evening walk", "20:00", "21:00", "item_w2", locked=False, confirmed=True)

    _, updated, _ = _modify(settings, "make it 3 days", ["dates"], days=5, new_days=3, prepare=prepare)

    walks = [item for day in updated.days for item in day.items if item.name == "Evening walk"]
    assert sorted((item.locked, item.confirmed) for item in walks) == [(False, True), (True, False)]
    assert len(_kept_texts(updated)) == 2


# -- G13: the same place under another spelling -----------------------------------------------------


def test_a_locked_stop_is_not_planned_a_second_time_under_the_rebuilds_spelling(settings: Settings) -> None:
    rebuilt_name = _build(settings, days=4)[4].days[0].items[0].name

    def prepare(plan: TripPlan) -> None:
        plan.days[0].items[0].name = f"{rebuilt_name} Temple"
        plan.days[0].items[0].locked = True

    _, updated, _ = _modify(settings, "make it 4 days", ["dates"], days=5, new_days=4, prepare=prepare)

    names = [item.name for day in updated.days for item in day.items]
    assert names.count(f"{rebuilt_name} Temple") == 1 and rebuilt_name not in names
    assert next(item for day in updated.days for item in day.items if item.name == f"{rebuilt_name} Temple").locked
    assert _overlaps(updated) == []


# -- G14: stops that would overlap ---------------------------------------------------------------------


def test_locked_stops_from_removed_days_do_not_pile_up_at_one_time(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        _custom(plan, 4, "Philosopher's Path", "09:00", "10:30", "item_c1")
        _custom(plan, 5, "Nanzen-ji Garden Walk", "09:00", "10:30", "item_c2")

    _, updated, _ = _modify(settings, "make it 3 days", ["dates"], days=5, new_days=3, prepare=prepare)

    assert _where(updated, "Philosopher's Path") == [3] and _where(updated, "Nanzen-ji Garden Walk") == [3]
    assert _overlaps(updated) == []
    assert validate_plan(updated, settings) == []
    # The first keeps its time; the second moves to the next free one, and the plan says where both are.
    times = {item.name: item.start for item in updated.days[2].items}
    assert times["Philosopher's Path"] == "09:00" and times["Nanzen-ji Garden Walk"] > "09:00"
    assert len(_kept_texts(updated)) == 2


def test_a_plan_with_restored_stops_can_be_read_from_the_store_again(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        _custom(plan, 4, "Philosopher's Path", "09:00", "10:30", "item_c1")
        _custom(plan, 5, "Nanzen-ji Garden Walk", "09:00", "10:30", "item_c2")

    _, updated, _ = _modify(settings, "make it 3 days", ["dates"], days=5, new_days=3, prepare=prepare)
    store = SQLiteStore(settings.database_path)
    updated.session_id = store.create_session("guest-protected").id

    store.save_plan(updated, "rebuilt")

    assert store.get_plan(updated.session_id).item_count == updated.item_count


def test_fourteen_locked_stops_from_seven_removed_days_all_stay_without_overlap(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        for number in range(4, 11):
            _custom(plan, number, f"Morning walk {number}", "09:00", "10:30", f"item_a{number}")
            _custom(plan, number, f"Afternoon walk {number}", "14:00", "15:30", f"item_b{number}")

    _, updated, _ = _modify(settings, "make it 3 days", ["dates"], days=10, new_days=3, prepare=prepare)

    walks = [item.name for day in updated.days for item in day.items if "walk" in item.name]
    assert sorted(walks) == sorted(
        [f"Morning walk {number}" for number in range(4, 11)] + [f"Afternoon walk {number}" for number in range(4, 11)]
    )
    assert _overlaps(updated) == []
    assert validate_plan(updated, settings) == []
    assert len(_kept_texts(updated)) == 14


def test_validation_reports_stops_that_overlap(settings: Settings) -> None:
    plan = _build(settings)[4]
    first = plan.days[0].items[0]
    second = plan.days[0].items[1]
    second.start, second.end = first.start, first.end

    errors = validate_plan(plan, settings)

    assert any("overlap" in error and first.name in error and second.name in error for error in errors)
    assert validate_plan(_build(settings)[4], settings) == []


# -- G15: a restored stop is part of its new day --------------------------------------------------------


def _rebuild(
    settings: Settings,
    *,
    days: int,
    new_days: int,
    prepare: Callable[[TripPlan], None],
    hard_constraints: list[str] | None = None,
) -> TripPlan:
    """Plan `days` days (under the constraints), let `prepare` edit the plan, then ask for `new_days` days."""
    gateway, planner, plan_path, context, _ = _build(settings, days=days)
    context.hard_constraints = list(hard_constraints or [])
    plan = asyncio.run(
        plan_path.run(
            context=context,
            session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    prepare(plan)
    context.days = new_days
    context.end_date = context.start_date.fromordinal(context.start_date.toordinal() + new_days - 1)
    updated, _ = asyncio.run(
        ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway).run(
            plan=plan,
            context=context,
            message=f"make it {new_days} days",
            route_parts=["dates"],
            toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    return updated


def test_a_restored_stop_takes_its_transfer_from_its_new_neighbour(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        stop = _custom(plan, 4, "Philosopher's Path", "15:00", "16:30", "item_p9")
        stop.transfer = TransferLeg(
            from_item_id="item_p4", minutes=25, mode="metro", source="transport-model", tool="routes.segments"
        )

    updated = _rebuild(settings, days=5, new_days=3, prepare=prepare)

    day = updated.days[_where(updated, "Philosopher's Path")[0] - 1]
    ids = {item.id for item in day.items}
    for item in day.items:
        assert item.transfer is None or item.transfer.from_item_id in ids, (item.name, item.transfer)
    restored = next(item for item in day.items if item.name == "Philosopher's Path")
    previous = sorted(day.items, key=lambda item: item.start)[sorted(day.items, key=lambda item: item.start).index(restored) - 1]
    assert restored.transfer is not None and restored.transfer.from_item_id == previous.id
    assert "from the previous stop" in restored.notes


def test_a_stop_that_starts_its_day_no_longer_says_how_far_it_is_from_the_stop_before(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        stop = _custom(plan, 4, "Philosopher's Path", "08:30", "09:00", "item_p9")
        stop.notes = "A quiet walk. About 25 min by metro or bus from the previous stop (estimate)."
        stop.transfer = TransferLeg(from_item_id="item_p4", minutes=25, mode="metro", source="transport-model")

    updated = _rebuild(settings, days=5, new_days=4, prepare=prepare)

    day = updated.days[3]
    first = sorted(day.items, key=lambda item: item.start)[0]
    assert first.name == "Philosopher's Path"
    assert first.transfer is None and first.notes == "A quiet walk."


def test_a_locked_stop_does_not_land_on_a_day_the_traveller_kept_free(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        assert plan.days[3].items == [] and plan.days[3].title.startswith("Free day")
        _custom(plan, 4, "Philosopher's Path", "15:00", "16:30", "item_c1")

    updated = _rebuild(settings, days=5, new_days=4, prepare=prepare, hard_constraints=["Day 4 is a free day"])

    assert updated.days[3].items == [] and updated.days[3].note == "Kept free, as you asked."
    assert _where(updated, "Philosopher's Path") == [3]
    free = next(check for check in updated.constraints if check.text == "Day 4 is a free day")
    assert free.status.value == "pass"
    assert [text for text in _kept_texts(updated)] == ["Kept your locked stop Philosopher's Path on day 3."]


def test_hours_stated_for_one_weekday_do_not_follow_a_stop_to_another(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        for number in (4, 5):
            stop = _custom(plan, number, f"Garden {number}", "13:00", "14:30", f"item_g{number}")
            stop.opening_hours = [f"{plan.days[number - 1].date.strftime('%A')}: 9:00 AM – 5:00 PM"]
            stop.hours_status = "stated"

    updated = _rebuild(settings, days=5, new_days=4, prepare=prepare)

    stays = next(item for day in updated.days for item in day.items if item.name == "Garden 4")
    moves = next(item for day in updated.days for item in day.items if item.name == "Garden 5")
    assert (stays.hours_status, len(stays.opening_hours)) == ("stated", 1), "day 4 is still day 4"
    assert (moves.hours_status, moves.opening_hours) == ("unknown", []), "day 5 became day 4: another weekday"


def test_a_stop_put_on_an_empty_day_makes_the_day_and_its_check_read_so(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        assert plan.days[4].items == [] and plan.days[5].items == []
        _custom(plan, 6, "Philosopher's Path", "15:00", "16:30", "item_c1")

    updated = _rebuild(settings, days=6, new_days=5, prepare=prepare)

    assert _where(updated, "Philosopher's Path") == [5]
    assert updated.days[4].note == ""
    populated = next(check for check in updated.constraints if check.text == "Every day has at least one verified stop")
    assert (populated.status.value, populated.evidence) == ("pass", "All days populated.")


def test_a_day_filled_by_locked_stops_says_which_stop_it_had_no_time_for(settings: Settings) -> None:
    def prepare(plan: TripPlan) -> None:
        for number, (start, end) in enumerate(
            (("08:00", "12:00"), ("12:00", "16:00"), ("16:00", "20:00"), ("20:00", "23:30")), start=1
        ):
            _custom(plan, 3, f"Long visit {number}", start, end, f"item_long{number}")

    plan, updated, _ = _modify(settings, "make it 3 days", ["dates"], days=5, new_days=3, prepare=prepare)

    day = updated.days[2]
    assert [item.name for item in day.items] == [f"Long visit {number}" for number in range(1, 5)]
    assert all(item.locked for item in day.items) and _overlaps(updated) == []
    left_out = [gap.message for gap in updated.data_quality.gaps if "was left out" in gap.message]
    assert left_out == [
        "There was no time left on day 3 for Arashiyama Bamboo Grove around your locked stops, so it was left out."
    ]
    assert validate_plan(updated, settings) == []
