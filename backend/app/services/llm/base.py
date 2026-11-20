from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolCall:
    """Represents a function or tool call emitted by an LLM."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResult:
    """Standardized response from an LLM invocation supporting text and tool calls."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    def __str__(self) -> str:
        return self.text


class LLMProvider(ABC):
    """
    Abstract contract for LLM providers in Pathfinder.
    Isolates agent nodes and workflows from specific provider SDKs and client interfaces.
    """

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        """
        Generate completion text asynchronously given a prompt and optional parameters.
        """
        pass

    async def generate_with_tools(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        tools: Optional[list[Any]] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResult:
        """
        Generate completion with tool calling capabilities.
        Default implementation delegates to generate without tools.
        """
        text = await self.generate(
            prompt=prompt,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            **kwargs,
        )
        return LLMResult(text=text, tool_calls=[])

    async def generate_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        """
        Generate completion tokens asynchronously as an async stream.
        Default implementation yields the full generation if streaming not overridden.
        """
        result = await self.generate(
            prompt=prompt,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            **kwargs,
        )
        if result:
            yield result
