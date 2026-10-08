"""Model client interface and provisional vendor clients.

Team decision: no agent framework. A model client does exactly one thing: send the given
`LLMRequest` and return the raw text. It never adds, rewrites or reorders messages, so what we
log is exactly what the model saw.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.schemas.llm import LLMRequest, LLMResponse
from backend.settings import Settings


class LLMError(Exception):
    """Transport or provider failure of a model call."""


@runtime_checkable
class LLMClient(Protocol):
    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...


class XAIClient:
    """TODO(provisional): Grok 4.7 via the xAI API (Appendix B) for agents, planner, note
    cleaning and chat. Not implemented until model assignment is fixed."""

    name = "xai"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError("TODO(provisional): xAI Grok client")


class JevRouterClient:
    """TODO(provisional): Jev, the System 1 decision model from TypeSafe AI (Appendix B), called
    for a typed choice, a clarity score and a yes/no gate."""

    name = "jev"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError("TODO(provisional): Jev router client")


class OpenAIJudgeClient:
    """TODO(provisional): secondary judge from a different model family (e.g. GPT-4) so the
    judge never scores its own outputs (self-enhancement bias, Zheng et al. 2023)."""

    name = "openai-judge"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError("TODO(provisional): OpenAI judge client")


def build_router_client(settings: Settings) -> LLMClient:
    if settings.router_provider == "mock":
        from backend.agents.runtime.mock_llm import MockLLMClient

        return MockLLMClient(model_name="mock-router")
    return JevRouterClient(settings)


def build_agent_client(settings: Settings) -> LLMClient:
    if settings.agent_llm_provider == "mock":
        from backend.agents.runtime.mock_llm import MockLLMClient

        return MockLLMClient(model_name="mock-agent")
    return XAIClient(settings)


def build_judge_client(settings: Settings) -> LLMClient:
    if settings.judge_provider == "mock":
        from backend.agents.runtime.mock_llm import MockLLMClient

        return MockLLMClient(model_name="mock-judge")
    return OpenAIJudgeClient(settings)
