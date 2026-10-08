"""Model call contracts.

Team decision: no agent framework. Every prompt is an explicit `LLMRequest` built by our code,
sent as-is, and logged as-is (no hidden prompt mutation). `purpose` names the call site for
tracing and is never sent to the model.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from backend.schemas.common import StrictModel


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(StrictModel):
    role: Role
    content: str


class LLMRequest(StrictModel):
    purpose: str = Field(min_length=1, description="Call-site label, e.g. 'router', 'planner'")
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=2048, ge=1)
    json_output: bool = True


class TokenUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMResponse(StrictModel):
    content: str
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: float = 0.0
