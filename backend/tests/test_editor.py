from __future__ import annotations

from datetime import date

from app.config import Settings
from app.domain.models import GeoPoint, Money, SourceRef, TripContext, TripPlan
from app.evidence.models import Bundle, Evidence
from app.plan.builder import build_plan
from app.plan.editor import _apply, diff_plans
from app.understand.schemas import DayChoice, EditOp, PlanDraft, StopChoice


def _plan() -> TripPlan:
    evidence = [
        Evidence(
            ref="at-1", kind="attraction", name="Temple", address="A", geo=GeoPoint(lat=35.0, lng=135.0),
            source_refs=[SourceRef(title="Temple", url="https://e.test", source="Test")], suggested_minutes=60,
        ),
        Evidence(
            ref="at-2", kind="attraction", name="Museum", address="B", geo=GeoPoint(lat=35.01, lng=135.01),
            source_refs=[SourceRef(title="Museum", url="https://e.test", source="Test")], suggested_minutes=60,
        ),
        Evidence(
            ref="me-1", kind="meal", name="Lunch", address="C", geo=GeoPoint(lat=35.02, lng=135.02),
            source_refs=[SourceRef(title="Lunch", url="https://e.test", source="Test")], price=Money(amount=1000, currency="JPY"),
        ),
    ]
    bundle = Bundle()
    bundle.add(evidence, "test")
    context = TripContext(
        session_id="s1", destination="Kyoto", start_date="2027-04-10", end_date="2027-04-11",
        days=2, party_size=2, budget=100000, currency="JPY",
    )
    draft = PlanDraft(
        summary="x",
        days=[
            DayChoice(date="2027-04-10", stops=[StopChoice(ref="at-1", start="09:00", end="10:00")]),
            DayChoice(date="2027-04-11", stops=[StopChoice(ref="at-2", start="09:00", end="10:00")]),
        ],
    )
    return build_plan(
        context=context, bundle=bundle, draft=draft, disruptions=[], weather_by_date={},
        start=date(2027, 4, 10), end=date(2027, 4, 11), settings=Settings(database_path="data/test.db"),
    )


def test_move_changes_the_day_and_reports_a_diff():
    plan = _plan()
    item = plan.days[0].items[0]
    log: list[str] = []
    changed = _apply(EditOp(op="move", item_id=item.id, day=2, start="14:00", end="15:00"), plan, _context(), Bundle(), log)
    assert changed is True
    assert any(entry.name == "Temple" for entry in plan.days[1].items)
    assert not any(entry.id == item.id for entry in plan.days[0].items)
    diff = diff_plans(_plan(), plan)
    assert any(entry["kind"] == "changed" for entry in diff)


def test_locked_item_is_never_removed():
    plan = _plan()
    item = plan.days[0].items[0]
    item.locked = True
    log: list[str] = []
    changed = _apply(EditOp(op="remove", item_id=item.id), plan, _context(), Bundle(), log)
    assert changed is False
    assert any("locked" in line.lower() for line in log)
    assert any(entry.id == item.id for entry in plan.days[0].items)


def test_add_uses_sourced_evidence_only():
    plan = _plan()
    log: list[str] = []
    bundle = Bundle()
    changed = _apply(EditOp(op="add", ref="at-2", day=2, start="15:00", end="16:00"), plan, _context(), bundle, log)
    assert changed is False  # ref not in this bundle
    evidence = Evidence(
        ref="at-2", kind="attraction", name="Museum", source_refs=[SourceRef(title="Museum", url="https://e.test", source="Test")]
    )
    bundle.add([evidence], "test")
    changed = _apply(EditOp(op="add", ref="at-2", day=2, start="15:00", end="16:00"), plan, _context(), bundle, log)
    assert changed is True
    assert any(entry.name == "Museum" for entry in plan.days[1].items)


def test_swap_hotel_keeps_dates():
    plan = _plan()
    bundle = Bundle()
    bundle.add(
        [
            Evidence(
                ref="ho-2", kind="hotel", name="New Hotel",
                source_refs=[SourceRef(title="New Hotel", url="https://e.test", source="Test")],
                price=Money(amount=9000, currency="JPY"),
            )
        ],
        "test",
    )
    log: list[str] = []
    assert _apply(EditOp(op="swap_hotel", ref="ho-2"), plan, _context(), bundle, log) is True
    assert plan.hotels[0].name == "New Hotel"
    assert plan.hotels[0].check_in == plan.dates.start


def test_diff_reports_added_removed_and_hotel():
    old = _plan()
    new = _plan()
    new.days[0].items.pop()
    new.hotels = []
    diff = diff_plans(old, new)
    kinds = {entry["kind"] for entry in diff}
    assert "removed" in kinds


def _context() -> TripContext:
    return TripContext(
        session_id="s1", destination="Kyoto", start_date="2027-04-10", end_date="2027-04-11",
        days=2, party_size=2, budget=100000, currency="JPY",
    )


def test_user_can_force_an_unconfirmed_stop():
    plan = _plan()
    before = len(plan.item_index())
    log: list[str] = []
    changed = _apply(
        EditOp(op="add", name="Hong Kong Disneyland", day=2, start="13:00", end="17:00"),
        plan,
        _context(),
        Bundle(),
        log,
    )
    assert changed is True
    item = next(entry for entry in plan.days[1].items if entry.name == "Hong Kong Disneyland")
    assert item.data_status == "stale"
    assert "user-provided" in item.tags
    assert item.source_refs == []
    assert any(gap.kind == "user_provided" for gap in plan.data_quality.gaps)
    assert len(plan.item_index()) == before + 1


def test_user_provided_stop_satisfies_its_constraint():
    from app.plan.builder import sync_constraints

    plan = _plan()
    context = _context()
    context.hard_constraints = ["must include Hong Kong Disneyland"]
    sync_constraints(plan, context)
    assert plan.constraints and plan.constraints[0].status == "unknown"
    _apply(EditOp(op="add", name="Hong Kong Disneyland", day=2, start="13:00", end="17:00"), plan, context, Bundle(), [])
    sync_constraints(plan, context)
    assert plan.constraints[0].status == "pass"
    assert "Hong Kong Disneyland" in plan.constraints[0].evidence


def test_constraint_evidence_names_the_right_stop():
    from app.plan.builder import sync_constraints

    plan = _plan()
    # A second, unrelated museum must not satisfy a Disneyland constraint.
    plan.days[1].items.append(
        __import__("app.domain.models", fromlist=["PlanItem"]).PlanItem(
            id="palace", name="Hong Kong Palace Museum", start="16:00", end="17:00",
            source_refs=[], data_status="ok",
        )
    )
    context = _context()
    context.hard_constraints = ["must include Hong Kong Disneyland"]
    sync_constraints(plan, context)
    assert plan.constraints[0].status == "unknown"
    _apply(EditOp(op="add", name="Hong Kong Disneyland", day=2, start="13:00", end="17:00"), plan, context, Bundle(), [])
    sync_constraints(plan, context)
    assert plan.constraints[0].status == "pass"
    assert "Disneyland" in plan.constraints[0].evidence
    assert "Palace" not in plan.constraints[0].evidence
