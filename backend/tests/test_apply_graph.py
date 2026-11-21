"""A named add places a sourced stop when the usual slots are full, and warns instead of refusing."""

from app.application.apply_graph import run_apply
from app.application.validation import is_edit_outcome
from app.domain.models import DataGap, FailureKind, GeoPoint
from tests.plan_samples import make_plan, stop

_SOURCE = [{"title": "Ocean Park", "url": "https://example.com/ocean", "source": "test"}]


def _row(name: str, lat: float, lng: float, row_id: str = "ocean") -> dict:
    return {
        "id": row_id,
        "name": name,
        "description": "A sourced place",
        "geo": {"lat": lat, "lng": lng},
        "source_refs": _SOURCE,
    }


def _pack(day) -> None:
    day.items = [
        stop("a", "Block A", "08:00", "12:00"),
        stop("b", "Block B", "12:00", "14:00"),
        stop("c", "Block C", "14:00", "18:30"),
        stop("d", "Block D", "18:30", "21:00"),
    ]


def test_a_full_day_still_gets_the_named_place_and_a_warning() -> None:
    plan = make_plan(days=3, with_tickets=False)
    _pack(plan.days[2])
    added, warnings = run_apply(plan, [_row("Ocean Park", 22.25, 114.17)], "can you add ocean park to day3")

    assert added == ["Ocean Park"]
    placed = next(item for item in plan.days[2].items if item.name == "Ocean Park")
    assert placed.start == "21:00"
    ordered = sorted(plan.days[2].items, key=lambda item: item.start)
    for previous, current in zip(ordered, ordered[1:]):
        assert current.start >= previous.end
    assert any("usual times" in text for text in warnings)
    assert any("after 21:00" in text for text in warnings)
    assert all(is_edit_outcome(DataGap(section="attractions", kind=FailureKind.NO_DATA, message=text)) for text in warnings)


def test_a_far_stop_warns_and_a_near_one_does_not() -> None:
    far = make_plan(days=1, with_tickets=False)
    far.days[0].items[0].geo = GeoPoint(lat=22.30, lng=114.17)
    _, far_notes = run_apply(far, [_row("Ocean Park", 22.70, 114.17)], "add Ocean Park on day 1")
    assert any("km" in text for text in far_notes)

    near = make_plan(days=1, with_tickets=False)
    near.days[0].items[0].geo = GeoPoint(lat=22.30, lng=114.17)
    added, near_notes = run_apply(near, [_row("Ocean Park", 22.36, 114.17)], "add Ocean Park on day 1")
    assert added == ["Ocean Park"]
    assert not any("km" in text for text in near_notes)


def test_an_over_budget_trip_still_adds_and_says_so() -> None:
    plan = make_plan(days=1, with_tickets=False)
    plan.cost.status = "over"
    added, warnings = run_apply(plan, [_row("Ocean Park", 22.25, 114.17)], "add Ocean Park on day 1")
    assert added == ["Ocean Park"]
    assert any("budget" in text for text in warnings)


def test_a_name_with_no_pin_is_not_invented() -> None:
    plan = make_plan(days=1, with_tickets=False)
    before = [item.name for item in plan.days[0].items]
    added, warnings = run_apply(plan, [], "add Ocean Park on day 1", named="Ocean Park")
    assert added == []
    assert [item.name for item in plan.days[0].items] == before
    assert any("did not return a located Ocean Park" in text for text in warnings)


def test_a_kind_with_no_rows_stays_for_the_old_reply() -> None:
    plan = make_plan(days=1, with_tickets=False)
    added, warnings = run_apply(plan, [], "Add a museum")
    assert added == []
    assert warnings == []
