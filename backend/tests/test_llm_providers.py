"""Provider catalogue, the Anthropic adapter, key scrubbing and the key test."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import FailureKind
from app.infrastructure import anthropic_llm
from app.infrastructure.anthropic_llm import AnthropicLLM
from app.infrastructure.http import override_default_transport, use_transport
from app.infrastructure.llm import OpenAICompatibleLLM
from app.infrastructure.llm_providers import PROVIDERS, build_llm, list_models, probe_key
from app.security.scrub import scrub


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


OPENAI_OK = {
    "model": "m",
    "choices": [{"message": {"content": "OK"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}
ANTHROPIC_OK = {
    "model": "claude-x",
    "content": [{"type": "text", "text": "OK"}],
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


def _run(coro):
    return asyncio.run(coro)


def _complete(adapter, **kw):
    args = dict(system="sys", user="usr", purpose="t", json_mode=False)
    args.update(kw)
    return _run(adapter.complete(**args))


def test_scrub_replaces_long_secrets_only() -> None:
    assert scrub("key sk-SECRET here", ["sk-SECRET"]) == "key [redacted] here"
    assert scrub("abc abcde", ["abc", "abcde", ""]) == "abc abcde"


def test_catalogue_has_five_providers() -> None:
    assert set(PROVIDERS) == {"anthropic", "openai", "poe", "xai", "gemini"}
    assert {k for k, v in PROVIDERS.items() if v.supports_search} == {"xai"}
    assert PROVIDERS["anthropic"].default_model == "claude-sonnet-5-5"
    assert PROVIDERS["xai"].default_model == "grok-4.20-0309-non-reasoning"
    assert {k: v.key_prefix for k, v in PROVIDERS.items()} == {
        "anthropic": "sk-ant-", "openai": "sk-", "xai": "xai-", "gemini": "AIza", "poe": "",
    }


def test_openai_wire_hits_provider_url(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OPENAI_OK)

    key = "AIzaTESTKEY123"
    adapter = build_llm(PROVIDERS["gemini"], key, "gemini-2.5-flash", settings)
    with use_transport(httpx.MockTransport(handler)):
        _complete(adapter)
    request = seen[0]
    assert str(request.url).startswith(PROVIDERS["gemini"].base_url)
    assert str(request.url).endswith("/chat/completions")
    assert request.headers["authorization"] == f"Bearer {key}"
    assert key not in str(request.url)
    assert key not in request.content.decode()
    assert not any(key in v for k, v in request.headers.items() if k != "authorization")


def test_anthropic_request_shape(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ANTHROPIC_OK)

    adapter = build_llm(PROVIDERS["anthropic"], "sk-ant-TEST", "claude-sonnet-5-5", settings)
    with use_transport(httpx.MockTransport(handler)):
        _complete(adapter, system="be brief", user="hello", json_mode=True, max_tokens=50)
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant-TEST"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["max_tokens"] == 50
    assert body["model"] == "claude-sonnet-5-5"
    assert body["system"].startswith("be brief")
    assert body["system"].endswith(anthropic_llm.JSON_ONLY_INSTRUCTION)

    with use_transport(httpx.MockTransport(handler)):
        _complete(adapter, system="be brief", json_mode=False)
    assert json.loads(seen[1].content)["system"] == "be brief"


def test_anthropic_response_parsing(settings: Settings) -> None:
    reply = {
        "model": "claude-x",
        "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": '{"a":1}'}],
        "usage": {"input_tokens": 5, "output_tokens": 7},
    }
    adapter = AnthropicLLM(settings, api_key="sk-ant-TEST", model="claude-x")
    with use_transport(httpx.MockTransport(lambda r: httpx.Response(200, json=reply))):
        result = _complete(adapter)
    assert result.text == '{"a":1}'
    assert result.reasoning == "hmm"
    assert (result.prompt_tokens, result.completion_tokens) == (5, 7)


def test_anthropic_401_is_auth(settings: Settings) -> None:
    adapter = AnthropicLLM(settings, api_key="sk-ant-TEST", model="claude-x")
    with use_transport(httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "bad"}))):
        with pytest.raises(ProviderError) as info:
            _complete(adapter)
    assert info.value.kind == FailureKind.AUTH


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_error_detail_scrubs_echoed_key(settings: Settings, provider: str) -> None:
    secret = "sk-SECRETSECRET123"
    body = f"Incorrect API key provided: {secret}"
    adapter = build_llm(PROVIDERS[provider], secret, "some-model", settings)
    with use_transport(httpx.MockTransport(lambda r: httpx.Response(401, text=body))):
        with pytest.raises(ProviderError) as info:
            _complete(adapter)
    assert secret not in str(info.value)
    assert secret not in info.value.detail
    assert "[redacted]" in info.value.detail


def test_openai_retries_max_completion_tokens(settings: Settings) -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(400, text="Unsupported parameter: 'max_tokens'")
        return httpx.Response(200, json=OPENAI_OK)

    adapter = OpenAICompatibleLLM(settings, base_url="https://x.test/v1", api_key="k-123456", model="m")
    with use_transport(httpx.MockTransport(handler)):
        result = _complete(adapter)
    assert result.text == "OK"
    assert "max_tokens" in bodies[0]
    assert "max_completion_tokens" in bodies[1] and "max_tokens" not in bodies[1]


def test_openai_retries_without_temperature(settings: Settings) -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(400, text="'temperature' is not supported with this model")
        return httpx.Response(200, json=OPENAI_OK)

    adapter = OpenAICompatibleLLM(settings, base_url="https://x.test/v1", api_key="k-123456", model="m")
    with use_transport(httpx.MockTransport(handler)):
        assert _complete(adapter).text == "OK"
    assert "temperature" in bodies[0] and "temperature" not in bodies[1]


def _no_wait(monkeypatch) -> None:
    import asyncio

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)


def _replying(replies: list[httpx.Response], bodies: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return replies[len(bodies) - 1]

    return handler


def test_max_tokens_retry_then_429_then_success(settings: Settings, monkeypatch) -> None:
    """A field retry is not a network retry: after the JSON-mode and max_tokens retries, a 429 is still retried."""
    _no_wait(monkeypatch)
    replies = [
        httpx.Response(400, text="Unsupported parameter: 'response_format'"),
        httpx.Response(400, text="Unsupported parameter: 'max_tokens'"),
        httpx.Response(429, json={}),
        httpx.Response(200, json=OPENAI_OK),
    ]
    bodies: list[dict] = []
    adapter = OpenAICompatibleLLM(settings, base_url="https://x.test/v1", api_key="k-123456", model="m")
    with use_transport(httpx.MockTransport(_replying(replies, bodies))):
        assert _complete(adapter, json_mode=True).text == "OK"
    assert len(bodies) == 4
    assert "max_completion_tokens" in bodies[-1] and "response_format" not in bodies[-1]


def test_field_retry_keeps_both_network_retries(settings: Settings, monkeypatch) -> None:
    _no_wait(monkeypatch)
    replies = [
        httpx.Response(400, text="Unsupported parameter: 'max_tokens'"),
        httpx.Response(429, json={}),
        httpx.Response(429, json={}),
        httpx.Response(200, json=OPENAI_OK),
    ]
    bodies: list[dict] = []
    adapter = OpenAICompatibleLLM(settings, base_url="https://x.test/v1", api_key="k-123456", model="m")
    with use_transport(httpx.MockTransport(_replying(replies, bodies))):
        assert _complete(adapter).text == "OK"
    assert len(bodies) == 4


def test_default_transport_override_reaches_other_contexts(settings: Settings) -> None:
    adapter = OpenAICompatibleLLM(settings, base_url="https://x.test/v1", api_key="k-123456", model="m")
    with override_default_transport(httpx.MockTransport(lambda r: httpx.Response(200, json=OPENAI_OK))):
        assert _complete(adapter).text == "OK"


def test_list_models_sorted_and_never_raises() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]})

    with use_transport(httpx.MockTransport(handler)):
        assert _run(list_models(PROVIDERS["anthropic"], "sk-ant-TEST")) == ["a", "b"]
    assert str(seen[0].url).split("?")[0] == "https://api.anthropic.com/v1/models"
    assert seen[0].headers["x-api-key"] == "sk-ant-TEST"
    assert seen[0].headers["anthropic-version"] == "2023-06-01"
    assert seen[0].url.params["limit"] == "1000"

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with use_transport(httpx.MockTransport(boom)):
        assert _run(list_models(PROVIDERS["openai"], "sk-TESTKEY")) == []


def test_list_models_strips_gemini_prefix() -> None:
    rows = {"data": [{"id": "models/gemini-2.5-flash"}, {"id": "models/gemini-2.5-pro"}]}
    with use_transport(httpx.MockTransport(lambda r: httpx.Response(200, json=rows))):
        assert _run(list_models(PROVIDERS["gemini"], "AIzaTESTKEY123")) == ["gemini-2.5-flash", "gemini-2.5-pro"]


def test_probe_key_empty_content_still_counts_as_working(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "o-x"}]})
        return httpx.Response(200, json={"model": "o-x", "choices": [{"message": {"content": ""}}]})

    with use_transport(httpx.MockTransport(handler)):
        result = _run(probe_key(PROVIDERS["openai"], "sk-TESTKEY123", "o-x", settings))
    assert (result.ok, result.kind) == (True, "ok")
    assert result.message == "The model answered without text; the key works."
    assert result.models == ["o-x"]


def test_probe_key_reports_kinds(settings: Settings) -> None:
    spec = PROVIDERS["openai"]
    key = "sk-TESTKEY123"

    def respond(status: int, text: str = ""):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "gpt-x"}]})
            if status == 200:
                return httpx.Response(200, json=OPENAI_OK)
            return httpx.Response(status, text=text)

        return handler

    def kind_for(status: int, text: str = ""):
        with use_transport(httpx.MockTransport(respond(status, text))):
            return _run(probe_key(spec, key, "gpt-x", settings))

    ok = kind_for(200)
    assert (ok.ok, ok.kind, ok.models) == (True, "ok", ["gpt-x"])
    assert kind_for(401, "bad key").kind == "auth"
    assert kind_for(429, "slow down").kind == "rate_limit"
    assert kind_for(404, "The model `gpt-x` does not exist").kind == "model"
    assert kind_for(400, "invalid model name").kind == "model"
    assert kind_for(400, "something else").kind == "other"
    assert kind_for(404, "").kind == "other"
    assert kind_for(402, "").kind == "credit"
    assert kind_for(429, '{"error": {"code": "insufficient_quota"}}').kind == "credit"
    assert kind_for(401, "billing account suspended").kind == "auth"
    leaked = kind_for(401, f"bad key {key}")
    assert key not in leaked.message and len(leaked.message) <= 200

    low_credit = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic API.",
        },
    }
    with use_transport(httpx.MockTransport(lambda request: httpx.Response(400, json=low_credit))):
        anthropic = _run(probe_key(PROVIDERS["anthropic"], "sk-ant-TESTKEY123", "claude-x", settings))
    assert (anthropic.ok, anthropic.kind) == (False, "credit")


def test_default_openai_path_unchanged(tmp_path) -> None:
    settings = Settings(
        _env_file=None, environment="test", data_dir=tmp_path, database_path=tmp_path / "t.db",
        llm_base_url="https://writer.test/v1/", llm_api_key="writer-key", llm_model="writer-model",
        xai_api_key="",
    )
    adapter = OpenAICompatibleLLM(settings)
    assert adapter._base_url == settings.writer_base_url == "https://writer.test/v1"
    assert adapter._api_key == settings.writer_api_key == "writer-key"
    assert adapter._model == settings.writer_model == "writer-model"
    assert adapter.available
