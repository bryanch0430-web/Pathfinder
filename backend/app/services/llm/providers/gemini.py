import logging
from typing import Any, Optional
from google import genai
from google.genai import types

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


class GeminiProvider(LLMProvider):
    """
    Google Gemini provider implementing the LLMProvider abstraction using Google GenAI SDK.
    Handles client lifecycle, model invocation, and provider-specific error catching.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._model_name = model or model_name or settings.GEMINI_MODEL or "gemini-3.1-flash-lite"

    def _get_client(self) -> genai.Client:
        """Create or return client bound to current execution environment."""
        if not self._api_key:
            raise ValueError("GEMINI_API_KEY is not configured.")
        return genai.Client(api_key=self._api_key)

    @traceable(name="gemini_generate", run_type="llm")
    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        """
        Generate completion text asynchronously via Gemini API.
        """
        try:
            client = self._get_client()

            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )

            response = await client.aio.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=config,
            )

            if response and response.text:
                return response.text.strip()

            return ""

        except Exception as e:
            logger.error(f"Gemini API invocation error: {type(e).__name__} - {str(e)}")
            raise

    @traceable(name="gemini_generate_with_tools", run_type="llm")
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
        Generate completion text and capture tool calls via Gemini API.
        """
        try:
            client = self._get_client()

            config_kwargs: dict[str, Any] = {
                "system_instruction": system_instruction,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
            }
            if tools:
                config_kwargs["tools"] = tools

            config = types.GenerateContentConfig(**config_kwargs)

            response = await client.aio.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=config,
            )

            tool_calls = []
            if response and response.function_calls:
                for fc in response.function_calls:
                    tool_calls.append(ToolCall(name=fc.name, args=dict(fc.args or {})))

            text = ""
            if response and response.text:
                text = response.text.strip()

            return LLMResult(text=text, tool_calls=tool_calls)

        except Exception as e:
            logger.error(f"Gemini tool invocation error: {type(e).__name__} - {str(e)}")
            raise

    @traceable(name="gemini_generate_stream", run_type="llm")
    async def generate_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        """
        Generate completion tokens asynchronously as an SSE/token stream via Gemini API.
        """
        try:
            client = self._get_client()

            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )

            stream = await client.aio.models.generate_content_stream(
                model=self._model_name,
                contents=prompt,
                config=config,
            )

            async for chunk in stream:
                if chunk and chunk.text:
                    yield chunk.text

        except Exception as e:
            logger.error(f"Gemini streaming API invocation error: {type(e).__name__} - {str(e)}")
            raise
