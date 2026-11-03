from __future__ import annotations

import os
from pathlib import Path

import pytest

# Importing app.interface.api builds the app, and an unset ENVIRONMENT now means
# production (which refuses the repository's default secrets). The suite runs as "test".
os.environ.setdefault("ENVIRONMENT", "test")

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.config import Settings
from app.container import build_container
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        data_dir=tmp_path,
        database_path=tmp_path / "test.db",
        trace_dir=tmp_path / "traces",
        eval_dir=tmp_path / "eval",
        llm_api_key="",
        llm_base_url="",
        xai_api_key="",
        decision_enabled=False,
        langfuse_enabled=False,
        default_currency="USD",
    )


@pytest.fixture()
def container(settings: Settings):
    return build_container(settings)


@pytest.fixture()
def offline_gateway(settings: Settings) -> LLMGateway:
    return LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
