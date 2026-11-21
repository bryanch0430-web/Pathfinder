"""A/B on the shared visit-length table: pace only, then the table.

Arm A is today's schedule: every stop keeps the pace slot.
Arm B is the same slot passed through visit_minutes.
A stated length is the traveller's, and it wins in arm B.
"""

from datetime import date

from app.application.refine import refine_plan
from app.application.visit_length import STATED, unclassified, visit_minutes
from app.domain.models import DateRange, DayPlan, Destination, ItemType, PlanItem, SourceRef, TripContext, TripPlan

PACE = 90
MONDAY = date(2027, 4, 12)

# name, source types, stated minutes or None, minutes arm B must schedule
CASES = [
    ("Ocean Park", ["amusement_park"], None, 300),
    ("Ocean Park", [], None, 300),
    ("Hong Kong Disneyland", [], None, 300),
    ("A city zoo", ["zoo"], None, 300),
    ("Lion Rock trail", ["hiking_area"], None, 120),
    ("Kiyomizu-dera", ["place_of_worship"], None, 90),
    ("A small shrine", [], None, 90),
    ("7-Eleven", ["convenience_store"], None, 60),
    ("Ocean Park", ["amusement_park"], 60, 60),
    ("A viewpoint", [], None, 90),
]


def _arm_a(pace: int, stated: int | None) -> int:
    return stated if stated else pace


def test_arm_b_fixes_every_too_short_long_visit_and_arm_a_does_not() -> None:
    too_short_before = too_short_after = 0
    for name, types, stated, expect in CASES:
        before = _arm_a(PACE, stated)
        after = visit_minutes(name, types, PACE, stated=stated)
        assert after == expect
        if expect > PACE and stated is None:
            too_short_before += before < expect
            too_short_after += after < expect
    assert too_short_before == 5
    assert too_short_after == 0


def test_a_stop_the_table_cannot_classify_stays_on_the_pace_slot() -> None:
    assert unclassified("A viewpoint", [])
    assert visit_minutes("A viewpoint", [], PACE) == PACE


def test_refine_lengthens_ocean_park_and_leaves_a_stated_hour() -> None:
    def stop(item_id: str, name: str, start: str, end: str, **extra) -> PlanItem:
        return PlanItem(
            id=item_id, type=ItemType.ATTRACTION, name=name, start=start, end=end,
            source_refs=[SourceRef(title=name, url=f"https://example.test/{item_id}", source="google-places")],
            **extra,
        )

    plan = TripPlan(
        session_id="s",
        destination=Destination(name="Hong Kong"),
        dates=DateRange(start=MONDAY, end=MONDAY),
        days=[DayPlan(date=MONDAY, items=[
            stop("park", "Ocean Park", "09:00", "10:30"),
            stop("temple", "A small shrine", "15:00", "16:30"),
        ])],
    )
    refined, _, rounds = refine_plan(plan, TripContext(session_id="s", destination="Hong Kong"))
    park = next(item for item in refined.days[0].items if item.name == "Ocean Park")
    shrine = next(item for item in refined.days[0].items if item.name == "A small shrine")
    assert rounds >= 1
    assert park.end == "14:00"
    assert shrine.end == "16:30"

    asked = stop("park", "Ocean Park", "09:00", "10:00", tags=[STATED])
    held = TripPlan(
        session_id="s",
        destination=Destination(name="Hong Kong"),
        dates=DateRange(start=MONDAY, end=MONDAY),
        days=[DayPlan(date=MONDAY, items=[asked])],
    )
    refined, _, rounds = refine_plan(held, TripContext(session_id="s", destination="Hong Kong"))
    assert rounds == 0
    assert refined.days[0].items[0].end == "10:00"
