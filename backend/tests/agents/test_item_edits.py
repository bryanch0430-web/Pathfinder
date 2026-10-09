"""Manual edits of one stop (design 4.2): pure plan operations, no agent run."""

from __future__ import annotations

from datetime import date, time

import pytest
from pydantic import ValidationError

from backend.agents.item_edits import apply_item_patch, recompute_plan, remove_item
from backend.agents.plan_ops import PlanRequestError
from backend.schemas.edits import PlanItemPatch
from backend.schemas.trip_plan import ItineraryItem
from backend.tests.agents.helpers import DAY1, DAY2, make_trip_context, make_trip_plan

TEMPLE = "temple@2026-04-10"  # day 1, 10:00-12:00
SHRINE = "shrine@2026-04-10"  # day 1, 12:30-14:30, confirmed (locked)


def _ids(items: list[ItineraryItem]) -> list[str]:
    return [i.item_id for i in items]


def test_patch_rejects_an_empty_body_and_nulls_for_times_and_day() -> None:
    with pytest.raises(ValidationError):
        PlanItemPatch.model_validate({})
    for field in ("start_time", "end_time", "day"):
        with pytest.raises(ValidationError):
            PlanItemPatch.model_validate({field: None})
    with pytest.raises(ValidationError):
        PlanItemPatch.model_validate({"start_time": "09:00+09:00"})  # local times only
    with pytest.raises(ValidationError):
        PlanItemPatch.model_validate({"note": "x" * 501})
    assert PlanItemPatch.model_validate({"start_time": "09:30"}).start_time == time(9, 30)
    assert PlanItemPatch.model_validate({"note": None}).model_fields_set == {"note"}


def test_new_times_re_sort_the_day() -> None:
    edited = apply_item_patch(
        make_trip_plan(), TEMPLE, PlanItemPatch(start_time=time(15, 0), end_time=time(16, 0))
    )
    assert _ids(edited.days[0].items) == [SHRINE, TEMPLE]
    moved = edited.days[0].items[1]
    assert (moved.start_time, moved.end_time) == (time(15, 0), time(16, 0))
    assert edited.days[1] == make_trip_plan().days[1]


def test_note_is_set_and_cleared() -> None:
    noted = apply_item_patch(make_trip_plan(), TEMPLE, PlanItemPatch(note="Go early"))
    assert noted.days[0].items[0].note == "Go early"
    cleared = apply_item_patch(noted, TEMPLE, PlanItemPatch.model_validate({"note": None}))
    assert cleared.days[0].items[0].note is None
    assert cleared.days[0].items[0].start_time == time(10, 0)  # fields not sent are kept


def test_move_appends_to_the_new_day_then_sorts_by_start_time() -> None:
    moved = apply_item_patch(make_trip_plan(), TEMPLE, PlanItemPatch(day=DAY2))
    assert _ids(moved.days[0].items) == [SHRINE]
    # museum 09:00, temple 10:00, market 11:30
    assert _ids(moved.days[1].items) == ["museum@2026-04-11", TEMPLE, "market@2026-04-11"]


def test_end_must_follow_start_after_merging_with_the_stored_times() -> None:
    with pytest.raises(PlanRequestError) as caught:
        apply_item_patch(make_trip_plan(), TEMPLE, PlanItemPatch(end_time=time(9, 0)))  # starts 10:00
    assert caught.value.status_code == 422 and "end_time" in caught.value.message
    with pytest.raises(PlanRequestError):
        apply_item_patch(make_trip_plan(), TEMPLE, PlanItemPatch(start_time=time(12, 0)))  # ends 12:00


def test_day_must_be_a_trip_day() -> None:
    with pytest.raises(PlanRequestError) as caught:
        apply_item_patch(make_trip_plan(), TEMPLE, PlanItemPatch(day=date(2026, 4, 12)))
    assert caught.value.status_code == 422 and "outside the trip" in caught.value.message


def test_unknown_and_locked_items_are_refused() -> None:
    with pytest.raises(PlanRequestError) as unknown:
        apply_item_patch(make_trip_plan(), "nope", PlanItemPatch(note="x"))
    assert unknown.value.status_code == 422
    with pytest.raises(PlanRequestError) as locked:
        apply_item_patch(make_trip_plan(), SHRINE, PlanItemPatch(note="x"))
    assert locked.value.status_code == 409 and "unlock it first" in locked.value.message
    with pytest.raises(PlanRequestError) as locked_delete:
        remove_item(make_trip_plan(), SHRINE)
    assert locked_delete.value.status_code == 409
    with pytest.raises(PlanRequestError) as unknown_delete:
        remove_item(make_trip_plan(), "nope")
    assert unknown_delete.value.status_code == 422


def test_remove_drops_the_stop_and_its_unused_place() -> None:
    plan = remove_item(make_trip_plan(), TEMPLE)
    assert _ids(plan.days[0].items) == [SHRINE]
    assert plan.place("temple") is None and plan.place("museum") is not None


def test_recompute_prices_the_edited_plan_as_a_new_version() -> None:
    before = recompute_plan(make_trip_plan(), make_trip_context())
    assert before.cost is not None and before.cost.attractions == (500 + 1500) * 2
    after = recompute_plan(remove_item(before, TEMPLE), make_trip_context())
    assert after.cost is not None and after.cost.attractions == 1500 * 2
    assert after.cost.total == before.cost.total - 500 * 2
    assert after.version == before.version + 1 and after.updated_at >= before.updated_at
    assert after.days[0].date == DAY1
