"""Per-turn observability: one object collects every span, model call and tool run.

The UI renders this from the final ``trace`` SSE frame (see ``RunTrace`` in the
wire model), so the shape here is deliberately the same dictionary.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class TurnTrace:
    request_id: str
    started: float = field(default_factory=time.perf_counter)
    spans: list[dict[str, Any]] = field(default_factory=list)
    generations: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    searches: list[dict[str, Any]] = field(default_factory=list)
    why: list[str] = field(default_factory=list)
    flow: list[str] = field(default_factory=list)
    path: dict[str, Any] | None = None
    reason: str = ""
    message: str = ""

    @asynccontextmanager
    async def span(self, name: str) -> AsyncIterator[dict[str, Any]]:
        started = time.perf_counter()
        record: dict[str, Any] = {"name": name, "duration_ms": None, "status": "ok"}
        self.spans.append(record)
        try:
            yield record
        except Exception:
            record["status"] = "error"
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)

    def generation(
        self,
        *,
        purpose: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        reasoning: str = "",
        output_preview: str = "",
        user_preview: str = "",
    ) -> None:
        self.generations.append(
            {
                "purpose": purpose,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_ms": round(latency_ms, 1),
                "reasoning": reasoning,
                "output_preview": output_preview[:1200],
                "user_preview": user_preview[:600],
            }
        )

    def tool(self, *, name: str, status: str, latency_ms: float, args: dict | None = None, note: str = "") -> None:
        self.tools.append(
            {
                "name": name,
                "status": status,
                "latency_ms": round(latency_ms, 1),
                "args": args or {},
                "note": note[:400],
            }
        )

    def search(self, *, provider: str, query: str, results: list[dict[str, Any]] | None = None) -> None:
        self.searches.append({"provider": provider, "query": query, "results": results or []})

    def say(self, line: str) -> None:
        """A human-readable reason shown in the chat's 'why' fold."""
        if line and line not in self.why:
            self.why.append(line)

    def step(self, line: str) -> None:
        if line and line not in self.flow:
            self.flow.append(line)

    @property
    def tool_calls(self) -> int:
        return len(self.tools)

    @property
    def total_tokens(self) -> int:
        return sum(int(gen.get("prompt_tokens", 0)) + int(gen.get("completion_tokens", 0)) for gen in self.generations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "spans": self.spans,
            "generations": self.generations,
            "why": self.why,
            "flow": self.flow,
            "path": self.path,
            "reason": self.reason,
            "message": self.message,
            "searches": self.searches,
            "tools": self.tools,
            "tool_calls": self.tool_calls,
        }
