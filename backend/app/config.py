"""Typed configuration. Every external dependency has an explicit switch and a
documented degraded mode, so the application starts with no credentials at all.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env", BASE_DIR / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application -------------------------------------------------------
    app_name: str = "Pathfinder"
    environment: str = "development"
    debug: bool = False
    base_dir: Path = BASE_DIR
    data_dir: Path = DATA_DIR
    database_path: Path = DATA_DIR / "pathfinder.db"
    trace_dir: Path = DATA_DIR / "traces"
    eval_dir: Path = DATA_DIR / "eval"
    frontend_dist: Path = BASE_DIR / "frontend" / "dist"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    admin_token: str = "local-admin"
    auth_secret: str = "local-dev-auth-secret-change-me"
    auth_token_hours: int = 168
    user_agent: str = "Pathfinder30/1.0 (COMP7608B student project)"
    wikimedia_user_agent: str = (
        "Pathfinder30/1.0 (https://github.com/bryan0430-web/Pathfinder; educational project) "
        "python-httpx/0.28"
    )

    # --- language ----------------------------------------------------------
    default_locale: str = "en"
    supported_locales: str = "en,zh-Hans,zh-Hant"

    # --- writer model ------------------------------------------------------
    llm_provider: str = "deepseek"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096
    llm_timeout_seconds: float = 90.0
    llm_json_mode: bool = True

    xai_base_url: str = ""
    xai_api_key: str = ""
    xai_model: str = ""

    # --- System 1 router ---------------------------------------------------
    decision_enabled: bool = True
    decision_api_url: str = ""
    decision_api_key: str = ""
    decision_model: str = "jev-latest"
    decision_auth_prefix: str = "Bearer"
    decision_provider: str = "typesafe"
    decision_path: str = "/v1/systemone"
    decision_timeout_seconds: float = 8.0

    # --- judge (evaluation only) ------------------------------------------
    # Optional LLM-as-a-judge. Leave judge_api_key empty to keep the
    # deterministic scorer; a set key enables an advisory score per case.
    judge_base_url: str = "https://api.deepseek.com"
    judge_api_key: str = ""
    judge_model: str = "deepseek-chat"
    judge_timeout_seconds: float = 20.0

    # --- observability -----------------------------------------------------
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # --- data providers ----------------------------------------------------
    google_places_api_key: str = ""
    google_places_proxy: str = ""
    google_maps_api_key: str = ""
    serpapi_key: str = ""
    youtube_api_key: str = ""

    enable_notes: bool = True
    enable_flights: bool = True
    enable_routes: bool = True
    enable_route_matrix: bool = True
    enable_timezone: bool = True
    enable_elevation: bool = True
    enable_bilibili: bool = True
    enable_youtube: bool = True

    # --- planning policy ---------------------------------------------------
    router_threshold: float = 0.8
    max_repairs: int = 2
    preplan_budget_seconds: float = 90.0
    agent_timeout_seconds: float = 30.0
    http_timeout_seconds: float = 15.0
    http_retries: int = 2
    max_days: int = 14
    max_attractions_per_day: int = 4
    memory_similarity_cutoff: float = 0.72
    search_window_months: int = 24
    default_currency: str = "HKD"

    # freshness windows, seconds
    weather_fresh_seconds: int = 6 * 3600
    ticket_fresh_seconds: int = 24 * 3600
    hotel_fresh_seconds: int = 7 * 24 * 3600
    attraction_fresh_seconds: int = 30 * 24 * 3600

    # --- security ----------------------------------------------------------
    canary_token: str = "<<CANARY-PF30-7f3a9c>>"
    blocklist_enabled: bool = True
    fence_enabled: bool = True

    @field_validator("llm_provider", mode="before")
    @classmethod
    def _normalise_provider(cls, value: object) -> str:
        return str(value).strip().lower()

    @field_validator("data_dir", "database_path", "trace_dir", "eval_dir", "frontend_dist", mode="before")
    @classmethod
    def _absolute(cls, value: object) -> Path:
        path = Path(str(value))
        return path if path.is_absolute() else BASE_DIR / path

    @property
    def writer_is_xai(self) -> bool:
        """True when the provider label selects the xAI-specific fields."""
        return self.llm_provider.strip().lower() in {"xai", "grok"}

    @property
    def writer_base_url(self) -> str:
        """The base URL the writer will actually call (label-aware)."""
        if self.writer_is_xai:
            return (self.xai_base_url or self.llm_base_url).strip().rstrip("/")
        return self.llm_base_url.strip().rstrip("/")

    @property
    def writer_api_key(self) -> str:
        if self.writer_is_xai:
            return self.xai_api_key or self.llm_api_key
        return self.llm_api_key

    @property
    def writer_model(self) -> str:
        if self.writer_is_xai and self.xai_api_key:
            return self.xai_model or self.llm_model
        return self.llm_model

    @property
    def writer_available(self) -> bool:
        return bool(self.writer_base_url and self.writer_api_key)

    @property
    def writer_vendor(self) -> str:
        """Vendor inferred from the effective base URL (for logs and /api/runtime)."""
        host = self.writer_base_url.lower()
        if "x.ai" in host:
            return "xai"
        if "deepseek" in host:
            return "deepseek"
        if "openai" in host:
            return "openai"
        if "anthropic" in host:
            return "anthropic"
        if "googleapis" in host or "generativelanguage" in host:
            return "google"
        return "custom"

    @property
    def decision_available(self) -> bool:
        return bool(self.decision_enabled and self.decision_api_key and self.decision_api_url)

    @property
    def judge_available(self) -> bool:
        return bool(self.judge_api_key and self.judge_base_url and self.judge_model)

    @property
    def langfuse_available(self) -> bool:
        return bool(
            self.langfuse_enabled
            and self.langfuse_public_key
            and self.langfuse_secret_key
            and self.langfuse_host
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.trace_dir, self.eval_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
