import json
from unittest.mock import patch

import httpx
import pytest

from app.services.llm.providers.deepseek import DeepSeekProvider
from app.services.llm.service import LLMService, model_for
from app.core.config import settings


def bind_transport(provider, handler):
    return patch.object(provider, "_get_client", side_effect=lambda: httpx.AsyncClient(
        base_url="https://api.deepseek.com/", transport=httpx.MockTransport(handler),
    ))


@pytest.mark.asyncio
async def test_text_request_uses_deepseek_model_and_preserves_json_format():
    provider = DeepSeekProvider(api_key="test-key", model="deepseek-flash")

    def handler(request):
        assert request.url.path == "/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 100
        assert body["messages"][0] == {"role": "system", "content": "Return JSON"}
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"city":"Tokyo"}'}}]})

    with bind_transport(provider, handler):
        assert json.loads(await provider.generate(
            "Tokyo", system_instruction="Return JSON", max_output_tokens=100,
            response_format={"type": "json_object"},
        )) == {"city": "Tokyo"}


@pytest.mark.asyncio
async def test_tools_support_callable_and_schema_and_reject_bad_arguments():
    provider = DeepSeekProvider(api_key="test-key")

    def get_user_location():
        """Read browser location."""

    schema = {"name": "find_city", "parameters": {"type": "object", "properties": {}}}

    def handler(request):
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "get_user_location"
        assert body["tools"][1]["function"] == schema
        return httpx.Response(200, json={"choices": [{"message": {
            "content": None,
            "tool_calls": [{"function": {"name": "get_user_location", "arguments": "{}"}}],
        }}]})

    with bind_transport(provider, handler):
        result = await provider.generate_with_tools("Use my location", tools=[get_user_location, schema])
        assert result.tool_calls[0].name == "get_user_location"
        assert result.tool_calls[0].args == {}

    with bind_transport(provider, lambda _: httpx.Response(200, json={"choices": [{"message": {
        "tool_calls": [{"function": {"name": "find_city", "arguments": "not-json"}}],
    }}]})):
        with pytest.raises(json.JSONDecodeError):
            await provider.generate_with_tools("Tokyo", tools=[schema])


@pytest.mark.asyncio
async def test_stream_reads_content_and_ignores_keepalives():
    provider = DeepSeekProvider(api_key="test-key")
    events = ': keepalive\n\ndata: {"choices":[{"delta":{"content":"Hello "}}]}\n\n'
    events += 'data: {"choices":[{"delta":{"content":"Tokyo"}}]}\n\ndata: [DONE]\n\n'
    with bind_transport(provider, lambda _: httpx.Response(200, text=events)):
        assert "".join([t async for t in provider.generate_stream("Hello")]) == "Hello Tokyo"


@pytest.mark.asyncio
async def test_failures_are_not_reported_as_success():
    provider = DeepSeekProvider(api_key="test-key")
    with bind_transport(provider, lambda _: httpx.Response(401, json={"error": {"message": "Unauthorized"}})):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.generate("Hello")
    with bind_transport(provider, lambda _: httpx.Response(200, text='data: {"choices":[]}\n\n')):
        with pytest.raises(RuntimeError, match="completion marker"):
            _ = [t async for t in provider.generate_stream("Hello")]
    with bind_transport(provider, lambda _: httpx.Response(200, json={"choices": [{
        "finish_reason": "length", "message": {"content": "partial"},
    }]})):
        with pytest.raises(ValueError, match="token budget"):
            await provider.generate("Hello")


def test_deepseek_selection_and_fast_model_never_use_groq():
    with patch.object(settings, "LLM_PROVIDER", "deepseek"):
        service = LLMService()
        assert isinstance(service._primary_provider, DeepSeekProvider)
        assert service._fallback_provider is None
        assert model_for("fast") == {"model": settings.DEEPSEEK_FAST_MODEL}
        assert model_for("reply") == {}


def test_missing_key_fails_locally():
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider(api_key="")._get_client()
