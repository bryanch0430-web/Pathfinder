import logging
import time
from typing import Any, Optional

from app.core.config import settings
from app.services.llm.base import LLMProvider, LLMResult, ToolCall
from app.services.llm.providers.gemini import GeminiProvider
from app.services.llm.providers.groq import GroqProvider
from app.services.llm.providers.deepseek import DeepSeekProvider

logger = logging.getLogger(__name__)

WELCOME_SYSTEM_INSTRUCTION = (
    "You are Pathfinder's friendly, inspiring AI travel-planning companion. "
    "Your role is to welcome the traveler naturally and ask where they would like to travel. "
    "Keep your response concise and conversational (1 to 2 sentences maximum). "
    "If a traveler's name is provided, personalize the greeting warmly with their name. "
    "Do not invent trip details, do not generate an itinerary, and do not ask multiple questions."
)


_UNSET = object()


def model_for(role: str) -> dict[str, str]:
    """Select an extraction model belonging to the configured provider."""
    provider = settings.LLM_PROVIDER.strip().lower()
    fast_model = {"groq": settings.GROQ_FAST_MODEL, "deepseek": settings.DEEPSEEK_FAST_MODEL}.get(provider, "")
    model = fast_model if role == "fast" else ""
    return {"model": model} if model else {}


def default_fallback() -> Any:
    """What runs when the first Groq key hits a limit: the second Groq key, then Gemini.

    The fallback is itself an LLMService, so the chain is Groq key 1 -> Groq key 2 -> Gemini, each step
    taken only on a rate-limit or quota error. Without a second key it goes straight to Gemini.
    ponytail: every call still tries key 1 first, so once it's out each call pays one fast 429 before
    key 2; add a cooldown on the exhausted key if that round trip ever shows up in latency.
    """
    if settings.GROQ_API_KEY2:
        return LLMService(primary_provider=GroqProvider(api_key=settings.GROQ_API_KEY2),
                          fallback_provider=GeminiProvider())
    return GeminiProvider()


class LLMService:
    """
    Application-level LLM Service.
    Coordinates primary (Groq) and fallback (second Groq key, then Gemini) providers, builds structured prompts,
    and provides transparent fallback when the primary provider encounters rate limits or quota limits.
    """

    def __init__(
        self,
        primary_provider: Any = _UNSET,
        fallback_provider: Any = _UNSET,
        provider: Any = _UNSET,
    ):
        self._follow_server_menu = False
        if provider is not _UNSET:
            self._primary_provider = provider
            self._fallback_provider = fallback_provider if fallback_provider is not _UNSET else None
        else:
            selected = settings.LLM_PROVIDER.strip().lower()
            if primary_provider is not _UNSET:
                self._primary_provider = primary_provider
            elif selected == "deepseek":
                self._primary_provider = DeepSeekProvider()
            elif selected == "gemini":
                self._primary_provider = GeminiProvider()
            elif selected == "groq":
                self._primary_provider = GroqProvider()
            else:
                raise ValueError(f"Unsupported LLM_PROVIDER: {selected}")
            self._fallback_provider = fallback_provider if fallback_provider is not _UNSET else (
                default_fallback() if selected == "groq" else None
            )
            self._follow_server_menu = primary_provider is _UNSET

    @property
    def _provider(self) -> LLMProvider:
        """Compatibility property for legacy provider references."""
        return self._primary_provider

    @_provider.setter
    def _provider(self, val: LLMProvider) -> None:
        self._primary_provider = val

    def set_provider(self, provider: LLMProvider) -> None:
        """Allow runtime swapping of the primary LLM provider."""
        self._primary_provider = provider

    def set_providers(
        self,
        primary: LLMProvider,
        fallback: Optional[LLMProvider] = None,
    ) -> None:
        """Allow runtime swapping of both primary and fallback LLM providers."""
        self._primary_provider = primary
        self._fallback_provider = fallback
        self._follow_server_menu = False

    def _writer(self, override: Any) -> Any:
        """A pasted key wins. Otherwise Poe or Grok from the server file, then DeepSeek."""
        if override is not None:
            return override
        if self._follow_server_menu:
            from app.services.byok import server_provider
            chosen = server_provider()
            if chosen is not None:
                return chosen
        return self._primary_provider

    @staticmethod
    def _model_name(writer: Any) -> str:
        return str(getattr(writer, "_model", None) or getattr(writer, "_model_name", None) or settings.DEEPSEEK_MODEL or "")

    def _tune(self, writer: Any, temperature: float, kwargs: dict) -> tuple[float, dict]:
        """A saved temperature or reasoning effort replaces the caller's default."""
        chosen = getattr(writer, "sampling_temperature", None)
        effort = getattr(writer, "sampling_reasoning", None)
        if writer is self._primary_provider and self._follow_server_menu:
            from app.services.byok import server_sampling
            sample = server_sampling()
            chosen = chosen or sample["temperature"]
            effort = effort or sample["reasoning"]
        if chosen not in (None, ""):
            temperature = float(chosen)
        if effort:
            kwargs = {**kwargs, "reasoning_effort": effort}
        return temperature, kwargs

    def _is_provider_limit_error(self, exc: Exception) -> bool:
        """
        Determine if an exception represents a rate limit, quota exhaustion, or capacity constraint.
        Distinguishes limit errors from authentication, validation, or malformed request errors.
        """
        exc_type_name = type(exc).__name__

        # Direct rate limit exception types across SDKs (Groq, OpenAI, Google)
        if "RateLimit" in exc_type_name or "Quota" in exc_type_name or "ResourceExhausted" in exc_type_name:
            return True

        # HTTP 429 Status Code check
        if hasattr(exc, "status_code") and exc.status_code == 429:
            return True
        if hasattr(exc, "response") and getattr(exc.response, "status_code", None) == 429:
            return True

        # Check for explicit message patterns
        msg = str(exc).lower()
        limit_keywords = [
            "rate limit",
            "rate_limit",
            "quota exceeded",
            "insufficient_quota",
            "too many requests",
            "resource_exhausted",
            "tokens per minute",
            "requests per minute",
            "tpm limit",
            "rpm limit",
            "429",
        ]
        # Ensure we don't catch authentication or invalid request errors that mention keys
        auth_keywords = ["unauthorized", "invalid api key", "authentication", "forbidden", "401", "403"]
        if any(ak in msg for ak in auth_keywords) and "429" not in msg:
            return False

        return any(lk in msg for lk in limit_keywords)

    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        """
        Generate completion text via the primary provider, automatically retrying with fallback on limit failure.
        """
        from app.services.usage_log import record

        started = time.perf_counter()
        writer = self._writer(await self._byok())
        temperature, kwargs = self._tune(writer, temperature, kwargs)
        model = self._model_name(writer)
        text = ""
        try:
            if writer is not self._primary_provider:
                text = await writer.generate(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    **kwargs,
                )
                return text
            try:
                text = await self._primary_provider.generate(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    **kwargs,
                )
                return text
            except Exception as exc:
                if self._fallback_provider and self._is_provider_limit_error(exc):
                    logger.warning(
                        "Primary LLM provider (%s) limit/quota reached (%s: %s). Falling back to %s.",
                        type(self._primary_provider).__name__,
                        type(exc).__name__,
                        exc,
                        type(self._fallback_provider).__name__,
                    )
                    model = self._model_name(self._fallback_provider)
                    text = await self._fallback_provider.generate(
                        prompt=prompt,
                        system_instruction=system_instruction,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        **kwargs,
                    )
                    return text
                raise
        finally:
            record("generate", model, prompt or "", text or "", started)

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
        Generate completion text with tool calls via primary provider with automatic fallback on limit failure.
        """
        writer = self._writer(await self._byok())
        temperature, kwargs = self._tune(writer, temperature, kwargs)
        if writer is not self._primary_provider:
            return await writer.generate_with_tools(
                prompt=prompt,
                system_instruction=system_instruction,
                tools=tools,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                **kwargs,
            )
        try:
            return await self._primary_provider.generate_with_tools(
                prompt=prompt,
                system_instruction=system_instruction,
                tools=tools,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                **kwargs,
            )
        except Exception as exc:
            if self._fallback_provider and self._is_provider_limit_error(exc):
                logger.warning(
                    "Primary LLM provider (%s) limit/quota reached in generate_with_tools (%s: %s). Falling back to %s.",
                    type(self._primary_provider).__name__,
                    type(exc).__name__,
                    exc,
                    type(self._fallback_provider).__name__,
                )
                return await self._fallback_provider.generate_with_tools(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    tools=tools,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    **kwargs,
                )
            raise

    async def generate_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        """
        Stream completion tokens via primary provider with automatic fallback on limit failure.
        """
        from app.services.usage_log import record

        started = time.perf_counter()
        writer = self._writer(await self._byok())
        temperature, kwargs = self._tune(writer, temperature, kwargs)
        model = self._model_name(writer)
        pieces: list[str] = []
        try:
            source = writer if writer is not self._primary_provider else self._primary_provider
            try:
                async for token in source.generate_stream(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    **kwargs,
                ):
                    pieces.append(token)
                    yield token
            except Exception as exc:
                if source is self._primary_provider and self._fallback_provider and self._is_provider_limit_error(exc):
                    logger.warning(
                        "Primary LLM provider (%s) limit/quota reached in generate_stream (%s: %s). Falling back to %s.",
                        type(self._primary_provider).__name__,
                        type(exc).__name__,
                        exc,
                        type(self._fallback_provider).__name__,
                    )
                    model = self._model_name(self._fallback_provider)
                    async for token in self._fallback_provider.generate_stream(
                        prompt=prompt,
                        system_instruction=system_instruction,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        **kwargs,
                    ):
                        pieces.append(token)
                        yield token
                else:
                    raise
        finally:
            record("generate_stream", model, prompt or "", "".join(pieces), started)

    async def _byok(self):
        """The chosen bring-your-own writer, or None when the server key is in use."""
        from app.core.database import async_session_maker
        from app.services.byok import PROVIDERS, active_key, decrypt_key
        from app.services.llm.providers.byok_chat import ByokChatProvider

        async with async_session_maker() as db:
            row = await active_key(db)
        if row is None:
            return None
        spec = PROVIDERS.get(row.provider)
        if spec is None:
            return None
        try:
            secret = decrypt_key(row.key_ciphertext)
        except ValueError:
            logger.warning("The chosen model key can no longer be read. The server key is not used in its place.")
            return None
        return ByokChatProvider(
            api_key=secret, base_url=spec.base_url, model=row.model, wire=spec.wire,
            sampling_temperature=row.temperature, sampling_reasoning=row.reasoning,
        )

    async def generate_welcome_greeting(
        self,
        user_name: Optional[str] = None,
        user_message: Optional[str] = None,
    ) -> str:
        """
        Generate a dynamic, personalized welcome greeting for initializing a trip session.
        Safely falls back to a deterministic greeting if both providers fail.
        """
        prompt_parts = []
        if user_name:
            prompt_parts.append(f"Traveler Name: {user_name}")
        else:
            prompt_parts.append("Traveler: Guest / New Explorer")

        if user_message and user_message.strip():
            prompt_parts.append(f"Initial Note: {user_message.strip()}")

        prompt_parts.append(
            "Generate a warm welcome greeting inviting the traveler to share where they'd like to travel."
        )
        prompt = "\n".join(prompt_parts)

        try:
            response = await self.generate(
                prompt=prompt,
                system_instruction=WELCOME_SYSTEM_INSTRUCTION,
                temperature=0.7,
                max_output_tokens=500,
            )
            if response and response.strip():
                return response.strip()

        except Exception as exc:
            logger.warning(
                f"LLM generation failed ({type(exc).__name__}). Using safe fallback greeting."
            )

        # Resilient Fallback
        if user_name:
            return f"Welcome to Pathfinder, {user_name}! Where would you like to travel?"
        return "Welcome to Pathfinder! Where would you like to travel?"


llm_service = LLMService()
