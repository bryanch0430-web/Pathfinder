"""A move is not thrown away, and a full day is a list of swaps the traveller accepts."""

from app.application.modification import _move_named, _stated_move, keep_only
from app.domain.models import TripPlan
from app.application.proposals import (
    apply_change,
    apply_layout,
    decision_record,
    locks_to_one_row,
    mark_decision,
    proposal_changes,
    swap_suggestions,
)
from tests.plan_samples import make_plan, make_route_plan, stop


def test_the_sentence_is_a_move_of_shibuya_sky_to_day_2() -> None:
    plan = make_plan(days=8, with_tickets=False)
    plan.days[7].items[0].name = "Shibuya Sky"
    assert _stated_move(plan, "can you move shibuya sky from day8 to day2?") == ("Shibuya Sky", 2)


def test_locking_the_named_stop_puts_a_move_back_on_its_old_day() -> None:
    plan = make_plan(days=8, with_tickets=False)
    plan.days[7].items[0].name = "Shibuya Sky"
    item_id = plan.days[7].items[0].id
    original = plan.model_copy(deep=True)
    moved, _ = _move_named(plan, "Shibuya Sky", 2)

    assert moved and locks_to_one_row("replace") and not locks_to_one_row("move")
    undone = plan.model_copy(deep=True)
    keep_only(original, undone, item_id)
    assert any(item.id == item_id for item in undone.days[7].items)
    assert all(item.id != item_id for item in undone.days[1].items)


def test_a_full_day_offers_one_swap_per_stop_and_one_tick_applies_it() -> None:
    plan = make_plan(days=8, with_tickets=False)
    sky = plan.days[7].items[0]
    sky.name = "Shibuya Sky"
    plan.days[1].items = [
        stop("ivy", "Ivy Place", "08:00", "12:00"),
        stop("museum", "TeamLab", "12:00", "16:00"),
        stop("park", "Yoyogi", "16:00", "20:00"),
        stop("tower", "Tokyo Tower", "20:00", "23:30"),
    ]
    moved, sentence = _move_named(plan, "Shibuya Sky", 2)

    assert not moved and "no free time slot" in sentence
    offers = swap_suggestions(plan, sky, 2)
    assert [offer["label"].split()[0] for offer in offers] == ["Replace", "Replace", "Replace", "Replace"]
    assert "Ivy Place" in offers[0]["label"]

    updated = apply_change(plan, offers[0])
    assert updated.version == plan.version + 1
    assert any(item.name == "Shibuya Sky" for item in updated.days[1].items)
    assert all(item.name != "Ivy Place" for day in (updated.days[1],) for item in day.items)
    assert all(item.name != "Shibuya Sky" for item in updated.days[7].items)

    mark_decision(offers, offers[0]["id"], True)
    assert offers[0]["status"] == "accepted"
    assert {offer["status"] for offer in offers[1:]} == {"rejected"}


def test_a_free_day_is_one_move_waiting_to_be_accepted() -> None:
    plan = make_plan(days=3, with_tickets=False)
    plan.days[0].items[0].name = "Shibuya Sky"
    before = plan.model_copy(deep=True)
    assert _move_named(plan, "Shibuya Sky", 3)[0]

    labels = [change["label"] for change in proposal_changes(before, plan)]
    assert len(labels) == 1 and labels[0].startswith("Move Shibuya Sky from Day 1") and "Day 3" in labels[0]


def test_the_grid_moves_a_stop_to_another_morning_and_refuses_a_locked_one() -> None:
    plan = make_plan(days=2, with_tickets=False)
    item = plan.days[1].items[0]
    moved = apply_layout(plan, [{"id": item.id, "day": 1, "start": "10:00", "end": "11:00"}])

    assert moved.version == plan.version + 1
    assert any(row.id == item.id and row.start == "10:00" and row.end == "11:00" for row in moved.days[0].items)
    assert all(row.id != item.id for row in moved.days[1].items)
    assert apply_layout(moved, [{"id": item.id, "day": 1, "start": "10:00", "end": "11:00"}]) is moved

    item.locked = True
    try:
        apply_layout(plan, [{"id": item.id, "day": 1, "start": "09:00", "end": "10:30"}])
    except ValueError:
        return
    raise AssertionError("a locked stop must stay where it is")


def test_an_overlapping_edit_is_refused_and_leaves_the_plan_as_it_was() -> None:
    plan = make_plan(days=1, with_tickets=False)
    first, second = plan.days[0].items[0], plan.days[0].items[1]
    try:
        apply_layout(plan, [{"id": second.id, "day": 1, "start": first.start, "end": first.end}])
    except ValueError as refused:
        assert first.name in str(refused) and second.name in str(refused)
    else:
        raise AssertionError("two stops at the same time must not be saved")
    assert TripPlan.model_validate_json(plan.model_dump_json()).days[0].items


def test_an_edit_that_changed_nothing_records_why() -> None:
    record = decision_record(
        "can you move shibuya sky from day8 to day2?",
        "Nothing in the plan matched that request, so it is unchanged.",
        [],
        held=False,
    )
    assert record["ok"] is False
    assert any("day 2" in step for step in record["steps"])


def test_a_held_edit_changes_the_stay_of_each_city_of_a_multi_city_plan() -> None:
    old = make_route_plan(["Tokyo", "Kyoto"])
    new = old.model_copy(deep=True)
    new.hotels[1].price_per_night = new.hotels[1].price_per_night.model_copy(update={"amount": 900.0})

    changes = proposal_changes(old, new)
    [change] = [row for row in changes if "hotel" in row["id"]]
    assert change["segment_id"] == "seg_2" and "Kyoto Inn 2" in change["label"]

    applied = apply_change(old, change)
    assert [hotel.id for hotel in applied.hotels] == ["hotel_1", "hotel_2"]
    assert applied.hotels[0].price_per_night == old.hotels[0].price_per_night
    assert applied.hotels[1].price_per_night.amount == 900.0
