"""Langfuse sink (SDK 3.x): mirrors traces, LLM calls, tool calls and events to Langfuse.

Mapping (verified against langfuse 3.15):
  trace start -> `client.start_span(trace_context={"trace_id": id}, name=trace.name, ...)` is the
                 root span of the trace; `root.update_trace(name=..., session_id=...)` sets the
                 session. Our trace id (uuid4 hex) is already a valid Langfuse trace id.
  llm call    -> `root.start_observation(as_type="generation", name=purpose, model=...,
                 input=<messages as dicts>, output=<response>, usage_details={"input",
                 "output"}, ...)` then `.end(...)`. (`start_generation` is deprecated in 3.15;
                 `start_observation(as_type="generation")` is its replacement.)
  tool call   -> `root.start_span(name="tool:<operation>", input=<raw request>, output=<payload
                 json or issues>, metadata=...)` then `.end(...)`.
  event       -> `root.create_event(name=..., metadata=attributes)` (a zero-length observation).
  trace end   -> `root.update(output=...)`, `root.update_trace(output=...)`, `root.end()`.
  flush       -> `client.flush()`.

The SDK cannot back-date an observation's start (start_span / start_generation take no start
time), and records reach a sink after the call has finished. So each observation is created when
the record arrives and ended at `now + latency_ms`; its duration is therefore the real call
latency, while the real wall-clock `started_at` / `ended_at` travel in the metadata.

The client is injectable (`client=`) so tests use a fake; the real `Langfuse` client is built only
when none is passed, with the keys from settings.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Mapping
from datetime import datetime
from time import time_ns
from typing import Protocol

from backend.schemas.observability import LLMCallRecord, ToolCallRecord, TraceContext, TraceEvent
from backend.schemas.tools import ToolOutcomeStatus
from backend.settings import Settings

logger = logging.getLogger(__name__)

_MAX_OPEN_TRACES = 500
_MetadataValue = str | int | float | bool


class LangfuseObservation(Protocol):
    """The slice of a Langfuse span / generation this sink uses."""

    def update(self, **kwargs: object) -> object: ...
    def update_trace(self, **kwargs: object) -> object: ...
    def end(self, *, end_time: int | None = None) -> object: ...
    def start_span(self, name: str, **kwargs: object) -> LangfuseObservation: ...
    def start_observation(self, *, name: str, as_type: str, **kwargs: object) -> LangfuseObservation: ...
    def create_event(self, *, name: str, **kwargs: object) -> object: ...


class LangfuseClientLike(Protocol):
    """The slice of the `langfuse.Langfuse` client this sink uses."""

    def start_span(self, *, name: str, **kwargs: object) -> LangfuseObservation: ...
    def start_observation(self, *, name: str, as_type: str, **kwargs: object) -> LangfuseObservation: ...
    def create_event(self, *, name: str, **kwargs: object) -> object: ...
    def flush(self) -> None: ...


def _clean(values: Mapping[str, _MetadataValue | None]) -> dict[str, _MetadataValue]:
    return {key: value for key, value in values.items() if value is not None}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class LangfuseSink:
    def __init__(self, settings: Settings, client: LangfuseClientLike | None = None) -> None:
        if client is None:
            from langfuse import Langfuse  # imported lazily: the SDK is heavy and optional offline

            client = Langfuse(
                public_key=settings.langfuse_public_key.get_secret_value(),
                secret_key=settings.langfuse_secret_key.get_secret_value(),
                host=settings.langfuse_host,
            )
        self._client: LangfuseClientLike = client
        self._roots: OrderedDict[str, LangfuseObservation] = OrderedDict()

    # ---- CallSink protocol --------------------------------------------------------------------

    def on_trace_start(self, trace: TraceContext) -> None:
        root = self._client.start_span(
            trace_context={"trace_id": trace.trace_id},
            name=trace.name,
            metadata=_clean({"session_id": trace.session_id, "started_at": _iso(trace.started_at)}),
        )
        root.update_trace(name=trace.name, session_id=trace.session_id)
        self._roots[trace.trace_id] = root
        while len(self._roots) > _MAX_OPEN_TRACES:  # traces that were never ended
            self._roots.popitem(last=False)

    def on_llm_call(self, record: LLMCallRecord) -> None:
        kwargs: dict[str, object] = {
            "name": record.purpose,
            "model": record.model,
            "input": [message.model_dump(mode="json") for message in record.request.messages],
            "output": record.response_content,
            "model_parameters": {
                "temperature": str(record.request.temperature),
                "max_tokens": record.request.max_tokens,
                "json_output": record.request.json_output,
            },
            "metadata": _clean(
                {
                    "record_id": record.record_id,
                    "path": record.path.value if record.path else None,
                    "agent": record.agent.value if record.agent else None,
                    "error": record.error,
                    "latency_ms": record.latency_ms,
                    "started_at": _iso(record.started_at),
                    "ended_at": _iso(record.ended_at),
                }
            ),
        }
        if record.usage is not None:
            kwargs["usage_details"] = {
                "input": record.usage.input_tokens,
                "output": record.usage.output_tokens,
            }
        if record.error:
            kwargs["level"] = "ERROR"
            kwargs["status_message"] = record.error
        root = self._roots.get(record.trace_id)
        start_ns = time_ns()
        if root is not None:
            generation = root.start_observation(as_type="generation", **kwargs)  # type: ignore[arg-type]
        else:
            generation = self._client.start_observation(
                trace_context={"trace_id": record.trace_id}, as_type="generation", **kwargs  # type: ignore[arg-type]
            )
        generation.end(end_time=start_ns + _ns(record.latency_ms))

    def on_tool_call(self, record: ToolCallRecord) -> None:
        operation = record.operation.value if record.operation else "unknown"
        output: object | None
        if record.payload is not None:
            output = record.payload.model_dump(mode="json")
        elif record.issues:
            output = {"issues": [issue.model_dump(mode="json") for issue in record.issues]}
        else:
            output = None
        kwargs: dict[str, object] = {
            "input": record.raw_request,
            "output": output,
            "metadata": _clean(
                {
                    "record_id": record.record_id,
                    "call_id": record.call_id,
                    "path": record.path.value,
                    "agent": record.agent.value if record.agent else None,
                    "tool": record.tool.value if record.tool else None,
                    "status": record.status.value,
                    "failure": record.failure.value if record.failure else None,
                    "attempts": record.attempts,
                    "provider": record.provider,
                    "fetched_at": _iso(record.fetched_at),
                    "latency_ms": record.latency_ms,
                    "started_at": _iso(record.started_at),
                    "ended_at": _iso(record.ended_at),
                }
            ),
        }
        if record.status is not ToolOutcomeStatus.OK:
            kwargs["level"] = "WARNING"
            kwargs["status_message"] = record.failure.value if record.failure else record.status.value
        name = f"tool:{operation}"
        root = self._roots.get(record.trace_id)
        start_ns = time_ns()
        if root is not None:
            span = root.start_span(name, **kwargs)
        else:
            span = self._client.start_span(
                trace_context={"trace_id": record.trace_id}, name=name, **kwargs  # type: ignore[arg-type]
            )
        span.end(end_time=start_ns + _ns(record.latency_ms))

    def on_event(self, event: TraceEvent) -> None:
        metadata = _clean({**event.attributes, "at": _iso(event.at)})
        root = self._roots.get(event.trace_id)
        if root is not None:
            root.create_event(name=event.name, metadata=metadata)
        else:
            self._client.create_event(
                trace_context={"trace_id": event.trace_id}, name=event.name, metadata=metadata
            )

    def on_trace_end(self, trace: TraceContext, output: str | None) -> None:
        root = self._roots.pop(trace.trace_id, None)
        if root is None:
            return
        root.update(output=output)
        root.update_trace(output=output)
        root.end()

    def flush(self) -> None:
        self._client.flush()


def _ns(milliseconds: float) -> int:
    return max(0, int(milliseconds * 1_000_000))
