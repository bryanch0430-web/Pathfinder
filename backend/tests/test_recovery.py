"""The recovery path: a disabled venue and a trip moved to dates with no flight (robustness step 4).

The classifier is deterministic, so it is tested as a pure function. The closed-stop recovery is
exercised with a stub modify path whose agent returns one fresh candidate: the test then checks the
promises of the path - only the attraction agent ran, the locked stop was kept, the rest was not
touched, and the plan says what changed.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.recovery import (
    classify_closed_stop,
    note_missing_flight,
    recover_closed_stop,
)
from app.config import Settings
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    GeoPoint,
    TripContext,
    TripPlan,
)
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from scripts.seed_platform_demo import DESTINATIONS, _plan

SESSION = "sess_recovery"
CLOSED = "Nijo Castle"  # Day 1, first row of the seeded plan


def _plan_fixture() -> TripPlan:
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    plan = _plan(random.Random(1), place, 1)
    plan = plan.model_copy(update={"session_id": SESSION, "version": 1})
    if plan.first_stay is not None:
        plan.first_stay.check_out = plan.dates.end
    for day in plan.days:
        for item in day.items:
            if item.geo is None:
                # The demo fixture leaves coordinates out; a real plan always has them, and the
                # recovery path's own validation pass expects a plan it can check.
                item.geo = GeoPoint(lat=35.0116, lng=135.7681)
    return plan


def _context() -> TripContext:
    return TripContext(
        session_id=SESSION,
        destination="Kyoto",
        origin="Hong Kong",
        start_date=date(2026, 8, 6),
        end_date=date(2026, 8, 8),
        days=3,
        party_size=2,
        budget=9000,
        currency="HKD",
    )


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        data_dir=tmp_path,
        database_path=tmp_path / "test.db",
        trace_dir=tmp_path / "traces",
        eval_dir=tmp_path / "eval",
        llm_api_key="",
        llm_base_url="",
        xai_api_key="",
        decision_enabled=False,
        langfuse_enabled=False,
        default_currency="USD",
    )


# -- the classifier -------------------------------------------------------


def test_a_stop_the_message_says_is_closed_is_a_recovery_request() -> None:
    request = classify_closed_stop(_plan_fixture(), "Nijo Castle is closed tomorrow, what now?")

    # A question is answered, not acted on.
    assert request is None

    request = classify_closed_stop(_plan_fixture(), "Nijo Castle is closed tomorrow.")
    assert request is not None
    assert request.kind == "closed_stop"
    assert request.name == CLOSED
    assert request.day == 1


def test_a_hypothetical_closure_is_not_a_disruption() -> None:
    assert classify_closed_stop(_plan_fixture(), "what if Nijo Castle is closed?") is None
    assert classify_closed_stop(_plan_fixture(), "if Nijo Castle were closed we would go elsewhere") is None


def test_a_closure_with_no_plan_stop_named_changes_nothing() -> None:
    assert classify_closed_stop(_plan_fixture(), "The Golden Pavilion is closed today.") is None


def test_a_remove_request_is_left_to_the_ordinary_edit_path() -> None:
    # The sentence says what to do with the stop: an automatic replacement would contradict it.
    assert classify_closed_stop(_plan_fixture(), "Remove Nijo Castle, it is closed.") is None


def test_the_selected_stop_can_be_the_closed_one() -> None:
    plan = _plan_fixture()
    selected = next(item for day in plan.days for item in day.items if item.name == CLOSED)

    request = classify_closed_stop(plan, "this is closed tomorrow", scope_item_id=selected.id)

    assert request is not None and request.item_id == selected.id


# -- the recovery turn ----------------------------------------------------


@dataclass
class StubPlanPath:
    settings: Settings
    candidates: list[dict[str, Any]]
    calls: list[str]
    #: No transfer source: the day's legs are not re-timed, which the recovery tolerates.
    registry: Any = None

    async def _locate(self, name: str, *, toolbox: Any, tracer: Any) -> Any:
        return DESTINATIONS[0]

    async def run_agents(self, *, names, **_: Any) -> dict[str, AgentResult]:
        self.calls.append(",".join(sorted(name.value for name in names)))
        return {
            "attraction": AgentResult(
                agent=AgentName.ATTRACTION,
                status=AgentStatus.OK,
                payload={"places": self.candidates},
            )
        }


@dataclass
class StubModifyPath:
    plan_path: StubPlanPath
    settings: Settings
    gateway: Any = None


def _stub(tmp_path, candidates: list[dict[str, Any]]) -> StubModifyPath:
    settings = _settings(tmp_path)
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    return StubModifyPath(StubPlanPath(settings, candidates, []), settings, gateway)


def _candidate(name: str, *, lat: float, lng: float) -> dict[str, Any]:
    return {
        "name": name,
        "category": "attraction",
        "description": "A nearby museum.",
        "geo": GeoPoint(lat=lat, lng=lng).model_dump(mode="json"),
        "source_refs": [],
        "opening_hours": [],
    }


def test_a_closed_stop_is_swapped_for_a_nearby_alternative_and_the_rest_is_kept(tmp_path) -> None:
    plan = _plan_fixture()
    before = {
        item.id: (index, item.name, item.start, item.end)
        for index, day in enumerate(plan.days)
        for item in day.items
    }
    closed = next(item for day in plan.days for item in day.items if item.name == CLOSED)
    # Coordinates near the closed stop: the nearest candidate wins, not the first row.
    candidates = [
        _candidate("Far Away Wax Museum", lat=35.9, lng=136.2),
        _candidate("Nearby Craft Museum", lat=35.0116, lng=135.7681),
    ]
    stub = _stub(tmp_path, candidates)

    result = asyncio.run(
        recover_closed_stop(
            stub,
            plan,
            classify_closed_stop(plan, f"{CLOSED} is closed tomorrow."),
            context=_context(),
            toolbox=object(),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )

    updated, diff = result
    assert stub.plan_path.calls == ["attraction"], "only the agent that owns the stop ran"
    assert diff, "the plan changed, so the diff is not empty"
    swapped = next(item for day in updated.days for item in day.items if item.name == "Nearby Craft Museum")
    assert updated.version == plan.version + 1, "a recovery that moved a stop is a new version"
    assert (swapped.start, swapped.end) == (closed.start, closed.end), "the day's clock is kept"
    # Every other row is exactly where it was.
    after = {
        item.id: (index, item.name, item.start, item.end)
        for index, day in enumerate(updated.days)
        for item in day.items
        if item.id != closed.id
    }
    for item_id, row in after.items():
        if item_id in before:
            assert before[item_id] == row
    assert any(text.startswith(f"{CLOSED} is") for text in updated.data_quality.notes)
    assert updated.disruptions and updated.disruptions[-1].resolved


def test_a_locked_closed_stop_stays_and_the_plan_says_why(tmp_path) -> None:
    plan = _plan_fixture()
    closed = next(item for day in plan.days for item in day.items if item.name == CLOSED)
    closed.locked = True
    stub = _stub(tmp_path, [])

    result = asyncio.run(
        recover_closed_stop(
            stub,
            plan,
            classify_closed_stop(plan, f"{CLOSED} is closed tomorrow."),
            context=_context(),
            toolbox=object(),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )

    updated, diff = result
    assert diff == []
    assert stub.plan_path.calls == [], "a locked stop is not looked up again"
    kept = next(item for day in updated.days for item in day.items if item.name == CLOSED)
    assert kept.id == closed.id
    assert "locked" in updated.change_summary


def test_a_closed_stop_with_no_alternative_is_taken_off_the_day(tmp_path) -> None:
    plan = _plan_fixture()
    closed = next(item for day in plan.days for item in day.items if item.name == CLOSED)
    stub = _stub(tmp_path, [])

    result = asyncio.run(
        recover_closed_stop(
            stub,
            plan,
            classify_closed_stop(plan, f"{CLOSED} is closed tomorrow."),
            context=_context(),
            toolbox=object(),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )

    updated, _diff = result
    assert all(item.id != closed.id for day in updated.days for item in day.items)
    assert any("no verified alternative" in text for text in updated.data_quality.notes)


def test_a_moved_trip_with_no_flight_says_so_and_keeps_the_gap_rule() -> None:
    plan = _plan_fixture()
    assert plan.tickets == []

    updated = note_missing_flight(plan, date_changed=True, flights=[])

    assert updated.disruptions and updated.disruptions[-1].kind == "missing_flights"
    assert any("No flight for the new dates" in text for text in updated.data_quality.notes)
    # A budget change or a trip that did not move gets no such record.
    assert note_missing_flight(plan, date_changed=False, flights=[]).disruptions == []


def test_a_closed_stop_message_is_recovered_through_a_real_modify_turn(settings: Settings) -> None:
    """The wiring: a message about a closed stop reaches the recovery path from a real turn."""
    from app.domain.models import EvalCase, ItemType
    from app.eval_efficiency import accepted_plan, build_rig, case_context
    from app.eval_metering import RunLedger

    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Tokyo", ledger)
        session = ledger.store.create_session("guest-recovery")
        context = case_context(
            EvalCase(id="rec", dataset="design", input="", destination="Tokyo"), session.id
        )
        ledger.store.save_context(context)
        before = asyncio.run(accepted_plan(settings, context))
        closed = next(
            item
            for day in before.days
            for item in day.items
            if item.type is ItemType.ATTRACTION
        )
        # One other stop is protected: it must survive the recovery untouched.
        other = next(
            item
            for day in before.days
            for item in day.items
            if item.type is ItemType.ATTRACTION and item.id != closed.id
        )
        other.locked = True
        ledger.store.save_plan(before, "Accepted plan")
        rows = {
            item.id: (index, item.name, item.start, item.end)
            for index, day in enumerate(before.days)
            for item in day.items
        }

        rig.llm.route = {"intent": "modify", "confidence": 0.95, "affected_parts": ["attraction"]}

        async def turn() -> list:
            return [
                event
                async for event in rig.turns.handle_turn(
                    session_id=session.id, message=f"{closed.name} is closed tomorrow."
                )
            ]

        events = asyncio.run(turn())
        errors = [event for event in events if event.event == "error"]
        assert not errors, errors[0].data if errors else ""
        payload = next(event.data for event in events if event.event == "plan")
        after = TripPlan.model_validate(payload["plan"])

        assert after.version == before.version + 1
        assert after.disruptions and after.disruptions[-1].kind == "closure"
        rows_after = [(item.id, item.name) for day in after.days for item in day.items]
        assert (closed.id, closed.name) not in rows_after, "the closed stop is no longer the stop it was"
        assert any(item_id == other.id for item_id, _name in rows_after), "the locked stop stays"
        kept = next(item for day in after.days for item in day.items if item.id == other.id)
        assert (kept.name, kept.start, kept.end) == (rows[other.id][1], rows[other.id][2], rows[other.id][3])
        assert any("closed" in item.content.lower() for item in ledger.store.list_messages(session.id, limit=3))
    finally:
        ledger.close()
