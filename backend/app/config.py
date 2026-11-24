"""Runtime settings for Pathfinder v5.

Every value comes from the environment (or the project ``.env``).  Nothing in the
core reads ``os.environ`` directly, so a test can build a :class:`Settings`
with its own values.

The writer model is OpenAI-compatible: Poe is the default endpoint.  ``LLM_PROVIDER``
is the vendor label that decides which block wins:

* ``openai`` (or ``openai-compatible`` / ``chatanywhere``) -> the ``OPENAI_*`` block
* ``xai``                                                  -> the ``XAI_*`` block
* anything else                                            -> the ``LLM_*`` block
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application ----------------------------------------------------
    environment: str = "development"
    database_path: str = Field(default="data/pathfinder.db", alias="DATABASE_PATH")
    data_dir: str = Field(default="data", alias="DATA_DIR")
    default_currency: str = Field(default="HKD", alias="DEFAULT_CURRENCY")
    # An operator can change this on Settings -> Thresholds. The hard ceiling in
    # app.domain.models stops an absurd body before it is parsed.
    max_message_chars: int = Field(default=4000, ge=200, le=8000, alias="MAX_MESSAGE_CHARS")
    canary_token: str = Field(default="", alias="CANARY_TOKEN")
    admin_token: str = Field(default="local-admin", alias="ADMIN_TOKEN")
    secret_key: str = Field(default="", alias="SECRET_KEY")
    local_admin: bool = Field(default=True, alias="LOCAL_ADMIN")
    local_admin_proxy: str = Field(default="", alias="LOCAL_ADMIN_PROXY")
    # Browser origins allowed to call the API. A same-origin page needs none of
    # these; the defaults are the local app and the Vite dev server.
    cors_origins: str = Field(
        default=(
            "http://127.0.0.1:8100,http://localhost:8100,"
            "http://127.0.0.1:5173,http://localhost:5173"
        ),
        alias="CORS_ORIGINS",
    )
    wikimedia_user_agent: str = Field(
        default="Pathfinder50/1.0 (educational project) python-httpx",
        alias="WIKIMEDIA_USER_AGENT",
    )

    # --- writer model ---------------------------------------------------
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llm_base_url: str = Field(default="https://api.poe.com/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="claude-sonnet-5.5", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.2, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=4096, alias="LLM_MAX_TOKENS")
    llm_timeout_seconds: float = Field(default=60.0, alias="LLM_TIMEOUT_SECONDS")
    # Optional per-part models (same provider/key unless a separate key is added).
    purpose_models: dict[str, str] = Field(default_factory=dict, alias="PURPOSE_MODELS")
    purpose_temperatures: dict[str, float] = Field(default_factory=dict, alias="PURPOSE_TEMPERATURES")
    purpose_reasoning: dict[str, str] = Field(default_factory=dict, alias="PURPOSE_REASONING")
    purpose_max_tokens: dict[str, int] = Field(default_factory=dict, alias="PURPOSE_MAX_TOKENS")
    # The constraint verifier can use its own endpoint/key. This is deliberately
    # separate from the writer: a different model is the point of the audit.
    verifier_base_url: str = Field(default="https://api.deepseek.com/v1", alias="VERIFIER_BASE_URL")
    verifier_api_key: str = Field(default="", alias="VERIFIER_API_KEY")
    verifier_model: str = Field(default="deepseek-flash", alias="VERIFIER_MODEL")
    verifier_temperature: float = Field(default=0.0, alias="VERIFIER_TEMPERATURE")
    verifier_max_tokens: int = Field(default=1500, alias="VERIFIER_MAX_TOKENS")
    verifier_timeout_seconds: float = Field(default=60.0, alias="VERIFIER_TIMEOUT_SECONDS")
    llm_reasoning: str = Field(default="none", alias="LLM_REASONING")
    turn_orchestrator: str = Field(default="analyst", alias="TURN_ORCHESTRATOR")

    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="", alias="OPENAI_MODEL")

    xai_base_url: str = Field(default="https://api.x.ai/v1", alias="XAI_BASE_URL")
    xai_api_key: str = Field(default="", alias="XAI_API_KEY")
    xai_model: str = Field(default="grok-4.20-0309-non-reasoning", alias="XAI_MODEL")

    poe_api_key: str = Field(default="", alias="POE_API_KEY")
    poe_base_url: str = Field(default="https://api.poe.com/v1", alias="POE_BASE_URL")

    # A second model checks every edit when a key is configured.
    reviewer_reasoning: str = Field(default="low", alias="REVIEWER_REASONING")
    reviewer_timeout_seconds: float = Field(default=25.0, alias="REVIEWER_TIMEOUT_SECONDS")

    # --- web search -----------------------------------------------------
    web_search_enabled: bool = Field(default=True, alias="WEB_SEARCH_ENABLED")
    deepseek_search_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_SEARCH_BASE_URL")
    deepseek_search_api_key: str = Field(default="", alias="DEEPSEEK_SEARCH_API_KEY")
    grok_search_enabled: bool = Field(default=False, alias="GROK_SEARCH_ENABLED")
    grok_search_timeout_seconds: float = Field(default=90.0, alias="GROK_SEARCH_TIMEOUT_SECONDS")

    # --- System 1 router (optional) ------------------------------------
    decision_enabled: bool = Field(default=False, alias="DECISION_ENABLED")
    decision_api_url: str = Field(default="https://api.typesafe.ai/v1/systemone", alias="DECISION_API_URL")
    decision_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DECISION_API_KEY", "JEV_API_KEY"),
    )
    decision_model: str = Field(default="jev-latest", alias="DECISION_MODEL")
    decision_auth_prefix: str = Field(default="Bearer", alias="DECISION_AUTH_PREFIX")
    decision_timeout_seconds: float = Field(default=1.5, alias="DECISION_TIMEOUT_SECONDS")

    # --- judge ----------------------------------------------------------
    judge_base_url: str = Field(default="https://api.deepseek.com", alias="JUDGE_BASE_URL")
    judge_api_key: str = Field(default="", alias="JUDGE_API_KEY")
    judge_model: str = Field(default="deepseek-chat", alias="JUDGE_MODEL")
    judge_timeout_seconds: float = Field(default=20.0, alias="JUDGE_TIMEOUT_SECONDS")

    # --- observability --------------------------------------------------
    langfuse_enabled: bool = Field(default=False, alias="LANGFUSE_ENABLED")
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://us.cloud.langfuse.com", alias="LANGFUSE_HOST")

    # --- data providers -------------------------------------------------
    google_places_api_key: str = Field(default="", alias="GOOGLE_PLACES_API_KEY")
    google_maps_api_key: str = Field(default="", alias="GOOGLE_MAPS_API_KEY")
    serpapi_key: str = Field(default="", alias="SERPAPI_KEY")
    serpapi_key_pool: str = Field(default="", alias="SERPAPI_KEY_POOL")
    youtube_api_key: str = Field(default="", alias="YOUTUBE_API_KEY")

    # --- feature toggles -----------------------------------------------
    enable_notes: bool = Field(default=True, alias="ENABLE_NOTES")
    enable_flights: bool = Field(default=True, alias="ENABLE_FLIGHTS")
    enable_routes: bool = Field(default=True, alias="ENABLE_ROUTES")
    enable_bilibili: bool = Field(default=False, alias="ENABLE_BILIBILI")
    enable_youtube: bool = Field(default=True, alias="ENABLE_YOUTUBE")
    enable_route_matrix: bool = Field(default=True, alias="ENABLE_ROUTE_MATRIX")
    enable_timezone: bool = Field(default=True, alias="ENABLE_TIMEZONE")
    enable_elevation: bool = Field(default=False, alias="ENABLE_ELEVATION")

    # --- policy ---------------------------------------------------------
    # Two different signals, two settings. ROUTER_THRESHOLD is accepted only as
    # a legacy fallback for the analyst knob; Jev has its own threshold.
    analyst_confidence_threshold: float = Field(
        default=0.8,
        validation_alias=AliasChoices("ANALYST_CONFIDENCE_THRESHOLD", "ROUTER_THRESHOLD"),
    )
    jev_confidence_threshold: float = Field(default=0.8, alias="JEV_CONFIDENCE_THRESHOLD")
    max_repairs: int = Field(default=2, alias="MAX_REPAIRS")
    preplan_budget_seconds: int = Field(default=90, alias="PREPLAN_BUDGET_SECONDS")
    agent_timeout_seconds: float = Field(default=30.0, alias="AGENT_TIMEOUT_SECONDS")
    memory_similarity_cutoff: float = Field(default=0.72, alias="MEMORY_SIMILARITY_CUTOFF")
    search_window_months: int = Field(default=24, alias="SEARCH_WINDOW_MONTHS")
    turn_deadline_seconds: float = Field(default=300.0, ge=60.0, le=900.0, alias="TURN_DEADLINE_SECONDS")
    sse_keepalive_seconds: float = Field(default=15.0, alias="SSE_KEEPALIVE_SECONDS")

    # --- sandbox --------------------------------------------------------
    # "1" makes every travel provider answer from curated fixtures: offline demos,
    # reproducible evaluation, and tests all run through the same pipeline.
    sandbox: bool = Field(default=False, alias="PATHFINDER_SANDBOX")

    @field_validator("database_path", "data_dir", mode="before")
    @classmethod
    def _relative_to_project(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute():
            return str(path)
        return str(PROJECT_DIR / path)

    @property
    def writer(self) -> tuple[str, str, str]:
        """(base_url, api_key, model) for the writer, resolved by ``LLM_PROVIDER``."""
        vendor = (self.llm_provider or "").strip().lower()
        if vendor in {"openai", "openai-compatible", "openai_compatible", "chatanywhere"}:
            base = self.openai_base_url or self.llm_base_url or self.poe_base_url
            key = self.openai_api_key or self.llm_api_key or self.poe_api_key
            model = self.openai_model or self.llm_model
        elif vendor == "xai":
            base, key, model = self.xai_base_url, self.xai_api_key, self.xai_model
        else:
            base = self.llm_base_url or self.poe_base_url
            key = self.llm_api_key or self.poe_api_key
            model = self.llm_model
        return base.rstrip("/"), key, model

    @property
    def database_file(self) -> Path:
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def allowed_origins(self) -> list[str]:
        """The origins the browser may call from, trimmed and without blanks."""
        return [item.strip().rstrip("/") for item in self.cors_origins.split(",") if item.strip()]

    @property
    def secret_key_value(self) -> str:
        return self.secret_key or self.admin_token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
