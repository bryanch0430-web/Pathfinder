"""Manual stop edits through the orchestrator: no agent runs, every edit is traced (design 4.2)."""

from __future__ import annotations

from backend.container import build_container
from backend.schemas.edits import PlanItemPatch
from backend.schemas.observability import LLMCallRecord, ToolCallRecord, TraceContext, TraceEvent
from backend.tests.integration.conftest import kyoto_context, make_settings, no_sleep


class EventLog:
    """A CallSink that keeps trace starts and events (the in-memory store cannot list traces)."""

    def __init__(self) -> None:
        self.traces: list[TraceContext] = []
        self.events: list[TraceEvent] = []

    def on_trace_start(self, trace: TraceContext) -> None:
        self.traces.append(trace)

    def on_llm_call(self, record: LLMCallRecord) -> None:
        return None

    def on_tool_call(self, record: ToolCallRecord) -> None:
        return None

    def on_event(self, event: TraceEvent) -> None:
        self.events.append(event)

    def on_trace_end(self, trace: TraceContext, output: str | None) -> None:
        return None

    def flush(self) -> None:
        return None


async def test_manual_edits_run_no_agent_and_are_traced() -> None:
    log = EventLog()
    container = build_container(make_settings(), sinks=[log], sleep=no_sleep)
    await container.start()
    try:
        orchestrator = container.orchestrator
        state = await orchestrator.create_session(kyoto_context())
        planned = await orchestrator.handle_turn(state.session_id, "Plan my trip to Kyoto please")
        plan = planned.plan
        assert plan is not None
        noted_id, deleted_id = plan.days[0].items[0].item_id, plan.days[1].items[0].item_id
        seen = len(log.traces)

        noted = await orchestrator.patch_item(state.session_id, noted_id, PlanItemPatch(note="Go early"))
        removed = await orchestrator.delete_item(state.session_id, deleted_id)

        assert noted.version == plan.version + 1 and noted.days[0].items[0].note == "Go early"
        assert removed.version == plan.version + 2
        edits = log.traces[seen:]
        assert [t.name for t in edits] == ["edit:patch_item", "edit:delete_item"]
        assert [e.attributes for e in log.events if e.name == "plan_item_edited"] == [
            {"op": "patch", "item_id": noted_id, "fields": "note", "version": plan.version + 1},
            {"op": "delete", "item_id": deleted_id, "fields": "", "version": plan.version + 2},
        ]
        for trace in edits:
            assert container.observability.llm_calls(trace.trace_id) == []
            assert container.observability.tool_calls(trace.trace_id) == []
        session = await orchestrator.get_session(state.session_id)
        assert session.plan == removed
        assert len(session.history) == 2  # edits are not chat turns
    finally:
        await container.stop()
