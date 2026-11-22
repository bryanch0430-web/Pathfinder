"""Resuming a turn from its checkpoint (robustness step 3), through the real turn service.

A plan turn that was cut off after its agents finished, or after its plan was built, leaves a
checkpoint. Re-sending the same message against the same plan finishes that turn instead of doing the
work again: the plan is written once, the reply is stored once, and no source is asked a second time.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from app.application.toolbox import Toolbox
from app.application.turn_state import TurnState, checkpoint_for
from app.config import Settings
from app.domain.models import EvalCase, Intent, RouteDecision
from app.eval_efficiency import accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger
from app.infrastructure.observability import NoopTracer

MESSAGE = "Plan this trip"


def _settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})


def _decision() -> RouteDecision:
    return RouteDecision(intent=Intent.PLAN, confidence=0.95, reason_code="new_trip_request")


def _session(rig: Any, ledger: RunLedger, context: Any) -> None:
    ledger.store.save_context(context)


async def _turn(rig: Any, session_id: str, message: str = MESSAGE) -> list:
    return [event async for event in rig.turns.handle_turn(session_id=session_id, message=message)]


def _keep_checkpoint(store: Any, state: TurnState, phase: str) -> None:
    checkpoint = checkpoint_for(state, phase=phase, ttl_seconds=900)
    store.save_turn_checkpoint(
        request_id=checkpoint.request_id,
        session_id=checkpoint.session_id,
        phase=checkpoint.phase,
        message_hash=checkpoint.message_hash,
        base_plan_version=checkpoint.base_plan_version,
        data=checkpoint.to_json(),
        expires_at=checkpoint.expires_at,
    )


def _assistant_rows(store: Any, session_id: str) -> list[str]:
    return [row.content for row in store.list_messages(session_id, limit=10) if row.role == "assistant"]


def _captured_agents(rig: Any, settings: Settings, context: Any) -> dict[str, Any]:
    """The evidence a plan turn gathers, captured before the checkpoint that stores it."""
    captured: dict[str, Any] = {}

    async def run() -> None:
        await rig.plan_path.run(
            context=context.model_copy(deep=True),
            session_id=context.session_id,
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="capture"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
            on_results=lambda results: captured.update(results),
        )

    asyncio.run(run())
    return captured


def _calls(ledger: RunLedger) -> set[str]:
    return {name for name, count in ledger.made.items() if count}


def test_a_turn_cut_off_after_the_plan_was_built_is_finished_from_its_checkpoint(settings: Settings) -> None:
    settings = _settings(settings)
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("guest-resume-plan")
        context = case_context(EvalCase(id="r", dataset="design", input="", destination="Kyoto"), session.id)
        _session(rig, ledger, context)
        plan = asyncio.run(accepted_plan(settings, context))

        state = TurnState.begin(session_id=session.id, request_id="req_old", message=MESSAGE, context=context)
        state.record_route(_decision())
        state.record_plan(plan)
        state.reply = "Your trip is ready."
        _keep_checkpoint(ledger.store, state, "planned")

        events = asyncio.run(_turn(rig, session.id))

        route = [event for event in events if event.event == "route"]
        assert route and route[0].data["intent"] == "plan"
        assert not [event for event in events if event.event == "agent"], "no agent ran again"
        assert not [event for event in events if event.event == "error"]
        assert rig.llm.purposes == [], "no model call was spent to finish the turn"
        stored = ledger.store.get_plan(session.id)
        assert stored is not None and stored.version == 1
        assert _assistant_rows(ledger.store, session.id).count("Your trip is ready.") == 1
        assert ledger.store.latest_turn_checkpoint(session.id) is None, "a finished turn keeps no checkpoint"
    finally:
        ledger.close()


def test_a_turn_cut_off_after_the_agents_finished_composes_from_their_evidence(settings: Settings) -> None:
    settings = _settings(settings)
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("guest-resume-agents")
        context = case_context(EvalCase(id="r2", dataset="design", input="", destination="Kyoto"), session.id)
        _session(rig, ledger, context)
        evidence = _captured_agents(rig, settings, context)
        assert evidence, "the capture turn gathered evidence"

        state = TurnState.begin(session_id=session.id, request_id="req_old", message=MESSAGE, context=context)
        state.record_route(_decision())
        state.record_agents(evidence)
        _keep_checkpoint(ledger.store, state, "agents")
        before = _calls(ledger)

        asyncio.run(_turn(rig, session.id))

        after = _calls(ledger)
        assert not [name for name in after - before if name.endswith(".search")], (
            "a resume composes from the evidence it holds; it does not search again"
        )
        stored = ledger.store.get_plan(session.id)
        assert stored is not None and stored.days, "the plan was composed from the stored evidence"
        assert ledger.store.latest_turn_checkpoint(session.id) is None
    finally:
        ledger.close()


def test_a_checkpoint_for_another_message_is_not_resumed(settings: Settings) -> None:
    settings = _settings(settings)
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("guest-resume-other")
        context = case_context(EvalCase(id="r3", dataset="design", input="", destination="Kyoto"), session.id)
        _session(rig, ledger, context)
        plan = asyncio.run(accepted_plan(settings, context))

        state = TurnState.begin(
            session_id=session.id, request_id="req_old", message="Some other request", context=context
        )
        state.record_route(_decision())
        state.record_plan(plan)
        state.reply = "That other reply."
        _keep_checkpoint(ledger.store, state, "planned")

        events = asyncio.run(_turn(rig, session.id))

        assert [event for event in events if event.event == "agent"], "the turn did its own work"
        assert "That other reply." not in _assistant_rows(ledger.store, session.id)
        assert ledger.store.latest_turn_checkpoint(session.id) is None, "the stale checkpoint is dropped"
    finally:
        ledger.close()


def test_an_expired_checkpoint_is_not_resumed(settings: Settings) -> None:
    settings = _settings(settings)
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("guest-resume-expired")
        context = case_context(EvalCase(id="r4", dataset="design", input="", destination="Kyoto"), session.id)
        _session(rig, ledger, context)
        plan = asyncio.run(accepted_plan(settings, context))

        state = TurnState.begin(session_id=session.id, request_id="req_old", message=MESSAGE, context=context)
        state.record_route(_decision())
        state.record_plan(plan)
        state.reply = "An old reply."
        checkpoint = checkpoint_for(state, phase="planned", ttl_seconds=0)
        ledger.store.save_turn_checkpoint(
            request_id=checkpoint.request_id,
            session_id=checkpoint.session_id,
            phase=checkpoint.phase,
            message_hash=checkpoint.message_hash,
            base_plan_version=checkpoint.base_plan_version,
            data=checkpoint.to_json(),
            expires_at=checkpoint.expires_at - timedelta(seconds=1),
        )

        events = asyncio.run(_turn(rig, session.id))

        assert [event for event in events if event.event == "agent"], "an expired checkpoint plans from scratch"
        assert "An old reply." not in _assistant_rows(ledger.store, session.id)
    finally:
        ledger.close()


def test_a_finished_plan_turn_keeps_no_checkpoint(settings: Settings) -> None:
    settings = _settings(settings)
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("guest-clean")
        context = case_context(EvalCase(id="r5", dataset="design", input="", destination="Kyoto"), session.id)
        _session(rig, ledger, context)

        asyncio.run(_turn(rig, session.id))

        assert ledger.store.get_plan(session.id) is not None
        assert ledger.store.latest_turn_checkpoint(session.id) is None
    finally:
        ledger.close()


def test_a_turn_cut_off_after_it_saved_replays_its_reply_instead_of_planning_again(settings: Settings) -> None:
    settings = _settings(settings)
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("guest-replay")
        context = case_context(EvalCase(id="r6", dataset="design", input="", destination="Kyoto"), session.id)
        _session(rig, ledger, context)
        plan = asyncio.run(accepted_plan(settings, context))
        # The turn got as far as saving its version, then was cut off before its reply was stored.
        ledger.store.save_plan(plan.model_copy(update={"change_summary": "Initial plan"}), "Initial plan")
        state = TurnState.begin(session_id=session.id, request_id="req_old", message=MESSAGE, context=context)
        state.record_route(_decision())
        state.record_plan(plan)
        state.reply = "The plan is written."
        _keep_checkpoint(ledger.store, state, "planned")

        events = asyncio.run(_turn(rig, session.id))

        assert [event for event in events if event.event == "plan"]
        assert not [event for event in events if event.event == "agent"]
        versions = [row["version"] for row in ledger.store.list_versions(session.id)]
        assert versions == [1], "the saved version is not written twice"
        assert _assistant_rows(ledger.store, session.id).count("The plan is written.") == 1
        assert ledger.store.latest_turn_checkpoint(session.id) is None
    finally:
        ledger.close()


def test_a_turn_whose_consumer_closed_keeps_the_checkpoint_it_had(settings: Settings) -> None:
    settings = _settings(settings)
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("guest-closed")
        context = case_context(EvalCase(id="r7", dataset="design", input="", destination="Kyoto"), session.id)
        _session(rig, ledger, context)

        async def first_route_then_close() -> None:
            events = rig.turns.handle_turn(session_id=session.id, message=MESSAGE)
            try:
                async for event in events:
                    if event.event == "route":
                        # The checkpoint after routing was written before this event.
                        break
            finally:
                await events.aclose()

        asyncio.run(first_route_then_close())

        row = ledger.store.latest_turn_checkpoint(session.id)
        assert row is not None, "a turn that was cut off leaves its checkpoint"
        assert row["phase"] == "routed"
    finally:
        ledger.close()
