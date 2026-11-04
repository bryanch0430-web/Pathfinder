"""A trip moved to other weekdays says which hours it can no longer vouch for, and locked stops that cannot reach each
other in the time between them are named.

D2-7: a stop carries only the hours line of the weekday it was planned for. After a same-length shift to other weekdays
that line is for another day: it is dropped (the hours are unknown) and the plan says for which stops, and a stop whose
stored hours say it is closed on its new day is named with that day.
D2-8: locked and confirmed stops put back on one day sit back to back; their own transfers do not fit. The plan says so.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from app.application.llm_gateway import LLMGateway
from app.application.modification import ModifyPath
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import TripContext, TripPlan
from app.evaluation import SandboxPlaces, SandboxRegistry
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService
from tests.test_modify_targets import _build

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class _Weekly(SandboxPlaces):
    """Closed on Mondays and open 9-5 on the other days, in Google's one-line-per-weekday shape."""

    async def search(self, **kwargs):
        rows = await super().search(**kwargs)
        for row in rows:
            if row.category == "Attraction":
                row.opening_hours = [f"{d}: Closed" if d == "Monday" else f"{d}: 9:00 AM - 5:00 PM" for d in DAYS]
        return rows


class _Rule(SandboxPlaces):
    """The weekdays-only rule in OpenStreetMap's shape: one line that covers the whole week."""

    async def search(self, **kwargs):
        rows = await super().search(**kwargs)
        for row in rows:
            if row.category == "Attraction":
                row.opening_hours = ["Mo-Fr 09:00-17:00; Sa,Su off"]
        return rows


def _next(weekday: int) -> date:
    start = date.today() + timedelta(days=30)
    return start + timedelta(days=(weekday - start.weekday()) % 7)


def _shifted(settings: Settings, source: SandboxPlaces, *, start: date, shift: int) -> tuple[TripPlan, TripPlan]:
    gateway, planner, plan_path, context, _ = _build(settings)
    plan_path.registry.places = [source]
    context.start_date, context.end_date, context.days = start, start + timedelta(days=2), 3
    toolbox = Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t")
    plan = asyncio.run(
        plan_path.run(context=context, session_id="s", toolbox=toolbox, tracer=NoopTracer(), emit=lambda *_: None)
    )
    context.start_date += timedelta(days=shift)
    context.end_date += timedelta(days=shift)
    modify = ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway)
    updated, _ = asyncio.run(
        modify.run(
            plan=plan,
            context=context,
            message="move the trip a day earlier",
            route_parts=["dates"],
            toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    return plan, updated


def test_hours_stated_for_other_weekdays_are_dropped_and_the_plan_says_which_stops_they_were(settings: Settings) -> None:
    plan, updated = _shifted(settings, _Weekly("Kyoto"), start=_next(6), shift=-1)  # Sun-Tue -> Sat-Mon

    assert updated.dates.start == plan.dates.start - timedelta(days=1)
    wrong = [
        (day.date, item.name, line)
        for day in updated.days
        for item in day.items
        for line in item.opening_hours
        if line.split(":")[0] != DAYS[day.date.weekday()]
    ]
    assert not wrong, wrong
    cleared = [i.name for d in updated.days for i in d.items if i.hours_status == "unknown" and i.type.value == "attraction"]
    assert cleared, "the shifted stops have no hours for their new weekday"
    gaps = [g.message for g in updated.data_quality.gaps if g.section == "attractions" and "Opening hours" in g.message]
    assert len(gaps) == 1 and all(name in gaps[0] for name in cleared), gaps


def test_a_shift_by_a_whole_week_keeps_the_hours_lines(settings: Settings) -> None:
    plan, updated = _shifted(settings, _Weekly("Kyoto"), start=_next(6), shift=-7)

    assert [i.opening_hours for d in updated.days for i in d.items] == [i.opening_hours for d in plan.days for i in d.items]
    assert not [g for g in updated.data_quality.gaps if "Opening hours" in g.message]


def test_a_stop_whose_stored_hours_say_it_is_closed_on_its_new_day_is_named_with_the_day(settings: Settings) -> None:
    plan, updated = _shifted(settings, _Rule("Kyoto"), start=_next(0), shift=-2)  # Mon-Wed -> Sat-Mon

    closed = [g.message for g in updated.data_quality.gaps if g.section == "attractions" and "is closed on" in g.message]
    assert closed, [g.message for g in updated.data_quality.gaps]
    assert any("Saturday" in text for text in closed)
    for text in closed:
        assert any(item.name in text for day in updated.days for item in day.items)


# -- D2-8 ---------------------------------------------------------------------------------------------------------


def test_locked_stops_put_back_on_one_day_whose_transfers_do_not_fit_are_named(settings: Settings) -> None:
    s = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    registry = SandboxRegistry(s, "Tokyo")
    gateway = LLMGateway(NullLLM(), PromptLibrary(s), NoopTracer(), s)
    planner = Planner(gateway, s, FxService())
    plan_path = PlanPath(planner=planner, registry=registry, settings=s)
    start = date.today() + timedelta(days=30)
    context = TripContext(
        session_id="s", destination="Tokyo", origin="Hong Kong", start_date=start, end_date=start + timedelta(days=4),
        days=5, party_size=2, budget=400000, currency="JPY",
    )
    plan = asyncio.run(
        plan_path.run(
            context=context, session_id="s", toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(), emit=lambda *_: None,
        )
    )
    for number in (3, 4, 5):  # the traveller locked the first stop of days 3, 4 and 5
        plan.days[number - 1].items[0].locked = True
    context.end_date, context.days = start + timedelta(days=2), 3
    modify = ModifyPath(planner=planner, plan_path=plan_path, settings=s, gateway=gateway)
    updated, _ = asyncio.run(
        modify.run(
            plan=plan, context=context, message="make it 3 days", route_parts=["dates"],
            toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t"), tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )

    def minutes(value: str) -> int:
        return int(value[:2]) * 60 + int(value[3:])

    tight = []
    for number, day in enumerate(updated.days, start=1):
        ordered = sorted(day.items, key=lambda item: item.start)
        for before, after in zip(ordered, ordered[1:]):
            if after.transfer and after.transfer.from_item_id == before.id:
                if minutes(after.start) < minutes(before.end) + after.transfer.minutes + after.transfer.delay_minutes:
                    tight.append((number, before.name, after.name))
    assert tight, "the scenario of the report: locked stops from removed days stacked on day 3"
    said = [g.message for g in updated.data_quality.gaps if g.section == "transfers"]
    for number, before, after in tight:
        assert any(f"Day {number}" in text and before in text and after in text for text in said), (number, before, after, said)
