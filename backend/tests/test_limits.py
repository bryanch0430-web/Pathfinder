"""Limits that grow with the number of cities in a turn, and the 30-day trip."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.application.limits import tool_cap_for, turn_deadline_for
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import PlanInvalidError
from app.domain.models import TripContext
from app.eval_efficiency import build_rig
from app.eval_metering import RunLedger
from app.infrastructure.observability import NoopTracer


def test_the_new_settings_defaults(settings: Settings) -> None:
    assert (settings.max_days, settings.max_cities) == (30, 8)
    assert (settings.segment_deadline_seconds, settings.segment_tool_calls) == (90.0, 100)


@pytest.mark.parametrize(
    ("segments", "deadline", "cap"), [(0, 180, 150), (1, 180, 150), (3, 360, 350), (8, 810, 850)]
)
def test_the_deadline_and_the_tool_cap_grow_with_the_cities(
    settings: Settings, segments: int, deadline: float, cap: int
) -> None:
    assert turn_deadline_for(settings, segments) == deadline
    assert tool_cap_for(settings, segments) == cap


def test_the_deadline_is_capped_and_a_cap_of_zero_stays_off(settings: Settings) -> None:
    slow = settings.model_copy(update={"segment_deadline_seconds": 400.0, "max_tool_calls_per_turn": 0})

    assert turn_deadline_for(slow, 8) == 900
    assert tool_cap_for(slow, 8) == 0


def _run(settings: Settings, days: int):
    async def main():
        ledger = RunLedger()
        try:
            rig = build_rig(settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8}), "Kyoto", ledger)
            start = date.today() + timedelta(days=30)
            context = TripContext(
                session_id="s1", destination="Kyoto", origin="Hong Kong", start_date=start,
                end_date=start + timedelta(days=days - 1), days=days, party_size=2, budget=90000, currency="HKD",
            )
            return await rig.plan_path.run(
                context=context, session_id="s1", tracer=NoopTracer(), emit=lambda *_: None,
                toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t"),
            )
        finally:
            ledger.close()

    return asyncio.run(main())


def test_a_thirty_day_trip_is_planned(settings: Settings) -> None:
    assert len(_run(settings, 30).days) == 30


def test_a_thirty_one_day_trip_is_refused_with_the_existing_message(settings: Settings) -> None:
    with pytest.raises(PlanInvalidError, match="Trips longer than 30 days are out of scope."):
        _run(settings, 31)


def test_a_running_deadline_can_be_given_more_time() -> None:
    from app.interface.sse import Deadline

    async def main() -> tuple[float, float]:
        deadline = Deadline(10)
        deadline.restart()
        before = deadline.at
        deadline.allow(5)
        return deadline.at - before, deadline.seconds

    assert asyncio.run(main()) == (5, 15)
