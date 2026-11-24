"""An accepted edit keeps every touched stop inside its opening hours (offline)."""

from __future__ import annotations

import asyncio
from datetime import date

from app.config import Settings
from app.domain.models import GeoPoint, SourceRef, TripContext
from app.evidence.models import Bundle, Evidence
from app.plan.builder import build_plan
from app.plan.editor import _apply, propose_edit
from app.plan.validator import hours_violation
from app.understand.schemas import DayChoice, EditDraft, EditOp, PlanDraft, StopChoice

SAT = date(2027, 4, 10)  # a Saturday


def _place(ref: str, name: str, *, hours: list[str], minutes: int = 90, lat: float = 34.65) -> Evidence:
    return Evidence(
        ref=ref,
        kind="attraction",
        name=name,
        address="Osaka",
        geo=GeoPoint(lat=lat, lng=135.43),
        source_refs=[SourceRef(title=name, url="https://e.test", source="Test")],
        opening_hours=hours,
        hours_stated=bool(hours),
        suggested_minutes=minutes,
    )


def _context() -> TripContext:
    return TripContext(
        session_id="s1",
        destination="Osaka",
        start_date="2027-04-10",
        end_date="2027-04-11",
        days=2,
        party_size=1,
        budget=100000,
        currency="JPY",
    )


def _bundle(aquarium_hours: list[str]) -> Bundle:
    bundle = Bundle()
    bundle.add(
        [
            _place("at-1", "Tsutenkaku Tower", hours=["10:00-22:00"], minutes=75),
            _place("at-2", "Dotonbori", hours=[], minutes=60, lat=34.6687),
            _place("at-3", "Osaka Aquarium Kaiyukan", hours=aquarium_hours, minutes=150),
        ],
        "test",
    )
    return bundle


def _plan(bundle: Bundle):
    draft = PlanDraft(
        summary="x",
        days=[
            DayChoice(
                date="2027-04-10",
                title="Arrival, Tsutenkaku and Dotonbori by night",
                stops=[
                    StopChoice(ref="at-1", start="19:00", end="20:15"),
                    StopChoice(ref="at-2", start="21:00", end="22:00"),
                ],
            ),
            DayChoice(date="2027-04-11", stops=[]),
        ],
    )
    return build_plan(
        context=_context(),
        bundle=bundle,
        draft=draft,
        disruptions=[],
        weather_by_date={},
        start=SAT,
        end=date(2027, 4, 11),
        settings=Settings(database_path="data/test.db"),
    )


class _FakeLLM:
    def __init__(self, draft: EditDraft) -> None:
        self.draft = draft

    async def json(self, **kwargs):
        return self.draft


def _edit(plan, bundle, draft: EditDraft):
    return asyncio.run(
        propose_edit(
            llm=_FakeLLM(draft),
            providers=None,
            settings=Settings(database_path="data/test.db"),
            context=_context(),
            plan=plan,
            bundle=bundle,
            message="edit",
            scope_label=None,
            scope_item_id=None,
        )
    )


def _swap(plan, bundle):
    old = plan.days[0].items[0]
    return _edit(
        plan, bundle, EditDraft(summary="Swapped the stop.", ops=[EditOp(op="replace", item_id=old.id, ref="at-3")])
    )


def test_swapped_stop_end_is_clamped_to_closing_time():  # 1(a)
    bundle = _bundle(["10:00-20:00"])
    outcome = _swap(_plan(bundle), bundle)
    aquarium = next(item for item in outcome.plan.days[0].items if item.name.startswith("Osaka Aquarium"))
    assert aquarium.start == "19:00"
    assert aquarium.end == "20:00"
    assert hours_violation(aquarium, SAT) is None


def test_stop_that_cannot_fit_is_flagged_not_hidden():  # 1(a)
    bundle = _bundle(["10:00-19:15"])
    outcome = _swap(_plan(bundle), bundle)
    assert any("Osaka Aquarium Kaiyukan" in line and "closes at 19:15" in line for line in outcome.log)
    hours = [check for check in outcome.plan.constraints if check.id == "hours"]
    assert hours and hours[0].status == "fail"


def test_validator_checks_the_end_and_google_style_hours():  # 1(a)
    plan = _plan(_bundle(["10:00-20:00"]))
    item = plan.days[0].items[0]
    item.opening_hours = ["Saturday: 10:00 AM – 8:00 PM", "Sunday: Closed"]
    item.hours_status = "stated"
    item.start, item.end = "19:00", "20:15"
    assert "closes at 20:00" in (hours_violation(item, SAT) or "")
    item.end = "20:00"
    assert hours_violation(item, SAT) is None
    assert hours_violation(item, date(2027, 4, 11)) == "closed that day"


def test_swapped_in_stop_takes_its_own_visit_length():  # 1(b)
    bundle = _bundle(["10:00-20:00"])
    plan = _plan(bundle)
    old = plan.days[0].items[0]
    old.start, old.end = "10:30", "11:30"
    _apply(EditOp(op="replace", item_id=old.id, ref="at-3"), plan, _context(), bundle, [])
    aquarium = next(item for item in plan.days[0].items if item.name.startswith("Osaka Aquarium"))
    assert (aquarium.start, aquarium.end) == ("10:30", "13:00")  # 150 min, not the old 60


def test_day_title_naming_a_removed_stop_is_replaced():  # 1(c)
    bundle = _bundle(["10:00-20:00"])
    outcome = _swap(_plan(bundle), bundle)
    title = outcome.plan.days[0].title
    assert "Tsutenkaku" not in title
    assert "Osaka Aquarium Kaiyukan" in title


def test_day_title_is_kept_when_its_stops_remain():  # 1(c)
    bundle = _bundle(["10:00-20:00"])
    outcome = _edit(
        _plan(bundle), bundle, EditDraft(summary="Added.", ops=[EditOp(op="add", day=1, ref="at-3", start="12:00")])
    )
    assert outcome.plan.days[0].title == "Arrival, Tsutenkaku and Dotonbori by night"
