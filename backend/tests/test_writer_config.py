from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.config import Settings
from app.container import build_container
from app.infrastructure.http import use_transport
from app.infrastructure.llm import OpenAICompatibleLLM


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "writer.db",
        trace_dir=tmp_path / "traces",
        eval_dir=tmp_path / "eval",
        decision_enabled=False,
        langfuse_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def test_xai_label_uses_the_xai_fields(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        llm_provider="xai",
        xai_api_key="xai-test-key",
        xai_base_url="https://api.x.ai/v1",
        xai_model="grok-test",
    )
    llm = OpenAICompatibleLLM(settings)

    assert settings.writer_available
    assert settings.writer_vendor == "xai"
    assert settings.writer_base_url == "https://api.x.ai/v1"
    assert settings.writer_model == "grok-test"
    assert llm.available
    assert llm.name == "grok-test"


def test_xai_only_configuration_does_not_fall_back_to_null(tmp_path: Path) -> None:
    """Regression: the container used to check only LLM_*, so a valid XAI_*-only
    configuration silently produced NullLLM."""
    settings = make_settings(
        tmp_path,
        llm_provider="xai",
        xai_api_key="xai-test-key",
        xai_base_url="https://api.x.ai/v1",
        xai_model="grok-test",
    )
    container = build_container(settings)

    assert container.selector.operator_chain().source == "env"
    assert getattr(container.llm, "available", False) is True


def test_deepseek_label_ignores_the_xai_fields(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        llm_provider="deepseek",
        llm_api_key="ds-key",
        llm_base_url="https://api.deepseek.com/v1",
        llm_model="deepseek-chat",
        xai_api_key="xai-test-key",
        xai_base_url="https://api.x.ai/v1",
        xai_model="grok-test",
    )

    assert settings.writer_base_url == "https://api.deepseek.com/v1"
    assert settings.writer_api_key == "ds-key"
    assert settings.writer_model == "deepseek-chat"
    assert settings.writer_vendor == "deepseek"


def test_label_is_normalised_case_and_whitespace(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, llm_provider="  XAI  ")
    assert settings.llm_provider == "xai"
    assert settings.writer_is_xai


def test_xai_label_falls_back_to_llm_fields_when_xai_is_empty(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        llm_provider="xai",
        llm_api_key="shared-key",
        llm_base_url="https://api.x.ai/v1",
        llm_model="grok-shared",
    )

    assert settings.writer_base_url == "https://api.x.ai/v1"
    assert settings.writer_api_key == "shared-key"
    assert settings.writer_model == "grok-shared"


# --- OpenAI-compatible endpoints (OpenAI itself, a gateway, vLLM, Ollama) ------------------------

GATEWAY = "https://gateway.test/v1"


def test_openai_label_uses_the_openai_fields(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        llm_provider="openai",
        openai_api_key="sk-gateway",
        openai_base_url=GATEWAY,
        openai_model="gpt-6.1-terra",
    )
    llm = OpenAICompatibleLLM(settings)

    assert settings.writer_available
    assert settings.writer_base_url == GATEWAY
    assert settings.writer_api_key == "sk-gateway"
    assert settings.writer_model == "gpt-6.1-terra"
    assert settings.writer_vendor == "openai-compatible"
    assert llm.available and llm.name == "gpt-6.1-terra"


def test_openai_label_falls_back_to_llm_fields_when_openai_is_empty(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        llm_provider="openai",
        llm_api_key="shared-key",
        llm_base_url="https://gateway.test/v1",
        llm_model="shared-model",
    )

    assert settings.writer_base_url == "https://gateway.test/v1"
    assert settings.writer_api_key == "shared-key"
    assert settings.writer_model == "shared-model"


def test_openai_compatible_labels_are_normalised(tmp_path: Path) -> None:
    for label in ("OpenAI", "openai_compatible", "OpenAI Compatible", "custom", "compatible"):
        settings = make_settings(tmp_path, llm_provider=label)
        assert settings.writer_is_openai_compatible, label


def test_openai_fields_are_ignored_under_another_label(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        llm_provider="deepseek",
        llm_api_key="ds-key",
        llm_base_url="https://api.deepseek.com/v1",
        llm_model="deepseek-chat",
        openai_api_key="sk-gateway",
        openai_base_url=GATEWAY,
        openai_model="gpt-6.1-terra",
    )

    assert settings.writer_base_url == "https://api.deepseek.com/v1"
    assert settings.writer_api_key == "ds-key"
    assert settings.writer_model == "deepseek-chat"


def test_openai_compatible_request_targets_the_gateway(tmp_path: Path) -> None:
    """The adapter posts to {OPENAI_BASE_URL}/chat/completions with the configured model."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "model": "gpt-6.1-terra",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    settings = make_settings(
        tmp_path,
        llm_provider="openai",
        openai_api_key="sk-gateway",
        openai_base_url=GATEWAY,
        openai_model="gpt-6.1-terra",
    )
    llm = OpenAICompatibleLLM(settings)
    with use_transport(httpx.MockTransport(handler)):
        result = asyncio.run(llm.complete(system="s", user="u", purpose="test", json_mode=True))

    body = json.loads(seen[0].content)
    assert str(seen[0].url) == f"{GATEWAY}/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-gateway"
    assert body["model"] == "gpt-6.1-terra"
    assert body["response_format"] == {"type": "json_object"}
    assert result.text == '{"ok":true}'


def test_openai_compatible_container_selects_the_env_writer(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        llm_provider="openai-compatible",
        openai_api_key="sk-gateway",
        openai_base_url=GATEWAY,
        openai_model="gpt-6.1-terra",
    )
    container = build_container(settings)

    assert container.selector.operator_chain().source == "env"
    assert container.selector.operator_chain().model == "gpt-6.1-terra"
