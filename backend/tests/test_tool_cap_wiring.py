"""``MAX_TOOL_CALLS_PER_TURN`` reaches every toolbox a turn builds.

``test_retry_budget`` shows that a ``Toolbox`` refuses the call after its cap. These tests show
that the setting gets there: a turn path that built its toolbox without ``max_calls`` would
quietly lose the cap, and nothing else would fail.

Offline: sandbox providers and the metered writer, like the other turn tests.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import EvalCase, utcnow
from app.eval_efficiency import accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger

PLAN = {"intent": "plan", "confidence": 0.95}
MODIFY = {"intent": "modify", "confidence": 0.95, "affected_parts": ["hotel"], "related_to_plan": True}
ASK = {"intent": "ask", "confidence": 0.95, "related_to_plan": True}


@pytest.fixture()
def built(monkeypatch: pytest.MonkeyPatch) -> list[Toolbox]:
    """Every toolbox the turn service and the freshness service build, in the order they build them."""
    boxes: list[Toolbox] = []

    class Recording(Toolbox):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            boxes.append(self)

    monkeypatch.setattr("app.application.orchestrator.Toolbox", Recording)
    monkeypatch.setattr("app.application.freshness.Toolbox", Recording)
    return boxes


def _run_turn(settings: Settings, route: dict, message: str, *, has_plan: bool, aged_forecast: bool = False) -> None:
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("guest-cap")
        context = case_context(EvalCase(id="cap", dataset="design", input="", destination="Kyoto"), session.id)
        ledger.store.save_context(context)
        if has_plan:
            plan = asyncio.run(accepted_plan(settings, context))
            if aged_forecast:
                aged = utcnow() - timedelta(seconds=settings.weather_fresh_seconds + 600)
                for day in plan.days:
                    day.weather.fetched_at = aged
            ledger.store.save_plan(plan, "Accepted plan")
        rig.llm.route = route

        async def turn() -> list:
            return [event async for event in rig.turns.handle_turn(session_id=session.id, message=message)]

        events = asyncio.run(turn())
    finally:
        ledger.close()
    errors = [event.data for event in events if event.event == "error"]
    assert not errors, errors


#: The setting and what the toolbox gets: 0 turns the cap off, so the toolbox gets no cap at all.
CAPS = pytest.mark.parametrize(("setting", "expected"), [(7, 7), (0, None)], ids=["cap-of-7", "cap-off"])


@CAPS
@pytest.mark.parametrize(
    ("path", "route", "message", "has_plan"),
    [
        ("plan", PLAN, "Plan my trip to Kyoto.", False),
        ("modify", MODIFY, "Swap the hotel for the other option.", True),
        ("ask", ASK, "What is there to see near the hotel?", True),
    ],
)
def test_each_turn_path_builds_its_toolbox_with_the_configured_cap(
    settings: Settings,
    built: list[Toolbox],
    path: str,
    route: dict,
    message: str,
    has_plan: bool,
    setting: int,
    expected: int | None,
) -> None:
    _run_turn(settings.model_copy(update={"max_tool_calls_per_turn": setting}), route, message, has_plan=has_plan)

    assert [(box.path, box.max_calls) for box in built if box.path == path] == [(path, expected)]


@CAPS
def test_refreshing_an_outdated_plan_builds_its_toolbox_with_the_configured_cap(
    settings: Settings, built: list[Toolbox], setting: int, expected: int | None
) -> None:
    _run_turn(
        settings.model_copy(update={"max_tool_calls_per_turn": setting}),
        ASK,
        "What will the weather be like on day 1?",
        has_plan=True,
        aged_forecast=True,
    )

    assert [(box.path, box.max_calls) for box in built if box.path == "refresh"] == [("refresh", expected)]
