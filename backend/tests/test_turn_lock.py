"""The turn lock of a trip: closing a turn, a deadline that starts late, a path that will not stop, two loops.

Offline: the sandbox providers and the metered writer, with a gate in the edit path where a turn has to be
held open.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import pytest
from fastapi.testclient import TestClient

import app.application.orchestrator as orchestrator
from app.config import Settings
from app.domain.models import EvalCase
from app.eval_efficiency import Rig, accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger
from app.interface.api import create_app
from app.interface.sse import KEEPALIVE, TIMED_OUT, Deadline, paced

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


# -- CONC-G11: a path that will not stop -------------------------------------


def test_a_path_that_ignores_cancellation_does_not_hold_the_trip_after_a_disconnect(
    quick_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator, "SETTLE_GRACE_SECONDS", 0.05)

    async def scenario(scene: Scene) -> None:
        turns = scene.rig.turns
        original = turns.modify_path.run
        released = asyncio.Event()
        entered = asyncio.Event()

        async def stubborn(**kwargs: Any):
            entered.set()
            while not released.is_set():
                try:
                    await released.wait()
                except asyncio.CancelledError:
                    pass  # a path that swallows its cancellation
            return await original(**kwargs)

        turns.modify_path.run = stubborn
        events = paced(
            turns.handle_turn(session_id=scene.session_id, message=SWAP_HOTEL), keepalive_seconds=0.05, deadline_seconds=60
        )
        try:
            async for item in events:
                if item is KEEPALIVE:
                    break
            assert entered.is_set()

            await asyncio.wait_for(events.aclose(), 4)  # the stream is closed: the client went away

            assert not turns.session_busy(scene.session_id), "the lock is still held by the path that will not stop"
            turns.modify_path.run = original
            assert not _errors(await asyncio.wait_for(scene.turn(), 30))
        finally:
            released.set()  # let the abandoned path end
        await asyncio.sleep(0)

    _run(quick_settings, scenario)


# -- CONC-G6: the deadline of a turn starts when the turn does ----------------


def test_a_turn_reports_that_it_waits_and_when_it_starts(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        turns = scene.rig.turns
        calls: list[str] = []
        hooks = {"on_wait": lambda: calls.append("wait"), "on_start": lambda: calls.append("start")}

        assert not _errors(await _collect(turns.handle_turn(session_id=scene.session_id, message=SWAP_HOTEL, **hooks)))
        assert calls == ["start"], "a turn that does not have to wait does not report waiting"

        calls.clear()
        scene.hold_the_edit_path()
        first = asyncio.create_task(scene.turn())
        await asyncio.wait_for(scene.entered.wait(), 30)
        second = asyncio.create_task(
            _collect(turns.handle_turn(session_id=scene.session_id, message=SWAP_HOTEL, **hooks))
        )
        for _ in range(20):  # the second turn can only wait: the first one holds the lock
            await asyncio.sleep(0)
        assert calls == ["wait"]

        scene.gate.set()
        await asyncio.wait_for(first, 60)
        events = await asyncio.wait_for(second, 60)

        assert calls == ["wait", "start"]
        assert not _errors(events)

    _run(quick_settings, scenario)


async def _collect(stream: Any) -> list:
    return [event async for event in stream]


def test_a_paced_stream_does_not_count_the_time_its_source_waits_for_its_turn() -> None:
    async def scenario() -> tuple[list, list]:
        async def queued_then_working(deadline: Deadline):
            deadline.pause()
            await asyncio.sleep(0.7)  # waiting for its turn: longer than the deadline
            deadline.restart()
            yield "working"
            await asyncio.sleep(0.2)  # its own work: shorter than the deadline
            yield "done"

        async def never_restarted():
            await asyncio.sleep(0.7)
            yield "late"

        deadline = Deadline(0.5)
        restarted = [item async for item in paced(queued_then_working(deadline), keepalive_seconds=0.1, deadline_seconds=deadline)]
        plain = [item async for item in paced(never_restarted(), keepalive_seconds=0.1, deadline_seconds=0.5)]
        return restarted, plain

    restarted, plain = asyncio.run(scenario())

    assert [item for item in restarted if item is not KEEPALIVE] == ["working", "done"]
    assert plain[-1] is TIMED_OUT and "late" not in plain


class QueuedThenWorking:
    """A turn service whose turn waits for the trip longer than the deadline, then works for less."""

    def __init__(self) -> None:
        self.hooks_given = False

    async def handle_turn(
        self,
        *,
        on_wait: Callable[[], None] | None = None,
        on_start: Callable[[], None] | None = None,
        **_: object,
    ):
        self.hooks_given = on_wait is not None and on_start is not None
        on_wait()
        await asyncio.sleep(0.7)
        on_start()
        yield SimpleNamespace(event="progress", data={"progress": 5})
        await asyncio.sleep(0.2)
        yield SimpleNamespace(event="done", data={"intent": "modify"})


def test_the_chat_stream_counts_the_deadline_from_the_start_of_the_turn(settings: Settings) -> None:
    settings = settings.model_copy(update={"turn_deadline_seconds": 0.5, "sse_keepalive_seconds": 0.1})
    turns = QueuedThenWorking()
    with TestClient(create_app(settings)) as client:
        client.app.state.container.turn_service = turns
        body = client.post(
            "/api/turn/stream", json={"message": "Swap the hotel.", "locale": "en", "client_token": "guest-lock"}
        ).text

    frames = [frame for frame in body.split("\n\n") if frame and not frame.startswith(":")]
    assert turns.hooks_given
    assert [frame.split("\n")[0] for frame in frames] == ["event: progress", "event: done"], body
    assert "turn_timeout" not in body
    assert json.loads(frames[-1].split("data: ", 1)[1])["intent"] == "modify"


# -- CONC-G12: two event loops on one trip -------------------------------------


def test_a_turn_from_another_event_loop_on_a_busy_trip_is_refused_and_does_not_hang(quick_settings: Settings) -> None:
    scene = asyncio.run(_scene(quick_settings))  # built in a loop of its own
    holding, release = threading.Event(), threading.Event()
    original = scene.rig.modify_path.run

    async def hold(**kwargs: Any):
        holding.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return await original(**kwargs)

    scene.rig.modify_path.run = hold
    first: dict[str, list] = {}
    thread = threading.Thread(target=lambda: first.update(events=asyncio.run(scene.turn())))
    thread.start()
    try:
        assert holding.wait(30)

        async def from_this_loop() -> list:
            return await asyncio.wait_for(scene.turn(), 10)

        with pytest.raises(RuntimeError, match="event loop"):
            asyncio.run(from_this_loop())
    finally:
        release.set()
        thread.join(60)
        scene.ledger.close()

    assert not thread.is_alive() and not _errors(first["events"])
    assert scene.rig.turns.session_busy(scene.session_id) is False


def test_turns_on_different_trips_may_run_in_different_event_loops(quick_settings: Settings) -> None:
    scene = asyncio.run(_scene(quick_settings))
    store = scene.ledger.store
    other = store.create_session("guest-lock")
    store.save_context(store.get_context(scene.session_id).model_copy(update={"session_id": other.id}))
    store.save_plan(store.get_plan(scene.session_id).model_copy(deep=True, update={"session_id": other.id}), "Accepted plan")
    holding, release = threading.Event(), threading.Event()
    original = scene.rig.modify_path.run

    async def hold(**kwargs: Any):
        if kwargs["plan"].session_id == scene.session_id:
            holding.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        return await original(**kwargs)

    scene.rig.modify_path.run = hold
    thread = threading.Thread(target=lambda: asyncio.run(scene.turn()))
    thread.start()
    try:
        assert holding.wait(30)
        events = asyncio.run(_collect(scene.rig.turns.handle_turn(session_id=other.id, message=SWAP_HOTEL)))
    finally:
        release.set()
        thread.join(60)
        scene.ledger.close()

    assert not _errors(events)
