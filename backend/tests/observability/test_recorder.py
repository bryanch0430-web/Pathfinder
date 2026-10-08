"""Observability facade: trace lifecycle, in-memory store, metrics, sinks."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.observability.jsonl import JsonlSink
from backend.observability.recorder import MAX_TRACES, Observability
from backend.schemas.common import AgentName, PathName, ToolName
from backend.schemas.llm import ChatMessage, LLMRequest, Role, TokenUsage
from backend.schemas.observability import LLMCallRecord, ToolCallRecord, TraceContext, TraceEvent
from backend.schemas.tools import (
    FailureKind,
    FieldIssue,
    SearchHit,
    SearchPayload,
    ToolOperation,
    ToolOutcomeStatus,
)
from backend.settings import Settings

T0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def llm_record(
    trace_id: str,
    *,
    agent: AgentName | None = None,
    usage: TokenUsage | None = None,
    purpose: str = "planner",
    record_id: str = "l1",
    error: str | None = None,
) -> LLMCallRecord:
    return LLMCallRecord(
        record_id=record_id,
        trace_id=trace_id,
        purpose=purpose,
        path=PathName.PLAN,
        agent=agent,
        model="mock-model",
        request=LLMRequest(
            purpose=purpose,
            model="mock-model",
            messages=[ChatMessage(role=Role.SYSTEM, content="sys"), ChatMessage(role=Role.USER, content="hi")],
        ),
        response_content=None if error else '{"ok": true}',
        usage=usage,
        error=error,
        started_at=T0,
        ended_at=T0 + timedelta(milliseconds=120),
        latency_ms=120.0,
    )


def tool_record(
    trace_id: str,
    *,
    agent: AgentName | None = None,
    record_id: str = "t1",
    status: ToolOutcomeStatus = ToolOutcomeStatus.OK,
) -> ToolCallRecord:
    ok = status is ToolOutcomeStatus.OK
    return ToolCallRecord(
        record_id=record_id,
        trace_id=trace_id,
        call_id=f"call-{record_id}",
        path=PathName.PLAN,
        agent=agent,
        operation=ToolOperation.WEB_SEARCH,
        tool=ToolName.WEB_SEARCH,
        raw_request='{"operation": "web_search", "query": "kyoto", "destination": "Kyoto"}',
        status=status,
        failure=None if ok else FailureKind.VALIDATION,
        issues=[] if ok else [FieldIssue(loc="query", message="too short", kind="string_too_short")],
        attempts=1,
        provider="mock",
        payload=SearchPayload(hits=[SearchHit(title="Kyoto", url="https://example.test", snippet="s")]) if ok else None,
        fetched_at=T0 if ok else None,
        started_at=T0,
        ended_at=T0 + timedelta(milliseconds=30),
        latency_ms=30.0,
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def obs(clock: Clock) -> Observability:
    return Observability(Settings(), sinks=[], clock=clock)


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def on_trace_start(self, trace: TraceContext) -> None:
        self.calls.append(("trace_start", trace))

    def on_llm_call(self, record: LLMCallRecord) -> None:
        self.calls.append(("llm_call", record))

    def on_tool_call(self, record: ToolCallRecord) -> None:
        self.calls.append(("tool_call", record))

    def on_event(self, event: TraceEvent) -> None:
        self.calls.append(("event", event))

    def on_trace_end(self, trace: TraceContext, output: str | None) -> None:
        self.calls.append(("trace_end", (trace, output)))

    def flush(self) -> None:
        self.calls.append(("flush", None))


class ExplodingSink(RecordingSink):
    def _boom(self, *_: object) -> None:
        raise RuntimeError("sink is down")

    on_trace_start = on_llm_call = on_tool_call = on_event = on_trace_end = flush = _boom  # type: ignore[assignment]


# --------------------------------------------------------------------------------------------------


def test_trace_lifecycle_and_trace_id_format(obs: Observability, clock: Clock) -> None:
    trace = obs.start_trace(name="chat_turn", session_id="sess-1")
    assert re.fullmatch(r"[0-9a-f]{32}", trace.trace_id)
    assert trace.name == "chat_turn" and trace.session_id == "sess-1"
    assert trace.started_at == T0
    assert obs.start_trace(name="chat_turn", session_id="sess-1").trace_id != trace.trace_id

    clock.advance(seconds=2)
    assert obs.metrics(trace.trace_id).latency_ms == pytest.approx(2000.0)  # still running: to now
    clock.advance(seconds=1)
    obs.end_trace(trace, output="done")
    clock.advance(seconds=60)  # time after the end does not count
    assert obs.metrics(trace.trace_id).latency_ms == pytest.approx(3000.0)


def test_records_are_retrievable_by_trace_id_and_isolated(obs: Observability) -> None:
    a = obs.start_trace(name="a", session_id="s")
    b = obs.start_trace(name="b", session_id="s")
    obs.record_llm_call(llm_record(a.trace_id, record_id="la"))
    obs.record_llm_call(llm_record(b.trace_id, record_id="lb"))
    obs.record_tool_call(tool_record(a.trace_id, record_id="ta"))
    obs.record_event(a, "gate", {"route": "plan", "confidence": 0.93, "accepted": True})

    assert [r.record_id for r in obs.llm_calls(a.trace_id)] == ["la"]
    assert [r.record_id for r in obs.llm_calls(b.trace_id)] == ["lb"]
    assert [r.record_id for r in obs.tool_calls(a.trace_id)] == ["ta"]
    assert obs.tool_calls(b.trace_id) == []
    [event] = obs.events(a.trace_id)
    assert event.name == "gate" and event.trace_id == a.trace_id
    assert event.attributes == {"route": "plan", "confidence": 0.93, "accepted": True}
    assert obs.events(b.trace_id) == []
    # Unknown trace ids read as empty, never raise.
    assert obs.llm_calls("0" * 32) == [] and obs.tool_calls("0" * 32) == [] and obs.events("0" * 32) == []
    assert obs.metrics("0" * 32).llm_calls == 0
    # Returned lists are copies.
    obs.llm_calls(a.trace_id).clear()
    assert len(obs.llm_calls(a.trace_id)) == 1


def test_metrics_sum_tokens_and_list_agents_in_canonical_order(obs: Observability) -> None:
    trace = obs.start_trace(name="plan", session_id="s")
    obs.record_llm_call(llm_record(trace.trace_id, usage=TokenUsage(input_tokens=100, output_tokens=20), record_id="l1"))
    obs.record_llm_call(
        llm_record(trace.trace_id, agent=AgentName.WEATHER, usage=TokenUsage(input_tokens=50, output_tokens=5), record_id="l2")
    )
    obs.record_llm_call(llm_record(trace.trace_id, usage=None, record_id="l3", error="timeout"))
    obs.record_tool_call(tool_record(trace.trace_id, agent=AgentName.TICKET, record_id="t1"))
    obs.record_tool_call(tool_record(trace.trace_id, agent=AgentName.ATTRACTION, record_id="t2"))
    obs.record_tool_call(tool_record(trace.trace_id, agent=AgentName.ATTRACTION, record_id="t3"))
    obs.record_tool_call(tool_record(trace.trace_id, agent=None, record_id="t4"))

    metrics = obs.metrics(trace.trace_id)
    assert metrics.llm_calls == 3
    assert metrics.tool_calls == 4
    assert metrics.input_tokens == 150
    assert metrics.output_tokens == 25
    # Hotel never ran; order follows ALL_AGENTS (attraction, hotel, weather, ticket).
    assert metrics.agents_run == [AgentName.ATTRACTION, AgentName.WEATHER, AgentName.TICKET]


def test_store_keeps_only_the_most_recent_500_traces(obs: Observability) -> None:
    assert MAX_TRACES == 500
    first = obs.start_trace(name="t", session_id="s")
    obs.record_llm_call(llm_record(first.trace_id))
    second = obs.start_trace(name="t", session_id="s")
    others = [obs.start_trace(name="t", session_id="s") for _ in range(498)]
    # 500 traces so far: nothing evicted.
    assert len(obs.llm_calls(first.trace_id)) == 1
    newest = obs.start_trace(name="t", session_id="s")  # 501st evicts the oldest
    assert obs.llm_calls(first.trace_id) == []
    assert obs.metrics(first.trace_id).llm_calls == 0
    obs.record_event(second, "still-here")
    assert len(obs.events(second.trace_id)) == 1
    obs.record_event(newest, "newest")
    assert len(obs.events(newest.trace_id)) == 1
    assert others  # silence unused warning


def test_store_bound_is_configurable(clock: Clock) -> None:
    small = Observability(Settings(), sinks=[], max_traces=2, clock=clock)
    t1 = small.start_trace(name="t", session_id="s")
    small.record_event(t1, "will be evicted")
    t2, t3 = (small.start_trace(name="t", session_id="s") for _ in range(2))
    assert small.events(t1.trace_id) == []  # oldest of three is gone
    small.record_event(t2, "kept")
    small.record_event(t3, "kept")
    assert len(small.events(t2.trace_id)) == 1 and len(small.events(t3.trace_id)) == 1
    with pytest.raises(ValueError):
        Observability(Settings(), sinks=[], max_traces=0)


def test_sinks_receive_every_record_in_order(clock: Clock) -> None:
    sink = RecordingSink()
    obs = Observability(Settings(), sinks=[sink], clock=clock)
    trace = obs.start_trace(name="turn", session_id="s")
    obs.record_llm_call(llm_record(trace.trace_id))
    obs.record_tool_call(tool_record(trace.trace_id))
    obs.record_event(trace, "e")
    obs.end_trace(trace, output="final answer")
    obs.flush()
    assert [kind for kind, _ in sink.calls] == [
        "trace_start", "llm_call", "tool_call", "event", "trace_end", "flush",
    ]
    assert sink.calls[4][1] == (trace, "final answer")


def test_a_failing_sink_never_breaks_recording(clock: Clock, caplog: pytest.LogCaptureFixture) -> None:
    good = RecordingSink()
    obs = Observability(Settings(), sinks=[ExplodingSink(), good], clock=clock)
    with caplog.at_level(logging.WARNING, logger="backend.observability.recorder"):
        trace = obs.start_trace(name="turn", session_id="s")
        obs.record_llm_call(llm_record(trace.trace_id, usage=TokenUsage(input_tokens=1, output_tokens=2)))
        obs.record_tool_call(tool_record(trace.trace_id))
        obs.record_event(trace, "e")
        obs.end_trace(trace, output="x")
        obs.flush()
    assert obs.metrics(trace.trace_id).llm_calls == 1
    assert len(obs.tool_calls(trace.trace_id)) == 1
    assert len(obs.events(trace.trace_id)) == 1
    assert [kind for kind, _ in good.calls][-1] == "flush"  # later sinks still ran
    assert any("ExplodingSink" in rec.getMessage() for rec in caplog.records)


def test_default_sinks_follow_settings(tmp_path: Path) -> None:
    jsonl_only = Observability(Settings(log_dir=tmp_path, call_log_jsonl=True))
    assert [type(s).__name__ for s in jsonl_only._sinks] == ["JsonlSink"]
    none = Observability(Settings(log_dir=tmp_path, call_log_jsonl=False))
    assert none._sinks == []
    assert Observability(Settings(), sinks=[])._sinks == []


# --------------------------------------------------------------------------------------------------
# JSONL sink
# --------------------------------------------------------------------------------------------------


def test_jsonl_sink_writes_parseable_typed_lines(tmp_path: Path, clock: Clock) -> None:
    log_dir = tmp_path / "logs" / "nested"
    obs = Observability(Settings(), sinks=[JsonlSink(log_dir)], clock=clock)
    trace = obs.start_trace(name="chat_turn", session_id="sess-9")
    obs.record_llm_call(llm_record(trace.trace_id, usage=TokenUsage(input_tokens=7, output_tokens=3)))
    obs.record_tool_call(tool_record(trace.trace_id, agent=AgentName.HOTEL))
    obs.record_tool_call(tool_record(trace.trace_id, record_id="bad", status=ToolOutcomeStatus.VALIDATION_ERROR))
    obs.record_event(trace, "route", {"route": "plan"})
    obs.end_trace(trace, output="日本 trip ✈")
    obs.flush()

    files = sorted(log_dir.glob("calls-*.jsonl"))
    assert len(files) == 1
    assert re.fullmatch(r"calls-\d{8}\.jsonl", files[0].name)
    raw = files[0].read_bytes()
    assert b"\r\n" not in raw  # one object per line on every platform
    lines = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    assert [line["type"] for line in lines] == [
        "trace_start", "llm_call", "tool_call", "tool_call", "event", "trace_end",
    ]
    assert all(line["trace_id"] == trace.trace_id for line in lines)

    llm = lines[1]
    assert llm["purpose"] == "planner" and llm["usage"] == {"input_tokens": 7, "output_tokens": 3}
    assert llm["request"]["messages"][1] == {"role": "user", "content": "hi"}  # exactly what was sent
    tool = lines[2]
    assert tool["status"] == "ok" and tool["agent"] == "hotel" and tool["operation"] == "web_search"
    assert tool["payload"]["hits"][0]["title"] == "Kyoto"
    assert tool["raw_request"].startswith('{"operation"')
    assert lines[3]["status"] == "validation_error" and lines[3]["issues"][0]["loc"] == "query"
    assert lines[4]["attributes"] == {"route": "plan"}
    assert lines[5]["output"] == "日本 trip ✈" and "ended_at" in lines[5]


def test_jsonl_sink_appends_across_instances(tmp_path: Path, clock: Clock) -> None:
    for _ in range(2):
        obs = Observability(Settings(), sinks=[JsonlSink(tmp_path)], clock=clock)
        obs.start_trace(name="t", session_id="s")
    [file] = list(tmp_path.glob("calls-*.jsonl"))
    assert len(file.read_text(encoding="utf-8").splitlines()) == 2


def test_jsonl_sink_failure_is_contained(tmp_path: Path, clock: Clock) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    obs = Observability(Settings(), sinks=[JsonlSink(blocker / "logs")], clock=clock)
    trace = obs.start_trace(name="t", session_id="s")  # the sink raises OSError internally
    obs.record_event(trace, "still recorded")
    assert len(obs.events(trace.trace_id)) == 1


def test_records_for_a_trace_never_started_here_are_still_stored(obs: Observability) -> None:
    # Callers (and unit tests of other layers) may record against an id this facade never issued.
    obs.record_tool_call(tool_record("manual-trace", agent=AgentName.TICKET))
    assert len(obs.tool_calls("manual-trace")) == 1
    assert obs.metrics("manual-trace").agents_run == [AgentName.TICKET]
    assert obs.metrics("manual-trace").latency_ms == 0.0
