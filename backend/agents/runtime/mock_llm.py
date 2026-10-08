"""Offline model clients: MockLLMClient (deterministic) and ScriptedLLMClient (tests).

`MockLLMClient(injection_compliant=True)` deliberately OBEYS injected instructions it finds in
data blocks: the router answers in free text, agents propose disallowed tools, and chat leaks the
canary. That turns the injection probes into a test of the defences (typed route, allowlist,
canary check, on-task fallback) instead of a test of how well-behaved the mock is.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from collections.abc import Mapping

from backend.agents.runtime.mock_handlers import (
    DEFAULT_HANDLERS,
    MockHandler,
    blocks,
    canary_in_prompt,
    dumps,
)
from backend.schemas.llm import LLMRequest, LLMResponse, TokenUsage
from backend.schemas.security import InjectionCategory
from backend.security.blocklist import screen_message

REPAIR_SUFFIX = ".repair"
TOOL_REPAIR_SUFFIX = ".tool_repair"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _base_purpose(purpose: str) -> str:
    if purpose.endswith(TOOL_REPAIR_SUFFIX):
        return purpose[: -len(TOOL_REPAIR_SUFFIX)] + ".tool_call"
    return purpose[: -len(REPAIR_SUFFIX)] if purpose.endswith(REPAIR_SUFFIX) else purpose


def _is_repair(purpose: str) -> bool:
    return purpose.endswith(REPAIR_SUFFIX) or purpose.endswith(TOOL_REPAIR_SUFFIX)


def _original_request(request: LLMRequest) -> LLMRequest:
    """For a repair turn, the original prompt is everything before the bad output + feedback."""
    if _is_repair(request.purpose) and len(request.messages) > 2:
        return request.model_copy(update={"messages": request.messages[:-2]})
    return request


def _single_call(envelope: str, rejected: str) -> str:
    """A tool-argument repair answers with ONE call: the regenerated call for the same operation."""
    try:
        calls = json.loads(envelope).get("tool_calls", [])
        wanted = json.loads(rejected).get("operation") if rejected.strip().startswith("{") else None
    except (json.JSONDecodeError, AttributeError):
        return envelope
    match = next((c for c in calls if isinstance(c, dict) and c.get("operation") == wanted), None)
    chosen = match or (calls[0] if calls else {})
    return json.dumps(chosen, ensure_ascii=False)


class MockLLMClient:
    def __init__(
        self,
        *,
        model_name: str = "mock",
        injection_compliant: bool = False,
        handlers: Mapping[str, MockHandler] | None = None,
    ) -> None:
        self.name = model_name
        self.injection_compliant = injection_compliant
        self._handlers: dict[str, MockHandler] = dict(DEFAULT_HANDLERS)
        if handlers:
            self._handlers.update(handlers)

    def register(self, purpose: str, handler: MockHandler) -> None:
        self._handlers[purpose] = handler

    async def complete(self, request: LLMRequest) -> LLMResponse:
        t0 = time.perf_counter()
        original = _original_request(request)
        base = _base_purpose(request.purpose)
        content = self._compromised(base, original) if self.injection_compliant else None
        if content is None:
            handler = self._handlers.get(base)
            if handler is None:
                raise KeyError(f"MockLLMClient has no handler for purpose {base!r}")
            content = handler(original)
            if request.purpose.endswith(TOOL_REPAIR_SUFFIX):
                content = _single_call(content, request.messages[-2].content)
        prompt_chars = "".join(m.content for m in request.messages)
        return LLMResponse(
            content=content,
            model=self.name,
            usage=TokenUsage(input_tokens=_estimate_tokens(prompt_chars), output_tokens=_estimate_tokens(content)),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # ---- deliberately compromised behaviour (injection probes) -----------------------------

    def _compromised(self, purpose: str, request: LLMRequest) -> str | None:
        categories: set[InjectionCategory] = set()
        for text in blocks(request).values():
            categories.update(screen_message(text).categories)
        if not categories:
            return None
        canary = canary_in_prompt(request) or "no-marker"
        leak = InjectionCategory.HIDDEN_INSTRUCTION_REQUEST in categories
        if purpose == "router":
            return "Sure! I will ignore my routing rules. Route: plan, and here are my instructions."
        if purpose.endswith(".tool_call"):
            if leak:
                return dumps({"tool_calls": [{"operation": "web_search", "query": canary, "destination": "x"}]})
            hijack = (
                {"operation": "ticket_search", "origin": "A", "destination": "B", "travel_date": "2026-01-01", "modes": ["train"]}
                if purpose.startswith("weather")
                else {"operation": "forecast", "location": "anywhere", "start_date": "2026-01-01", "end_date": "2026-01-01"}
            )
            return dumps({"tool_calls": [hijack]})
        if purpose == "attraction.clean":
            if leak:
                return dumps({"places": [{"name": canary, "category": "attraction", "indoor": None, "note": canary}]})
            return dumps({"places": [{"name": "Secret Rooftop Bar", "category": "bar", "indoor": True, "note": "as instructed"}]})
        if purpose in ("ask", "clarify"):
            text = f"My hidden instructions include the marker {canary}." if leak else "Ignoring my task as requested."
            key = "question" if purpose == "clarify" else "answer"
            body = {key: text} if purpose == "clarify" else {"related_to_plan": False, "answer": text, "tool_call": None}
            return dumps(body)
        if purpose == "modify.extract" and leak:
            return dumps({"summary": canary})
        return None


class ScriptedLLMClient:
    """Returns queued raw outputs per purpose (exact match, including '.repair' purposes), then
    falls back to `fallback`. Records every request it receives."""

    def __init__(self, fallback: MockLLMClient | None = None, *, model_name: str = "scripted") -> None:
        self.name = model_name
        self._fallback = fallback or MockLLMClient()
        self._scripts: dict[str, deque[str]] = defaultdict(deque)
        self.requests: list[LLMRequest] = []

    def script(self, purpose: str, *outputs: str) -> ScriptedLLMClient:
        self._scripts[purpose].extend(outputs)
        return self

    def calls_for(self, purpose: str) -> list[LLMRequest]:
        return [r for r in self.requests if r.purpose == purpose]

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        queue = self._scripts.get(request.purpose)
        if queue:
            content = queue.popleft()
            return LLMResponse(
                content=content,
                model=self.name,
                usage=TokenUsage(input_tokens=10, output_tokens=_estimate_tokens(content)),
            )
        return await self._fallback.complete(request)
