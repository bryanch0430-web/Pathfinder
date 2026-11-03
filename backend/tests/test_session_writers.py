"""Things that write a trip while a turn on it is running: the reopen refresh, a delete, a form save.

A turn reads the plan, works for seconds and saves the next version. Anything else that writes the same
trip in that time either makes the turn end in ``plan_conflict`` (CONC-G2), is undone by it (CONC-G9) or
is written back after it was removed (CONC-G3). Offline: the sandbox providers and the metered writer, with
a gate in the edit path to hold a turn open.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx
import pytest

import app.application.orchestrator as orchestrator
from app.config import Settings
from app.container import Container
from app.domain.models import EvalCase, Intent, RouteDecision, TripPlan, utcnow
from app.eval_efficiency import Rig, accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger
from app.interface.api import create_app

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


UNREACHABLE = "The source could not be reached; the earlier data is kept."


def test_a_reopen_that_refreshed_nothing_shows_the_plan_a_turn_saved_meanwhile(quick_settings: Settings) -> None:
    """RB-B-4: the sources were down, so the refresh was empty, and the page still got the old version."""

    async def scenario(scene: Scene) -> None:
        scene.days_ahead = 10
        fetching, release = asyncio.Event(), asyncio.Event()
        freshness = scene.rig.turns.freshness

        async def unreachable(*args: Any, **kwargs: Any):
            fetching.set()
            await release.wait()
            return UNREACHABLE, None  # (note, agent result): what _refetch answers

        freshness._refetch = unreachable
        reopen = asyncio.create_task(freshness.on_reopen(scene.store.get_plan(scene.session_id)))
        await asyncio.wait_for(fetching.wait(), 30)
        scene.days_ahead = 0  # for the turn nothing is outdated, so it does not refresh the plan itself

        events = await asyncio.wait_for(scene.turn(), 60)
        release.set()
        shown = await asyncio.wait_for(reopen, 60)

        stored = scene.store.get_plan(scene.session_id)
        assert not _errors(events)
        assert stored.version == 2 and scene.versions() == [1, 2]
        assert shown.version == 2 and shown.model_dump() == stored.model_dump()

    _run(quick_settings, scenario)


def test_a_reopen_that_refreshed_nothing_still_flags_the_sections_it_could_not_refresh(
    quick_settings: Settings,
) -> None:
    async def scenario(scene: Scene) -> None:
        scene.days_ahead = 10

        async def unreachable(*args: Any, **kwargs: Any):
            return UNREACHABLE, None  # (note, agent result): what _refetch answers

        scene.rig.turns.freshness._refetch = unreachable

        shown = await scene.rig.turns.freshness.on_reopen(scene.store.get_plan(scene.session_id))

        assert shown.version == 1 and scene.versions() == [1]
        assert {(mark.section, mark.status) for mark in shown.data_quality.freshness} == {
            ("weather", "outdated"),
            ("tickets", "outdated"),
            ("hotels", "outdated"),
        }

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


# -- CONC-G3: a trip deleted while a turn runs --------------------------------


def test_a_trip_deleted_while_a_turn_runs_is_not_written_back_by_the_turn(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        scene.hold_the_edit_path()
        turn = asyncio.create_task(scene.turn())
        await asyncio.wait_for(scene.entered.wait(), 30)

        deleted = scene.store.delete_session(scene.session_id)  # what DELETE /api/journeys/{id} runs
        scene.gate.set()
        events = await asyncio.wait_for(turn, 60)

        assert deleted["sessions"] == 1 and deleted["plans"] == 1
        assert not _errors(events)
        assert scene.store.get_session(scene.session_id) is None
        assert scene.versions() == []
        assert scene.store.list_messages(scene.session_id, limit=20) == []
        assert scene.store.get_context(scene.session_id) is None

    _run(quick_settings, scenario)


def test_a_turn_on_a_trip_that_is_still_there_keeps_what_it_wrote(quick_settings: Settings) -> None:
    async def scenario(scene: Scene) -> None:
        events = await scene.turn()

        assert not _errors(events)
        assert scene.versions() == [1, 2]
        roles = [message.role for message in scene.store.list_messages(scene.session_id, limit=20)]
        assert roles == ["user", "assistant"]

    _run(quick_settings, scenario)


@asynccontextmanager
async def _api(
    settings: Settings, token: str = "guest-writers"
) -> AsyncIterator[tuple[Container, httpx.AsyncClient, str]]:
    """The real app in this event loop, with one trip that has an accepted plan and a gated edit path.

    The edit path and the router are scripted (no network, no model); everything else is the real
    service graph behind the real routes.
    """
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        container: Container = app.state.container
        transport = httpx.ASGITransport(app=app)
        headers = {"X-Client-Token": token}
        async with httpx.AsyncClient(transport=transport, base_url="http://pathfinder.test", headers=headers) as client:
            created = await client.post("/api/journeys", json={"client_token": token})
            session_id = created.json()["session"]["id"]
            context = case_context(EvalCase(id="api", dataset="design", input="", destination="Tokyo"), session_id)
            container.store.save_context(context)
            container.store.save_plan(await accepted_plan(settings, context), "Accepted plan")

            async def route(**_: Any) -> RouteDecision:
                return RouteDecision(intent=Intent.MODIFY, confidence=0.95, reason_code="test", affected_parts=["hotel"])

            container.turn_service.router.route = route
            yield container, client, session_id


def _edit_that_waits(container: Container, entered: asyncio.Event, gate: asyncio.Event) -> None:
    async def run(**kwargs: Any):
        entered.set()
        await gate.wait()
        updated = kwargs["plan"].model_copy(deep=True)
        updated.version += 1
        updated.updated_at = utcnow()
        updated.change_summary = "Swapped the hotel."
        return updated, [{"kind": "hotel", "from": "A", "to": "B"}]

    container.turn_service.modify_path.run = run


def test_deleting_a_trip_through_the_api_while_its_turn_runs_leaves_nothing_behind(quick_settings: Settings) -> None:
    async def scenario() -> None:
        async with _api(quick_settings) as (container, client, session_id):
            entered, gate = asyncio.Event(), asyncio.Event()
            _edit_that_waits(container, entered, gate)
            body = {"session_id": session_id, "message": SWAP_HOTEL, "client_token": "guest-writers"}
            stream = asyncio.create_task(client.post("/api/turn/stream", json=body))
            await asyncio.wait_for(entered.wait(), 30)

            deleted = await client.delete(f"/api/journeys/{session_id}")
            gate.set()
            response = await asyncio.wait_for(stream, 60)

            assert deleted.status_code == 200 and deleted.json()["deleted"]["sessions"] == 1
            assert response.status_code == 200 and "event: error" not in response.text
            store = container.store
            assert store.get_session(session_id) is None
            assert store.list_versions(session_id) == []
            assert store.list_messages(session_id, limit=20) == []
            assert (await client.get(f"/api/journeys/{session_id}")).status_code == 404

    asyncio.run(scenario())


# -- RB-B-1: a message that waited for a trip that was deleted meanwhile ------

PLAN_MESSAGE = "Plan a 3 day trip to Tokyo from Hong Kong for 2 people with a budget of 3000 USD next month."
NOTHING_STORED = {"journeys": 0, "turns": 0, "itineraries": 0, "journey_state": 0}


def _stored(path) -> dict[str, int]:
    """How many rows the database holds in the tables of a trip, whoever owns them."""
    with closing(sqlite3.connect(path)) as conn:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in NOTHING_STORED}


@pytest.mark.parametrize(
    ("message", "route"),
    [
        (SWAP_HOTEL, {"intent": "modify", "confidence": 0.95, "affected_parts": ["hotel"]}),
        (PLAN_MESSAGE, {"intent": "plan", "confidence": 0.95}),
    ],
    ids=["an edit", "a new plan"],
)
def test_a_message_that_waited_for_a_trip_deleted_meanwhile_ends_with_an_error_and_makes_no_trip(
    quick_settings: Settings, message: str, route: dict[str, Any]
) -> None:
    async def scenario(scene: Scene) -> None:
        scene.hold_the_edit_path()
        running = asyncio.create_task(scene.turn())
        await asyncio.wait_for(scene.entered.wait(), 30)
        scene.rig.llm.route = route  # the message that waits is routed after the delete
        waiting = asyncio.Event()
        queued = asyncio.create_task(
            _collect(
                scene.rig.turns.handle_turn(session_id=scene.session_id, message=message, on_wait=waiting.set)
            )
        )
        await asyncio.wait_for(waiting.wait(), 30)

        scene.store.delete_session(scene.session_id)  # what DELETE /api/journeys/{id} runs
        scene.gate.set()
        await asyncio.wait_for(running, 60)
        events = await asyncio.wait_for(queued, 60)

        assert _stored(scene.store.path) == NOTHING_STORED, "the waiting message made a trip nobody can open"
        assert [event.event for event in events] == ["error"]
        assert events[0].data["code"] == "session_not_found" and events[0].data["retryable"] is False
        assert "no longer exists" in events[0].data["message"] and "nothing was changed" in events[0].data["message"]

    _run(quick_settings, scenario)


@pytest.mark.parametrize("session_id", ["sess_never_there", ""])
def test_a_turn_on_a_trip_that_does_not_exist_ends_with_an_error_and_writes_nothing(
    quick_settings: Settings, session_id: str
) -> None:
    async def scenario(scene: Scene) -> None:
        before = _stored(scene.store.path)
        assert before == {"journeys": 1, "turns": 0, "itineraries": 1, "journey_state": 1}

        events = await _collect(scene.rig.turns.handle_turn(session_id=session_id, message=SWAP_HOTEL))

        assert _stored(scene.store.path) == before
        assert [event.event for event in events] == ["error"]
        assert events[0].data["code"] == "session_not_found"
        assert not scene.rig.turns.session_busy(session_id)

    _run(quick_settings, scenario)


def test_a_second_message_on_a_trip_that_is_deleted_while_it_waits_gets_an_error_through_the_api(
    quick_settings: Settings,
) -> None:
    async def scenario() -> None:
        async with _api(quick_settings) as (container, client, session_id):
            entered, gate, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
            _edit_that_waits(container, entered, gate)
            turns = container.turn_service
            handle = turns.handle_turn

            def watching(**kwargs: Any):
                given = kwargs["on_wait"]
                return handle(**{**kwargs, "on_wait": lambda: (waiting.set(), given())})

            turns.handle_turn = watching
            body = {"session_id": session_id, "message": SWAP_HOTEL, "client_token": "guest-writers"}
            first = asyncio.create_task(client.post("/api/turn/stream", json=body))
            await asyncio.wait_for(entered.wait(), 30)
            second = asyncio.create_task(client.post("/api/turn/stream", json=body))
            await asyncio.wait_for(waiting.wait(), 30)

            deleted = await client.delete(f"/api/journeys/{session_id}")
            gate.set()
            await asyncio.wait_for(first, 60)
            response = await asyncio.wait_for(second, 60)

            assert deleted.status_code == 200 and deleted.json()["deleted"]["sessions"] == 1
            assert _stored(container.store.path) == NOTHING_STORED
            assert response.status_code == 200
            assert "event: error" in response.text and "session_not_found" in response.text
            assert "event: done" not in response.text and "event: plan" not in response.text
            assert (await client.get(f"/api/journeys/{session_id}")).status_code == 404

    asyncio.run(scenario())


async def _collect(stream: Any) -> list:
    return [event async for event in stream]


# -- CONC-G9: the trip form saved while the message is being understood -------


def _understanding_that_waits(monkeypatch: pytest.MonkeyPatch, entered: asyncio.Event, gate: asyncio.Event) -> None:
    """The model call inside the understand step takes a while."""
    real = orchestrator.understand

    async def slow_understand(*args: Any, **kwargs: Any):
        entered.set()
        await gate.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "understand", slow_understand)


def test_a_form_saved_while_the_message_is_understood_is_kept_next_to_what_the_message_said(
    quick_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(scene: Scene) -> None:
        entered, gate = asyncio.Event(), asyncio.Event()
        _understanding_that_waits(monkeypatch, entered, gate)
        turn = asyncio.create_task(scene.turn("Swap the hotel for the other option for 4 people."))
        await asyncio.wait_for(entered.wait(), 30)

        # What PATCH /api/journeys/{id}/state does: read the stored form, change a field, save it.
        form = scene.store.get_context(scene.session_id)
        form.budget = 777777
        form.preferences = [*form.preferences, "quiet evenings"]
        scene.store.save_context(form)
        gate.set()
        events = await asyncio.wait_for(turn, 60)

        saved = scene.store.get_context(scene.session_id)
        assert not _errors(events)
        assert saved.budget == 777777
        assert "quiet evenings" in saved.preferences
        assert saved.party_size == 4

    _run(quick_settings, scenario)


def test_a_turn_adds_to_the_preferences_that_were_saved_meanwhile(
    quick_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(scene: Scene) -> None:
        entered, gate = asyncio.Event(), asyncio.Event()
        _understanding_that_waits(monkeypatch, entered, gate)
        turn = asyncio.create_task(scene.turn("We are vegetarian. Swap the hotel for the other option."))
        await asyncio.wait_for(entered.wait(), 30)

        form = scene.store.get_context(scene.session_id)
        form.preferences = ["quiet evenings"]  # the form replaced the list
        scene.store.save_context(form)
        gate.set()
        await asyncio.wait_for(turn, 60)

        assert scene.store.get_context(scene.session_id).preferences == ["quiet evenings", "vegetarian"]

    _run(quick_settings, scenario)


def test_patching_the_form_through_the_api_while_its_turn_is_understood_is_kept(
    quick_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        async with _api(quick_settings) as (container, client, session_id):
            edit_may_finish = asyncio.Event()
            edit_may_finish.set()
            _edit_that_waits(container, asyncio.Event(), edit_may_finish)
            entered, gate = asyncio.Event(), asyncio.Event()
            _understanding_that_waits(monkeypatch, entered, gate)
            body = {"session_id": session_id, "message": SWAP_HOTEL, "client_token": "guest-writers"}
            stream = asyncio.create_task(client.post("/api/turn/stream", json=body))
            await asyncio.wait_for(entered.wait(), 30)

            patched = await client.patch(f"/api/journeys/{session_id}/state", json={"budget": 777777})
            gate.set()
            await asyncio.wait_for(stream, 60)

            assert patched.status_code == 200
            opened = await client.get(f"/api/journeys/{session_id}")
            assert opened.json()["context"]["budget"] == 777777

    asyncio.run(scenario())
