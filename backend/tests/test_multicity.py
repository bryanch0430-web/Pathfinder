from __future__ import annotations

from datetime import date, timedelta

from app.domain.models import (
    DateRange,
    DayPlan,
    Destination,
    PlanItem,
    TripContext,
    TripPlan,
    TripStop,
)
from app.plan.continuity import route_continuity_problems
from app.plan.multicity import _merge, segment_ranges


def _context() -> TripContext:
    return TripContext(
        session_id="multi-test",
        destination="Kyoto and Osaka",
        start_date="2027-04-10",
        end_date="2027-04-14",
        days=5,
        stops=[TripStop(city="Kyoto", nights=2), TripStop(city="Osaka", nights=2)],
    )


def _plan(city: str, start: date, end: date) -> TripPlan:
    days = []
    cursor = start
    index = 1
    while cursor <= end:
        days.append(
            DayPlan(
                date=cursor.isoformat(),
                title=city,
                items=[
                    PlanItem(
                        id=f"{city}-{index}",
                        type="attraction",
                        name=f"{city} stop {index}",
                        start="10:00",
                        end="11:00",
                    )
                ],
            )
        )
        cursor += timedelta(days=1)
        index += 1
    return TripPlan(
        trip_id=f"trip-{city}",
        session_id="multi-test",
        destination=Destination(name=city),
        dates=DateRange(start=start.isoformat(), end=end.isoformat()),
        days=days,
    )


def test_segment_ranges_cover_every_day_once():
    context = _context()
    ranges = segment_ranges(context)
    assert [span.city for span in ranges] == ["Kyoto", "Osaka"]
    assert ranges[0].start == date(2027, 4, 10)
    assert ranges[-1].end == date(2027, 4, 14)
    assert sum(span.days for span in ranges) == 5
    assert ranges[1].start == ranges[0].end + timedelta(days=1)


def test_merged_plan_has_ordered_city_segments_and_continuity():
    context = _context()
    ranges = segment_ranges(context)
    plan = _merge(
        [
            _plan("Kyoto", ranges[0].start, ranges[0].end),
            _plan("Osaka", ranges[1].start, ranges[1].end),
        ],
        ranges,
        context,
    )
    assert [day.city for day in plan.days] == ["Kyoto", "Kyoto", "Osaka", "Osaka", "Osaka"]
    assert [segment.destination["name"] for segment in plan.segments] == ["Kyoto", "Osaka"]
    assert not route_continuity_problems(plan, context)


def test_multi_city_builder_joins_segment_plans(monkeypatch):
    import asyncio

    from app.config import Settings
    from app.core.trace import TurnTrace
    from app.evidence.models import Bundle
    from app.plan import multicity
    from app.plan.agents import Gathered

    async def fake_gather(**kwargs):
        return Gathered(Bundle())

    async def fake_compose(*, context, **kwargs):
        start = date.fromisoformat(context.start_date)
        end = date.fromisoformat(context.end_date)
        return _plan(context.destination, start, end), None

    async def emit(*args, **kwargs):
        return None

    async def fake_aclose(self):
        return None

    monkeypatch.setattr(multicity, "gather", fake_gather)
    monkeypatch.setattr(multicity, "compose", fake_compose)

    class FakeLLM:
        async def aclose(self):
            return None

    monkeypatch.setattr(multicity, "LLMClient", lambda settings: FakeLLM())
    context = _context()
    plan = asyncio.run(
        multicity.build_multi_city_plan(
            providers=object(),
            settings=Settings(database_path="data/test.db"),
            context=context,
            trace=TurnTrace(request_id="test"),
            emit=emit,
        )
    )
    assert plan is not None
    assert len(plan.days) == 5
    assert [segment.destination["name"] for segment in plan.segments] == ["Kyoto", "Osaka"]
    assert not route_continuity_problems(plan, context)
