import json
import logging
from typing import Any, Optional
from groq import AsyncGroq

from app.core.config import settings
from app.services.llm.base import LLMProvider, LLMResult, ToolCall

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

logger = logging.getLogger(__name__)


class GroqProvider(LLMProvider):
    """
    Groq LLM provider implementing the LLMProvider abstraction using the official AsyncGroq SDK.
    Handles client lifecycle, model invocation, tool calling, and streaming with LangSmith observability.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._api_key = api_key or settings.GROQ_API_KEY
        self._model_name = model or model_name or settings.GROQ_MODEL or "openai/gpt-oss-120b"

    def _get_client(self) -> AsyncGroq:
        """Create or return client bound to current execution environment."""
        if not self._api_key:
            raise ValueError("GROQ_API_KEY is not configured.")
        # No SDK retries: a 429 would otherwise sleep for many seconds before LLMService can
        # switch to the Gemini fallback, which users experience as a frozen chat.
        return AsyncGroq(api_key=self._api_key, max_retries=0)

    def _build_messages(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
    ) -> list[dict[str, str]]:
        messages = []
        if system_instruction and system_instruction.strip():
            messages.append({"role": "system", "content": system_instruction.strip()})
        messages.append({"role": "user", "content": prompt})
        return messages

    @traceable(name="groq_generate", run_type="llm")
    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        """
        Generate completion text asynchronously via Groq API.
        """
        try:
            client = self._get_client()
            messages = self._build_messages(prompt, system_instruction)

            create_kwargs: dict[str, Any] = {
                "model": self._model_name,
                "messages": messages,
                "temperature": temperature,
            }
            if max_output_tokens:
                create_kwargs["max_tokens"] = max_output_tokens
            create_kwargs.update(kwargs)

            response = await client.chat.completions.create(**create_kwargs)

            if response and response.choices and response.choices[0].message:
                content = response.choices[0].message.content
                if content:
                    return content.strip()

            return ""

        except Exception as e:
            logger.error(f"Groq API invocation error: {type(e).__name__} - {str(e)}")
            raise

    @traceable(name="groq_generate_with_tools", run_type="llm")
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
        Generate completion text and capture tool calls via Groq API.
        """
        try:
            client = self._get_client()
            messages = self._build_messages(prompt, system_instruction)

            groq_tools = []
            if tools:
                for t in tools:
                    if isinstance(t, dict):
                        if "type" in t and "function" in t:
                            groq_tools.append(t)
                        else:
                            groq_tools.append({"type": "function", "function": t})
                    elif callable(t):
                        fn_name = getattr(t, "__name__", str(t))
                        fn_doc = getattr(t, "__doc__", "") or f"Tool function {fn_name}"
                        groq_tools.append({
                            "type": "function",
                            "function": {
                                "name": fn_name,
                                "description": fn_doc.strip(),
                                "parameters": {
                                    "type": "object",
                                    "properties": {},
                                    "required": [],
                                },
                            },
                        })

            create_kwargs: dict[str, Any] = {
                "model": self._model_name,
                "messages": messages,
                "temperature": temperature,
            }
            if max_output_tokens:
                create_kwargs["max_tokens"] = max_output_tokens
            if groq_tools:
                create_kwargs["tools"] = groq_tools
                create_kwargs["tool_choice"] = "auto"
            create_kwargs.update(kwargs)

            response = await client.chat.completions.create(**create_kwargs)

            tool_calls = []
            text = ""
            if response and response.choices and response.choices[0].message:
                msg = response.choices[0].message
                if msg.content:
                    text = msg.content.strip()
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        args = {}
                        if tc.function.arguments:
                            try:
                                args = json.loads(tc.function.arguments)
                            except Exception:
                                pass
                        tool_calls.append(ToolCall(name=tc.function.name, args=args))

            return LLMResult(text=text, tool_calls=tool_calls)

        except Exception as e:
            logger.error(f"Groq tool invocation error: {type(e).__name__} - {str(e)}")
            raise

    @traceable(name="groq_generate_stream", run_type="llm")
    async def generate_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        """
        Generate completion tokens asynchronously as an SSE/token stream via Groq API.
        """
        try:
            client = self._get_client()
            messages = self._build_messages(prompt, system_instruction)

            create_kwargs: dict[str, Any] = {
                "model": self._model_name,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
            }
            if max_output_tokens:
                create_kwargs["max_tokens"] = max_output_tokens
            create_kwargs.update(kwargs)

            stream = await client.chat.completions.create(**create_kwargs)

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error(f"Groq streaming API invocation error: {type(e).__name__} - {str(e)}")
            raise
