"""Default secrets are refused outside development/test; admin token checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.interface.api import create_app


# -- secrets --------------------------------------------------------------------


def test_production_refuses_to_start_with_default_secrets(settings: Settings) -> None:
    production = settings.model_copy(update={"environment": "production"})
    with pytest.raises(RuntimeError) as excinfo:
        create_app(production)
    message = str(excinfo.value)
    assert "AUTH_SECRET" in message
    assert "ADMIN_TOKEN" in message


def test_production_refuses_a_short_auth_secret(settings: Settings) -> None:
    production = settings.model_copy(
        update={"environment": "production", "auth_secret": "too-short", "admin_token": "a-real-admin-token-value"}
    )
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        create_app(production)


def test_production_refuses_the_default_admin_token(settings: Settings) -> None:
    production = settings.model_copy(
        update={"environment": "production", "auth_secret": "s" * 48, "admin_token": "local-admin"}
    )
    with pytest.raises(RuntimeError, match="ADMIN_TOKEN"):
        create_app(production)


def test_production_starts_when_secrets_are_set(settings: Settings) -> None:
    production = settings.model_copy(
        update={
            "environment": "production",
            "auth_secret": "a-long-random-production-secret-0123456789",
            "admin_token": "a-real-admin-token-value",
            "byok_encryption_key": "Hl0sKQ3pXx4nq0vYb2m5c9fJ8dT1aW6eRzUoLiNgPyA=",
        }
    )
    with TestClient(create_app(production)) as scoped:
        assert scoped.get("/api/ready").status_code == 200


def test_secret_check_reads_the_environment_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def from_env() -> Settings:
        return Settings(
            _env_file=None,
            data_dir=tmp_path,
            database_path=tmp_path / "env.db",
            trace_dir=tmp_path / "traces",
            eval_dir=tmp_path / "eval",
        )

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        create_app(from_env())

    monkeypatch.setenv("AUTH_SECRET", "a-long-random-production-secret-0123456789")
    monkeypatch.setenv("ADMIN_TOKEN", "a-real-admin-token-value")
    monkeypatch.setenv("BYOK_ENCRYPTION_KEY", "Hl0sKQ3pXx4nq0vYb2m5c9fJ8dT1aW6eRzUoLiNgPyA=")
    create_app(from_env())


def test_unset_environment_refuses_default_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An ENVIRONMENT variable left in the shell (CI sets it to "test") would hide the default.
    for name in ("ENVIRONMENT", "AUTH_SECRET", "ADMIN_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None, data_dir=tmp_path, database_path=tmp_path / "t.db")
    with pytest.raises(RuntimeError, match="AUTH_SECRET") as excinfo:
        settings.check_secrets()
    assert "ENVIRONMENT is not set" in str(excinfo.value)


def test_development_and_test_start_with_defaults(settings: Settings) -> None:
    for environment in ("development", "test"):
        create_app(settings.model_copy(update={"environment": environment}))


def test_admin_endpoints_reject_a_wrong_token(settings: Settings) -> None:
    scoped_settings = settings.model_copy(update={"admin_token": "right-admin-token"})
    with TestClient(create_app(scoped_settings)) as scoped:
        assert scoped.get("/api/admin/threats").status_code == 401
        assert scoped.get("/api/admin/threats", headers={"X-Admin-Token": "local-admin"}).status_code == 401
        assert scoped.get("/api/admin/threats", headers={"X-Admin-Token": "right-admin-token-x"}).status_code == 401
        assert scoped.get("/api/admin/threats", headers={"X-Admin-Token": "right-admin-token"}).status_code == 200


def test_empty_admin_token_setting_disables_admin_endpoints(settings: Settings) -> None:
    scoped_settings = settings.model_copy(update={"admin_token": ""})
    with TestClient(create_app(scoped_settings)) as scoped:
        assert scoped.get("/api/admin/threats").status_code == 401
        assert scoped.get("/api/admin/threats", headers={"X-Admin-Token": ""}).status_code == 401
