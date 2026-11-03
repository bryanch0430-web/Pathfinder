"""Things that write a trip while a turn on it is running: the reopen refresh, a delete, a form save.

A turn reads the plan, works for seconds and saves the next version. Anything else that writes the same
trip in that time either makes the turn end in ``plan_conflict`` (CONC-G2), is undone by it (CONC-G9) or
is written back after it was removed (CONC-G3). Offline: the sandbox providers and the metered writer, with
a gate in the edit path to hold a turn open.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Awaitable, Callable

import pytest

import app.application.orchestrator as orchestrator
from app.config import Settings
from app.domain.models import EvalCase, TripPlan, utcnow
from app.eval_efficiency import Rig, accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger

SWAP_HOTEL = "Swap the hotel for the other option."


@pytest.fixture()
def quick_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})


@dataclass
class Scene:
    """One trip with an accepted plan (version 1) and an edit path that waits at a gate."""

    ledger: RunLedger
    rig: Rig
    session_id: str
    plan: TripPlan
    #: Days added to the freshness clock: 10 makes the forecast, fares and stays outdated.
    days_ahead: int = 0
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    gate: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def store(self):
        return self.ledger.store

    def hold_the_edit_path(self) -> None:
        """The edit path stops at a gate once it has been given the plan it works on."""
        original = self.rig.modify_path.run

        async def gated(**kwargs: Any):
            self.entered.set()
            await self.gate.wait()
            return await original(**kwargs)

        self.rig.modify_path.run = gated

    async def turn(self, message: str = SWAP_HOTEL) -> list:
        return [event async for event in self.rig.turns.handle_turn(session_id=self.session_id, message=message)]

    def versions(self) -> list[int]:
        return sorted(row["version"] for row in self.store.list_versions(self.session_id))


async def _scene(settings: Settings) -> Scene:
    ledger = RunLedger()
    rig = build_rig(settings, "Tokyo", ledger)
    session = ledger.store.create_session("guest-writers")
    context = case_context(EvalCase(id="w", dataset="design", input="", destination="Tokyo"), session.id)
    ledger.store.save_context(context)
    plan = await accepted_plan(settings, context)
    ledger.store.save_plan(plan, "Accepted plan")
    rig.llm.route = {"intent": "modify", "confidence": 0.95, "affected_parts": ["hotel"]}
    scene = Scene(ledger, rig, session.id, plan)
    rig.turns.freshness.clock = lambda: utcnow() + timedelta(days=scene.days_ahead)
    return scene


def _run(settings: Settings, scenario: Callable[[Scene], Awaitable[None]]) -> None:
    async def main() -> None:
        scene = await _scene(settings)
        try:
            await scenario(scene)
        finally:
            scene.ledger.close()

    asyncio.run(main())


def _errors(events: list) -> list[dict]:
    return [event.data for event in events if event.event == "error"]


# -- CONC-G2: the reopen refresh is a plan writer -----------------------------


def test_reopening_a_plan_while_a_turn_runs_leaves_the_next_version_to_the_turn(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        scene.hold_the_edit_path()
        turn = asyncio.create_task(scene.turn())
        await asyncio.wait_for(scene.entered.wait(), 30)
        scene.days_ahead = 10  # the plan is outdated for the page that opens now
        made = scene.ledger.made_total

        shown = await scene.rig.turns.freshness.on_reopen(scene.store.get_plan(scene.session_id))

        assert shown.version == 1 and scene.versions() == [1]
        assert scene.ledger.made_total == made, "a refresh that cannot be saved is not paid for"
        scene.days_ahead = 0
        scene.gate.set()
        events = await asyncio.wait_for(turn, 60)
        assert not _errors(events)
        assert scene.versions() == [1, 2]

    _run(quick_settings, scenario)


def test_a_reopen_that_loses_the_race_to_a_turn_shows_the_plan_the_turn_saved(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        scene.days_ahead = 10
        fetching, release = asyncio.Event(), asyncio.Event()
        freshness = scene.rig.turns.freshness
        fetch = freshness._refetch

        async def slow_fetch(*args: Any, **kwargs: Any):
            fetching.set()
            await release.wait()
            return await fetch(*args, **kwargs)

        freshness._refetch = slow_fetch
        reopen = asyncio.create_task(freshness.on_reopen(scene.store.get_plan(scene.session_id)))
        await asyncio.wait_for(fetching.wait(), 30)
        scene.days_ahead = 0  # for the turn nothing is outdated, so it does not refresh the plan itself

        events = await asyncio.wait_for(scene.turn(), 60)
        release.set()
        shown = await asyncio.wait_for(reopen, 60)

        stored = scene.store.get_plan(scene.session_id)
        assert not _errors(events)
        assert stored.version == 2 and stored.hotels[0].name != scene.plan.hotels[0].name
        assert shown.model_dump() == stored.model_dump()

    _run(quick_settings, scenario)


def test_a_reopen_does_not_save_when_a_turn_started_while_it_fetched(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        scene.days_ahead = 10
        fetching, release = asyncio.Event(), asyncio.Event()
        freshness = scene.rig.turns.freshness
        fetch = freshness._refetch

        async def slow_fetch(*args: Any, **kwargs: Any):
            fetching.set()
            await release.wait()
            return await fetch(*args, **kwargs)

        freshness._refetch = slow_fetch
        reopen = asyncio.create_task(freshness.on_reopen(scene.store.get_plan(scene.session_id)))
        await asyncio.wait_for(fetching.wait(), 30)
        scene.days_ahead = 0
        scene.hold_the_edit_path()
        turn = asyncio.create_task(scene.turn())
        await asyncio.wait_for(scene.entered.wait(), 30)

        release.set()
        shown = await asyncio.wait_for(reopen, 60)

        assert shown.version == 1 and scene.versions() == [1]
        scene.gate.set()
        events = await asyncio.wait_for(turn, 60)
        assert not _errors(events)
        assert scene.versions() == [1, 2]

    _run(quick_settings, scenario)


def test_a_reopen_with_no_turn_running_still_refreshes_and_saves_the_plan(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        scene.days_ahead = 10

        shown = await scene.rig.turns.freshness.on_reopen(scene.store.get_plan(scene.session_id))

        assert shown.version == 2
        assert scene.versions() == [1, 2]
        assert scene.store.get_plan(scene.session_id).model_dump() == shown.model_dump()

    _run(quick_settings, scenario)
