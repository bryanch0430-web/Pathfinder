"""Observability facade: traces, LLM call records, tool call records, events.

Requirements met here:
  * Every LLM call and tool call is recorded with inputs, outputs, timestamps and trace id.
  * An in-memory store (bounded: keep the most recent N traces) always exists: the grounding
    metric and the per-turn metrics read from it.
  * Optional sinks: JSON-lines files under settings.log_dir (settings.call_log_jsonl) and Langfuse
    (enabled when settings.langfuse_enabled). A failing sink must never break a turn: every sink
    call is wrapped, the failure is logged with `logging`, and recording carries on.

Trace ids are `uuid4().hex` (32 lowercase hex characters), which is also a valid W3C / Langfuse
trace id, so the same id can be handed to Langfuse unchanged.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from backend.observability.jsonl import JsonlSink
from backend.observability.langfuse_sink import LangfuseSink
from backend.schemas.common import ALL_AGENTS, AgentName, utcnow
from backend.schemas.observability import (
    AttributeValue,
    LLMCallRecord,
    ToolCallRecord,
    TraceContext,
    TraceEvent,
    TurnMetrics,
)
from backend.settings import Settings

logger = logging.getLogger(__name__)

MAX_TRACES = 500


class CallSink(Protocol):
    def on_trace_start(self, trace: TraceContext) -> None: ...
    def on_llm_call(self, record: LLMCallRecord) -> None: ...
    def on_tool_call(self, record: ToolCallRecord) -> None: ...
    def on_event(self, event: TraceEvent) -> None: ...
    def on_trace_end(self, trace: TraceContext, output: str | None) -> None: ...
    def flush(self) -> None: ...


@dataclass
class _TraceBucket:
    """Everything recorded under one trace id. `trace` is None when records arrive for a trace
    that was never started here (or whose bucket was evicted and re-created)."""

    trace: TraceContext | None = None
    ended_at: datetime | None = None
    llm: list[LLMCallRecord] = field(default_factory=list)
    tools: list[ToolCallRecord] = field(default_factory=list)
    events: list[TraceEvent] = field(default_factory=list)


class Observability:
    def __init__(
        self,
        settings: Settings,
        *,
        sinks: Sequence[CallSink] | None = None,
        max_traces: int = MAX_TRACES,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        """`sinks=None` builds the default sinks from settings (JSONL, Langfuse); pass [] to keep
        only the in-memory store (tests). `max_traces` bounds the in-memory store; `clock` is an
        injectable time source (tests)."""
        if max_traces < 1:
            raise ValueError("max_traces must be at least 1")
        self._settings = settings
        self._max_traces = max_traces
        self._clock = clock
        self._store: OrderedDict[str, _TraceBucket] = OrderedDict()
        self._sinks: list[CallSink] = (
            list(sinks) if sinks is not None else _default_sinks(settings)
        )

    # ---- traces -----------------------------------------------------------------------------------

    def start_trace(self, *, name: str, session_id: str) -> TraceContext:
        trace = TraceContext(
            trace_id=uuid.uuid4().hex, session_id=session_id, name=name, started_at=self._clock()
        )
        self._bucket(trace.trace_id).trace = trace
        self._emit("on_trace_start", trace)
        return trace

    def end_trace(self, trace: TraceContext, *, output: str | None = None) -> None:
        bucket = self._bucket(trace.trace_id)
        if bucket.trace is None:
            bucket.trace = trace
        if bucket.ended_at is None:
            bucket.ended_at = self._clock()
        self._emit("on_trace_end", trace, output)

    # ---- records ----------------------------------------------------------------------------------

    def record_llm_call(self, record: LLMCallRecord) -> None:
        self._bucket(record.trace_id).llm.append(record)
        self._emit("on_llm_call", record)

    def record_tool_call(self, record: ToolCallRecord) -> None:
        self._bucket(record.trace_id).tools.append(record)
        self._emit("on_tool_call", record)

    def record_event(
        self,
        trace: TraceContext,
        name: str,
        attributes: Mapping[str, AttributeValue] | None = None,
    ) -> None:
        event = TraceEvent(
            trace_id=trace.trace_id, name=name, at=self._clock(), attributes=dict(attributes or {})
        )
        self._bucket(trace.trace_id).events.append(event)
        self._emit("on_event", event)

    # ---- reads --------------------------------------------------------------------------------------

    def llm_calls(self, trace_id: str) -> list[LLMCallRecord]:
        bucket = self._store.get(trace_id)
        return list(bucket.llm) if bucket else []

    def tool_calls(self, trace_id: str) -> list[ToolCallRecord]:
        bucket = self._store.get(trace_id)
        return list(bucket.tools) if bucket else []

    def events(self, trace_id: str) -> list[TraceEvent]:
        bucket = self._store.get(trace_id)
        return list(bucket.events) if bucket else []

    def metrics(self, trace_id: str) -> TurnMetrics:
        """Counts and token sums for the trace; latency_ms = trace start to end (or to now)."""
        bucket = self._store.get(trace_id)
        if bucket is None:
            return TurnMetrics()
        input_tokens = sum(r.usage.input_tokens for r in bucket.llm if r.usage is not None)
        output_tokens = sum(r.usage.output_tokens for r in bucket.llm if r.usage is not None)
        seen: set[AgentName] = {r.agent for r in bucket.llm if r.agent is not None}
        seen.update(r.agent for r in bucket.tools if r.agent is not None)
        latency_ms = 0.0
        if bucket.trace is not None:
            end = bucket.ended_at or self._clock()
            latency_ms = (end - bucket.trace.started_at).total_seconds() * 1000.0
        return TurnMetrics(
            llm_calls=len(bucket.llm),
            tool_calls=len(bucket.tools),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            agents_run=[agent for agent in ALL_AGENTS if agent in seen],
        )

    def flush(self) -> None:
        for sink in self._sinks:
            try:
                sink.flush()
            except Exception:
                logger.warning("observability sink %s failed in flush", _sink_name(sink), exc_info=True)

    # ---- internals ----------------------------------------------------------------------------------

    def _bucket(self, trace_id: str) -> _TraceBucket:
        bucket = self._store.get(trace_id)
        if bucket is None:
            bucket = _TraceBucket()
            self._store[trace_id] = bucket
            while len(self._store) > self._max_traces:
                self._store.popitem(last=False)  # evict the oldest trace
        return bucket

    def _emit(self, method: str, *args: object) -> None:
        """Call `method` on every sink; a failing sink is logged and skipped, never raised."""
        for sink in self._sinks:
            try:
                getattr(sink, method)(*args)
            except Exception:
                logger.warning("observability sink %s failed in %s", _sink_name(sink), method, exc_info=True)


def _sink_name(sink: object) -> str:
    return type(sink).__name__


def _default_sinks(settings: Settings) -> list[CallSink]:
    sinks: list[CallSink] = []
    if settings.call_log_jsonl:
        sinks.append(JsonlSink(settings.log_dir))
    if settings.langfuse_enabled:
        try:
            sinks.append(LangfuseSink(settings))
        except Exception:
            logger.warning("Langfuse sink disabled: client could not be created", exc_info=True)
    return sinks
