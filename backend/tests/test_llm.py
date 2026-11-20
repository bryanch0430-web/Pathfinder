import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services.llm.base import LLMProvider, LLMResult, ToolCall
from app.services.llm.providers.deepseek import DeepSeekProvider
from app.services.llm.providers.gemini import GeminiProvider
from app.services.llm.providers.groq import GroqProvider
from app.services.llm.service import LLMService


class MockProvider(LLMProvider):
    def __init__(self, response_text: str = "Mock response"):
        self.response_text = response_text
        self.last_prompt = None
        self.last_system_instruction = None

    async def generate(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int = 500,
        **kwargs,
    ) -> str:
        self.last_prompt = prompt
        self.last_system_instruction = system_instruction
        return self.response_text


# ==============================================================================
# 1. Groq Provider Unit Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_groq_provider_generate():
    """Verify GroqProvider calls AsyncGroq client and returns formatted text."""
    provider = GroqProvider(api_key="gsk-test-key", model="llama-3.3-70b-versatile")

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Groq travel response"
    mock_choice.message.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch.object(provider, "_get_client", return_value=mock_client):
        result = await provider.generate(
            prompt="Plan 5 days in Kyoto",
            system_instruction="You are a travel guide.",
            temperature=0.7,
            max_output_tokens=1000,
        )

        assert result == "Groq travel response"
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "llama-3.3-70b-versatile"
        assert len(call_kwargs["messages"]) == 2
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_groq_provider_generate_with_tools():
    """Verify GroqProvider converts tool functions and captures returned ToolCalls."""
    provider = GroqProvider(api_key="gsk-test-key", model="llama-3.3-70b-versatile")

    def get_user_location() -> dict:
        """Get location tool."""
        return {"action": "use_current_location"}

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "I'll fetch your location"
    
    mock_tc = MagicMock()
    mock_tc.function.name = "get_user_location"
    mock_tc.function.arguments = "{}"
    mock_choice.message.tool_calls = [mock_tc]

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch.object(provider, "_get_client", return_value=mock_client):
        result = await provider.generate_with_tools(
            prompt="use my location",
            tools=[get_user_location],
        )

        assert isinstance(result, LLMResult)
        assert result.text == "I'll fetch your location"
        assert result.has_tool_calls is True
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_user_location"
        assert result.tool_calls[0].args == {}


@pytest.mark.asyncio
async def test_groq_provider_generate_stream():
    """Verify GroqProvider generate_stream streams token chunks."""
    provider = GroqProvider(api_key="gsk-test-key", model="llama-3.3-70b-versatile")

    mock_client = MagicMock()

    class MockChunk:
        def __init__(self, delta_text):
            mock_choice = MagicMock()
            mock_choice.delta.content = delta_text
            self.choices = [mock_choice]

    async def mock_stream_gen(**kwargs):
        for token in ["Exploring ", "Paris ", "in ", "spring!"]:
            yield MockChunk(token)

    mock_client.chat.completions.create = AsyncMock(side_effect=mock_stream_gen)

    with patch.object(provider, "_get_client", return_value=mock_client):
        tokens = []
        async for t in provider.generate_stream("Tell me about Paris"):
            tokens.append(t)

        assert "".join(tokens) == "Exploring Paris in spring!"


# ==============================================================================
# 2. LLMService Primary (Groq) & Fallback (Gemini) Coordination Tests
# ==============================================================================

def test_llm_service_default_providers():
    """Verify the primary provider follows LLM_PROVIDER; only Groq pairs itself with a Gemini fallback."""
    from app.services.llm import service as svc

    with patch.object(svc.settings, "GROQ_API_KEY2", ""):
        service = LLMService()
    selected = svc.settings.LLM_PROVIDER.lower()
    expected = DeepSeekProvider if selected == "deepseek" else GroqProvider
    assert isinstance(service._primary_provider, expected)
    if selected == "groq":
        assert isinstance(service._fallback_provider, GeminiProvider)
    else:
        assert service._fallback_provider is None


@pytest.mark.asyncio
async def test_llm_service_groq_success_gemini_not_called():
    """Verify when primary Groq succeeds, Gemini fallback is never invoked."""
    mock_groq = MockProvider("Groq response")
    mock_gemini = MockProvider("Gemini response")

    service = LLMService(primary_provider=mock_groq, fallback_provider=mock_gemini)

    result = await service.generate("Plan trip")
    assert result == "Groq response"
    assert mock_groq.last_prompt == "Plan trip"
    assert mock_gemini.last_prompt is None  # Gemini never called


@pytest.mark.asyncio
async def test_llm_service_groq_rate_limit_fallback_to_gemini():
    """Verify when Groq raises rate limit / 429 quota error, service transparently falls back to Gemini."""
    class RateLimitedGroq(LLMProvider):
        async def generate(self, *args, **kwargs) -> str:
            class RateLimitError(Exception):
                status_code = 429
            raise RateLimitError("Groq TPM/RPM limit reached (429 Too Many Requests)")

    mock_gemini = MockProvider("Gemini fallback response")
    service = LLMService(primary_provider=RateLimitedGroq(), fallback_provider=mock_gemini)

    result = await service.generate("Plan 10 days in Italy", system_instruction="Be helpful", temperature=0.5)
    assert result == "Gemini fallback response"
    assert mock_gemini.last_prompt == "Plan 10 days in Italy"
    assert mock_gemini.last_system_instruction == "Be helpful"


@pytest.mark.asyncio
async def test_llm_service_groq_rate_limit_fallback_with_tools():
    """Verify tool calling transparently falls back to Gemini on Groq rate limit."""
    class RateLimitedGroq(LLMProvider):
        async def generate(self, *args, **kwargs) -> str:
            class RateLimitError(Exception):
                status_code = 429
            raise RateLimitError("Rate limit exceeded")

        async def generate_with_tools(self, *args, **kwargs) -> LLMResult:
            class RateLimitError(Exception):
                status_code = 429
            raise RateLimitError("Rate limit exceeded")

    class MockGeminiTools(LLMProvider):
        async def generate(self, *args, **kwargs) -> str:
            return "Gemini text"

        async def generate_with_tools(self, prompt, system_instruction=None, tools=None, **kwargs) -> LLMResult:
            return LLMResult(
                text="Gemini tool text",
                tool_calls=[ToolCall(name="get_user_location", args={})],
            )

    service = LLMService(primary_provider=RateLimitedGroq(), fallback_provider=MockGeminiTools())

    result = await service.generate_with_tools("where am I")
    assert result.text == "Gemini tool text"
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "get_user_location"


@pytest.mark.asyncio
async def test_llm_service_non_limit_error_does_not_fallback():
    """Verify non-limit errors (e.g. auth failure, bad request) raise directly without falling back."""
    class AuthFailedGroq(LLMProvider):
        async def generate(self, *args, **kwargs) -> str:
            raise PermissionError("Invalid API key (401 Unauthorized)")

    mock_gemini = MockProvider("Should not be called")
    service = LLMService(primary_provider=AuthFailedGroq(), fallback_provider=mock_gemini)

    with pytest.raises(PermissionError, match="Invalid API key"):
        await service.generate("Test prompt")

    assert mock_gemini.last_prompt is None


# ==============================================================================
# 3. Welcome Greeting & Gemini Provider Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_llm_service_welcome_authenticated():
    """Verify welcome greeting prompt is built for authenticated users with their name."""
    mock_provider = MockProvider("Welcome back Rohit! Where are we going?")
    service = LLMService(primary_provider=mock_provider)

    greeting = await service.generate_welcome_greeting(user_name="Rohit")
    assert greeting == "Welcome back Rohit! Where are we going?"
    assert "Rohit" in mock_provider.last_prompt


@pytest.mark.asyncio
async def test_llm_service_welcome_guest():
    """Verify welcome greeting prompt is built for guest users."""
    mock_provider = MockProvider("Welcome to Pathfinder! Where are we going?")
    service = LLMService(primary_provider=mock_provider)

    greeting = await service.generate_welcome_greeting(user_name=None)
    assert greeting == "Welcome to Pathfinder! Where are we going?"


@pytest.mark.asyncio
async def test_llm_service_welcome_fallback_on_error():
    """Verify LLMService falls back to safe deterministic greeting when provider fails."""
    class FailingProvider(LLMProvider):
        async def generate(self, *args, **kwargs) -> str:
            raise RuntimeError("API key quota exceeded")

    service = LLMService(primary_provider=FailingProvider(), fallback_provider=None)
    greeting_auth = await service.generate_welcome_greeting(user_name="Rohit")
    assert "Rohit" in greeting_auth
    assert "Where would you like to travel?" in greeting_auth

    greeting_guest = await service.generate_welcome_greeting(user_name=None)
    assert greeting_guest == "Welcome to Pathfinder! Where would you like to travel?"


@pytest.mark.asyncio
async def test_gemini_provider_generate():
    """Verify GeminiProvider calls google-genai client properly."""
    provider = GeminiProvider(api_key="test-api-key", model="gemini-3.6-flash")

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Generated travel response"
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    with patch.object(provider, "_get_client", return_value=mock_client):
        result = await provider.generate("Plan trip", system_instruction="Travel Guide")
        assert result == "Generated travel response"
        mock_client.aio.models.generate_content.assert_called_once()


class LimitedProvider:
    """Answers, or fails with a Groq-style 429, and records who was asked."""

    def __init__(self, name: str, calls: list[str], limited: bool = False):
        self.name, self.calls, self.limited = name, calls, limited

    def _check(self):
        self.calls.append(self.name)
        if self.limited:
            raise Exception("Error code: 429 - rate_limit_exceeded: tokens per day (TPD)")

    async def generate(self, prompt, system_instruction=None, temperature=0.7, max_output_tokens=None, **kwargs):
        self._check()
        return self.name

    async def generate_stream(self, prompt, system_instruction=None, temperature=0.7, max_output_tokens=None, **kwargs):
        self._check()
        yield self.name


@pytest.mark.asyncio
async def test_second_groq_key_is_used_before_gemini():
    """Groq key 1 -> Groq key 2 -> Gemini, each step only on a limit error, for calls and streams alike."""
    from app.services.llm import service as svc

    async def run(key1_limited: bool, key2_limited: bool):
        calls: list[str] = []
        chain = LLMService(
            primary_provider=LimitedProvider("groq-1", calls, key1_limited),
            fallback_provider=LLMService(primary_provider=LimitedProvider("groq-2", calls, key2_limited),
                                         fallback_provider=LimitedProvider("gemini", calls)))
        text = await chain.generate("hi")
        streamed = [t async for t in chain.generate_stream("hi")]
        assert streamed == [text]
        return text, calls[: len(calls) // 2]

    assert await run(False, False) == ("groq-1", ["groq-1"])
    assert await run(True, False) == ("groq-2", ["groq-1", "groq-2"])
    assert await run(True, True) == ("gemini", ["groq-1", "groq-2", "gemini"])

    # The real default wiring: with a second key the fallback is that key, then Gemini; without one, Gemini.
    with patch.object(svc.settings, "GROQ_API_KEY2", "gsk-second"):
        fallback = svc.default_fallback()
        assert isinstance(fallback, LLMService)
        assert fallback._primary_provider._api_key == "gsk-second"
        assert isinstance(fallback._fallback_provider, GeminiProvider)
    with patch.object(svc.settings, "GROQ_API_KEY2", ""):
        assert isinstance(svc.default_fallback(), GeminiProvider)

