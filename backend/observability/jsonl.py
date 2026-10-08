"""JSON-lines sink: one JSON object per record in `<log_dir>/calls-YYYYMMDD.jsonl`.

Appendix B: "The system must log every web search and API call to guarantee that its
recommendations are based on real data". The file is the durable, greppable copy of that log.
Each line carries a `type` field (trace_start, llm_call, tool_call, event, trace_end) followed by
the record exactly as `model_dump(mode="json")` renders it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from backend.schemas.common import utcnow
from backend.schemas.observability import LLMCallRecord, ToolCallRecord, TraceContext, TraceEvent


class JsonlSink:
    def __init__(self, log_dir: Path) -> None:
        self._dir = Path(log_dir)

    # ---- CallSink protocol --------------------------------------------------------------------

    def on_trace_start(self, trace: TraceContext) -> None:
        self._write("trace_start", trace)

    def on_llm_call(self, record: LLMCallRecord) -> None:
        self._write("llm_call", record)

    def on_tool_call(self, record: ToolCallRecord) -> None:
        self._write("tool_call", record)

    def on_event(self, event: TraceEvent) -> None:
        self._write("event", event)

    def on_trace_end(self, trace: TraceContext, output: str | None) -> None:
        self._write("trace_end", trace, ended_at=utcnow(), output=output)

    def flush(self) -> None:
        """Nothing to flush: every record is appended and the file closed immediately."""

    # ---- helpers --------------------------------------------------------------------------------

    def path_for(self, when: datetime) -> Path:
        return self._dir / f"calls-{when:%Y%m%d}.jsonl"

    def _write(self, kind: str, model: BaseModel, **extra: object) -> None:
        payload: dict[str, object] = {"type": kind, **model.model_dump(mode="json")}
        for key, value in extra.items():
            payload[key] = value.isoformat() if isinstance(value, datetime) else value
        line = json.dumps(payload, ensure_ascii=False, default=str)
        self._dir.mkdir(parents=True, exist_ok=True)
        # newline="\n": one JSON object per line on every platform (no CRLF on Windows).
        with self.path_for(utcnow()).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
