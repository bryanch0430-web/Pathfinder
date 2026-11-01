"""Per-turn tracing: JSONL on disk always, Langfuse when configured.

No personal content is written to the Langfuse sink beyond the prompt/answer
preview controlled by the caller; tool arguments are stored locally only.
"""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import httpx

from app.config import Settings
from app.domain.models import new_id


class Span:
    def __init__(self, name: str, trace_id: str) -> None:
        self.id = new_id("span")
        self.name = name
        self.trace_id = trace_id
        self.started_at = datetime.now(timezone.utc)
        self.ended_at: datetime | None = None
        self.attrs: dict[str, Any] = {}
        self.children: list["Span"] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "name": self.name,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": int((self.ended_at - self.started_at).total_seconds() * 1000) if self.ended_at else None,
            "attrs": self.attrs,
        }


class TraceRecorder:
    def __init__(self, request_id: str, settings: Settings, sink: "LangfuseSink | None" = None):
        self.request_id = request_id
        self.settings = settings
        self.sink = sink
        self.started_at = datetime.now(timezone.utc)
        self.spans: list[Span] = []
        self.generations: list[dict[str, Any]] = []
        self.metadata: dict[str, Any] = {}
        self._stack: list[Span] = []
        self.finished = False

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Span]:
        span = Span(name, self.request_id)
        span.attrs.update(attrs)
        parent = self._stack[-1] if self._stack else None
        if parent:
            parent.children.append(span)
        self._stack.append(span)
        try:
            yield span
        finally:
            self._stack.pop()
            span.ended_at = datetime.now(timezone.utc)
            self.spans.append(span)

    def record_generation(
        self,
        *,
        purpose: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        output_preview: str = "",
    ) -> None:
        self.generations.append(
            {
                "id": new_id("gen"),
                "purpose": purpose,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_ms": latency_ms,
                "output_preview": output_preview[:500],
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def finish(self, status: str = "ok") -> None:
        if self.finished:
            return
        self.finished = True
        record = {
            "request_id": self.request_id,
            "status": status,
            "started_at": self.started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int((datetime.now(timezone.utc) - self.started_at).total_seconds() * 1000),
            "metadata": self.metadata,
            "spans": [span.to_dict() for span in self.spans],
            "generations": self.generations,
        }
        try:
            self.settings.trace_dir.mkdir(parents=True, exist_ok=True)
            path = self.settings.trace_dir / f"{self.request_id}.json"
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        if self.sink and self.sink.available:
            threading.Thread(target=self.sink.send, args=(record,), daemon=True).start()


class _NoopSpan:
    def __init__(self, **attrs: Any):
        self.attrs = dict(attrs)


class NoopTracer:
    request_id = "noop"

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[_NoopSpan]:
        yield _NoopSpan(**attrs)

    def record_generation(self, **_: Any) -> None:
        return None

    def finish(self, status: str = "ok") -> None:
        return None


def _ns(value: str) -> str:
    return str(int(datetime.fromisoformat(value).timestamp() * 1_000_000_000))


def _otlp_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _hex_id(seed: str, size: int) -> str:
    return hashlib.md5(seed.encode("utf-8")).hexdigest()[:size]


def _otlp_payload(record: dict[str, Any]) -> dict[str, Any]:
    """OTLP/HTTP JSON for Langfuse's supported /otel/v1/traces endpoint.

    Langfuse retired the v3 ingestion API; the OpenTelemetry endpoint is the
    supported write path. Trace and span ids must be lowercase hex.
    """
    trace_id = _hex_id(str(record["request_id"]), 32)
    root_id = _hex_id(str(record["request_id"]) + ":root", 16)
    spans: list[dict[str, Any]] = [
        {
            "traceId": trace_id,
            "spanId": root_id,
            "name": "pathfinder.turn",
            "kind": 1,
            "startTimeUnixNano": _ns(record["started_at"]),
            "endTimeUnixNano": _ns(record["ended_at"]),
            "attributes": [
                {"key": "pathfinder.status", "value": _otlp_value(str(record.get("status") or "ok"))},
                {"key": "pathfinder.duration_ms", "value": _otlp_value(int(record.get("duration_ms") or 0))},
                {"key": "pathfinder.request_id", "value": _otlp_value(str(record["request_id"]))},
                *[
                    {"key": f"pathfinder.meta.{key}", "value": _otlp_value(value)}
                    for key, value in (record.get("metadata") or {}).items()
                ],
            ],
        }
    ]
    for span in record.get("spans") or []:
        if not span.get("ended_at"):
            continue
        spans.append(
            {
                "traceId": trace_id,
                "spanId": _hex_id(str(span.get("id") or ""), 16),
                "parentSpanId": root_id,
                "name": str(span.get("name") or "span"),
                "kind": 1,
                "startTimeUnixNano": _ns(span["started_at"]),
                "endTimeUnixNano": _ns(span["ended_at"]),
                "attributes": [
                    {"key": key, "value": _otlp_value(value)}
                    for key, value in (span.get("attrs") or {}).items()
                ],
            }
        )
    for generation in record.get("generations") or []:
        started = datetime.fromisoformat(generation["at"])
        ended = started + timedelta(milliseconds=int(generation.get("latency_ms") or 0))
        spans.append(
            {
                "traceId": trace_id,
                "spanId": _hex_id(str(generation.get("id") or ""), 16),
                "parentSpanId": root_id,
                "name": str(generation.get("purpose") or "generation"),
                "kind": 1,
                "startTimeUnixNano": str(int(started.timestamp() * 1e9)),
                "endTimeUnixNano": str(int(ended.timestamp() * 1e9)),
                "attributes": [
                    {"key": "langfuse.observation.type", "value": {"stringValue": "generation"}},
                    {"key": "gen_ai.request.model", "value": _otlp_value(str(generation.get("model") or ""))},
                    {
                        "key": "gen_ai.usage.input_tokens",
                        "value": _otlp_value(int(generation.get("prompt_tokens") or 0)),
                    },
                    {
                        "key": "gen_ai.usage.output_tokens",
                        "value": _otlp_value(int(generation.get("completion_tokens") or 0)),
                    },
                    {"key": "pathfinder.latency_ms", "value": _otlp_value(int(generation.get("latency_ms") or 0))},
                ],
            }
        )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "pathfinder"}}]
                },
                "scopeSpans": [{"scope": {"name": "pathfinder"}, "spans": spans}],
            }
        ]
    }


class LangfuseSink:
    """Langfuse over OpenTelemetry (the supported ingestion path)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = settings.langfuse_available
        self.base = settings.langfuse_host.rstrip("/")

    def send(self, record: dict[str, Any]) -> None:
        if not self.available:
            return
        try:
            response = httpx.post(
                f"{self.base}/api/public/otel/v1/traces",
                auth=(self.settings.langfuse_public_key, self.settings.langfuse_secret_key),
                json=_otlp_payload(record),
                timeout=8,
            )
            response.raise_for_status()
        except Exception:
            # Observability must never break a turn.
            return


_current_tracer: ContextVar[Any] = ContextVar("pathfinder_tracer", default=NoopTracer())


class TracerProxy:
    """Delegates to the tracer bound to the current async task.

    Application services are long-lived singletons; the tracer is per turn. A
    context variable keeps concurrent sessions from writing into each other's
    trace without rebuilding the whole service graph per request.
    """

    @property
    def _tracer(self):
        return _current_tracer.get()

    @property
    def request_id(self) -> str:
        return self._tracer.request_id

    def bind(self, tracer) -> None:
        _current_tracer.set(tracer)

    @contextmanager
    def span(self, name: str, **attrs: Any):
        with self._tracer.span(name, **attrs) as span:
            yield span

    def record_generation(self, **kwargs: Any) -> None:
        self._tracer.record_generation(**kwargs)

    def finish(self, status: str = "ok") -> None:
        self._tracer.finish(status)


def start_trace(settings: Settings, request_id: str | None = None) -> TraceRecorder:
    request_id = request_id or new_id("req")
    sink = LangfuseSink(settings) if settings.langfuse_available else None
    return TraceRecorder(request_id, settings, sink)
