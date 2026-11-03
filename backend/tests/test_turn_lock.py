"""The turn lock of a trip: closing a turn, a deadline that starts late, a path that will not stop, two loops.

Offline: the sandbox providers and the metered writer, with a gate in the edit path where a turn has to be
held open.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import pytest

from app.config import Settings
from app.domain.models import EvalCase
from app.eval_efficiency import Rig, accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger

SWAP_HOTEL = "Swap the hotel for the other option."


@pytest.fixture()
def quick_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})


@dataclass
class Scene:
    """One trip with an accepted plan and an edit path that can be held at a gate."""

    ledger: RunLedger
    rig: Rig
    session_id: str
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    gate: asyncio.Event = field(default_factory=asyncio.Event)

    def hold_the_edit_path(self) -> None:
        original = self.rig.modify_path.run

        async def gated(**kwargs: Any):
            self.entered.set()
            await self.gate.wait()
            return await original(**kwargs)

        self.rig.modify_path.run = gated

    async def turn(self, message: str = SWAP_HOTEL) -> list:
        return [event async for event in self.rig.turns.handle_turn(session_id=self.session_id, message=message)]

    def versions(self) -> list[int]:
        return sorted(row["version"] for row in self.ledger.store.list_versions(self.session_id))


async def _scene(settings: Settings) -> Scene:
    ledger = RunLedger()
    rig = build_rig(settings, "Tokyo", ledger)
    session = ledger.store.create_session("guest-lock")
    context = case_context(EvalCase(id="l", dataset="design", input="", destination="Tokyo"), session.id)
    ledger.store.save_context(context)
    ledger.store.save_plan(await accepted_plan(settings, context), "Accepted plan")
    rig.llm.route = {"intent": "modify", "confidence": 0.95, "affected_parts": ["hotel"]}
    return Scene(ledger, rig, session.id)


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


# -- CONC-G10: closing the real turn ------------------------------------------


def test_closing_a_turn_after_its_first_event_ends_quietly_and_frees_the_trip(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        turns = scene.rig.turns
        turn = turns.handle_turn(session_id=scene.session_id, message=SWAP_HOTEL)
        first = await turn.__anext__()
        assert first.event == "route" and turns.session_busy(scene.session_id)

        await turn.aclose()  # used to raise RuntimeError: async generator ignored GeneratorExit

        assert not turns.session_busy(scene.session_id)
        assert not _errors(await asyncio.wait_for(scene.turn(), 30))
        assert scene.versions() == [1, 2]

    _run(quick_settings, scenario)


def test_closing_a_turn_that_was_never_started_takes_no_lock(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        turn = scene.rig.turns.handle_turn(session_id=scene.session_id, message=SWAP_HOTEL)

        await turn.aclose()

        assert not scene.rig.turns.session_busy(scene.session_id)

    _run(quick_settings, scenario)


def test_a_turn_that_runs_to_the_end_still_sends_its_trace_and_done(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        events = await scene.turn()

        assert [event.event for event in events][-2:] == ["trace", "done"]

    _run(quick_settings, scenario)


def test_closing_a_turn_while_its_edit_path_works_stops_the_path(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        turns = scene.rig.turns
        stopped = asyncio.Event()
        original = turns.modify_path.run

        async def working(**kwargs: Any):
            kwargs["emit"]("think", {"text": "working on it"})  # an event the turn passes on while the path runs
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                stopped.set()
                raise
            return await original(**kwargs)

        turns.modify_path.run = working
        turn = turns.handle_turn(session_id=scene.session_id, message=SWAP_HOTEL)
        async for event in turn:
            if event.event == "think":
                break

        await turn.aclose()

        assert stopped.is_set(), "the path went on working after the turn was closed"
        assert not turns.session_busy(scene.session_id)

    _run(quick_settings, scenario)
