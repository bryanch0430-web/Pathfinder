"""Ports: the contracts the application layer depends on.

Infrastructure adapters implement these protocols; tests provide fakes. No
protocol imports an SDK, an HTTP client or a database driver. Only the ports
that are actually consumed are kept here — dead abstractions are worse than no
abstraction.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol, Sequence

from app.domain.models import AgentResult, GeoLocation, Message, RouteDecision, TripContext


class LLM(Protocol):
    name: str
    available: bool

    async def complete(
        self,
        *,
        system: str,
        user: str,
        purpose: str,
        json_mode: bool = True,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> "LLMResult": ...


class LLMResult(Protocol):
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class Router(Protocol):
    async def route(
        self,
        *,
        message: str,
        context: TripContext,
        plan_summary: dict[str, Any] | None,
        history: Sequence[Message],
    ) -> RouteDecision: ...


class Tracer(Protocol):
    request_id: str

    def span(self, name: str, **attrs: Any) -> AbstractContextManager[Any]: ...
    def record_generation(
        self,
        *,
        purpose: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        output_preview: str = "",
        reasoning: str = "",
        user_preview: str = "",
        llm_source: str = "",
    ) -> None: ...
    def finish(self, status: str = "ok") -> None: ...


class Agent(Protocol):
    name: str
    tools: frozenset[str]

    async def run(self, request: "AgentRequest") -> AgentResult: ...


class AgentRequest(Protocol):
    context: TripContext
    destination: GeoLocation
    date_range: Any
    request_id: str
    emit: Any
