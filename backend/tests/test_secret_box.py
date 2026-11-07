from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.infrastructure.secret_box import SecretBox, UnreadableSecret, mask_key


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, environment="test", **overrides)


def test_round_trip() -> None:
    box = SecretBox(_settings(auth_secret="secret-a"))
    token = box.encrypt("sk-test-123456")
    assert b"sk-test-123456" not in token
    assert box.decrypt(token) == "sk-test-123456"


def test_other_secret_cannot_read() -> None:
    token = SecretBox(_settings(auth_secret="secret-a")).encrypt("sk-test-123456")
    with pytest.raises(UnreadableSecret):
        SecretBox(_settings(auth_secret="secret-b")).decrypt(token)


def test_explicit_key_used() -> None:
    box = SecretBox(_settings(byok_encryption_key=Fernet.generate_key().decode()))
    assert box.decrypt(box.encrypt("sk-test-123456")) == "sk-test-123456"


def test_bad_key_value_refused() -> None:
    with pytest.raises(ValueError, match="BYOK_ENCRYPTION_KEY is not a valid Fernet key"):
        SecretBox(_settings(byok_encryption_key="nope"))


def test_production_requires_key() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        auth_secret="x" * 40,
        admin_token="real-admin-token-value",
        byok_encryption_key="",
    )
    with pytest.raises(RuntimeError, match="BYOK_ENCRYPTION_KEY"):
        settings.check_secrets()


def test_local_llm_configured_needs_url_and_model() -> None:
    assert not _settings().local_llm_configured
    assert not _settings(local_llm_base_url="http://localhost:11434/v1").local_llm_configured
    assert _settings(local_llm_base_url="http://localhost:11434/v1", local_llm_model="llama3.1").local_llm_configured


def test_mask_key() -> None:
    assert mask_key("sk-ant-api03-abcdefWXYZ") == "sk-…WXYZ"
    assert mask_key("abcd1234") == "ab…1234"
    assert "short" not in mask_key("short")
