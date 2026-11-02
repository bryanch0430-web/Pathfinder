from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.container import build_container
from app.infrastructure.llm import NullLLM, OpenAICompatibleLLM


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

    assert not isinstance(container.llm, NullLLM)
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
