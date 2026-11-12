"""Typed configuration. Every external dependency has an explicit switch and a
documented degraded mode, so the application starts with no credentials at all.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"

#: Local-only defaults. Anyone who reads the repository knows them, so
#: `Settings.check_secrets` refuses them outside development and test.
DEFAULT_ADMIN_TOKEN = "local-admin"
DEFAULT_AUTH_SECRET = "local-dev-auth-secret-change-me"
#: Admin tokens that are only ever placeholders (the default, .env.example).
PLACEHOLDER_ADMIN_TOKENS = frozenset({DEFAULT_ADMIN_TOKEN, "REPLACE_ME"})
MIN_AUTH_SECRET_LENGTH = 32
DEV_ENVIRONMENTS = frozenset({"development", "test"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env", BASE_DIR / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application -------------------------------------------------------
    app_name: str = "Pathfinder"
    #: Unset means production, so a deployment that forgets it is refused rather than run on the repo's secrets.
    environment: str = "production"
    debug: bool = False
    base_dir: Path = BASE_DIR
    data_dir: Path = DATA_DIR
    database_path: Path = DATA_DIR / "pathfinder.db"
    trace_dir: Path = DATA_DIR / "traces"
    eval_dir: Path = DATA_DIR / "eval"
    frontend_dist: Path = BASE_DIR / "frontend" / "dist"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    admin_token: str = DEFAULT_ADMIN_TOKEN
    #: The owner's own machine: a loopback caller opens the admin routes without the token. Honoured only
    #: with ENVIRONMENT=development/test; `check_secrets` refuses to start with it anywhere else.
    local_admin: bool = False
    auth_secret: str = DEFAULT_AUTH_SECRET
    auth_token_hours: int = 168
    user_agent: str = "Pathfinder/1.0 (COMP7608B student project)"
    wikimedia_user_agent: str = (
        "Pathfinder/1.0 (https://github.com/bryanch0430-web/Pathfinder; educational project) "
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

    # --- web search --------------------------------------------------------
    # DeepSeek Search is the interactive default (sub-second, $0.75 per 1k
    # successful calls). Grok's Agent Tools search is a model call with a ~20 s
    # round trip, so it is opt-in and only used for synthesis questions.
    web_search_enabled: bool = True
    deepseek_search_base_url: str = "https://api.deepseek.com"
    deepseek_search_api_key: str = ""  # falls back to judge_api_key
    grok_search_enabled: bool = False
    grok_search_timeout_seconds: float = 90.0

    # --- judge (evaluation only) ------------------------------------------
    # Optional LLM-as-a-judge. Leave judge_api_key empty to keep the
    # deterministic scorer; a set key enables an advisory score per case.
    judge_base_url: str = "https://api.deepseek.com"
    judge_api_key: str = ""
    judge_model: str = "deepseek-chat"
    judge_timeout_seconds: float = 20.0

    # --- bring your own key / local model ---------------------------------
    byok_encryption_key: str = ""  # a Fernet key; required outside development/test
    local_llm_base_url: str = ""
    local_llm_model: str = ""
    local_llm_api_key: str = ""
    local_llm_probe_seconds: float = 2.0

    # --- observability -----------------------------------------------------
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # --- data providers ----------------------------------------------------
    google_places_api_key: str = ""
    google_places_proxy: str = ""
    google_maps_api_key: str = ""
    #: AMap (Gaode) Web Service key: the maps fallback for mainland China (DB-16).
    #: Empty disables it. Server-side only; never sent to the browser.
    amap_key: str = ""
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
    # Hong Kong trips: HKO warnings and MTR line status adjust the plan (TC-03).
    enable_hk_disruptions: bool = True

    # --- planning policy ---------------------------------------------------
    router_threshold: float = 0.8
    # Let the deterministic rules decide unambiguous messages without a Jev or
    # writer call (Proposal TC-10). Unsure or flagged messages still use the model.
    router_rules_first: bool = True
    max_repairs: int = 2
    preplan_budget_seconds: float = 90.0
    # LLM audit of the four pre-planning agents' results (plan path only). Off by default; it
    # fails open, so a failed audit never blocks a trip.
    enable_preplan_audit: bool = False
    audit_timeout_seconds: float = 20.0
    audit_follow_up_seconds: float = 15.0
    #: Wall-clock budget for the meal searches near each day's stops, all days together. When it runs
    #: out the meals come from the city-wide restaurant pool the attraction agent already fetched.
    meal_search_budget_seconds: float = Field(15.0, ge=0)
    #: The deterministic refine loop after compose: rounds of check -> fix (no model call), and the day
    #: load past which a day is trimmed or noted (visit hours; km on foot between stops).
    refine_max_rounds: int = Field(3, ge=0, le=10)
    #: Stay research (plan path): when the traveller asks something of the stay or its room, the
    #: candidates' rooms are fetched and one writer call ranks the candidates against the wish.
    enable_stay_research: bool = True
    research_max_hotels: int = Field(5, ge=1, le=8)
    #: Wall-clock budget for fetching the candidates' rooms, all together.
    research_budget_seconds: float = Field(20.0, gt=0)
    research_timeout_seconds: float = Field(20.0, gt=0)
    #: The follow-up round: unclear stays looked up on the web, at most this many, in this long.
    research_follow_up_hotels: int = Field(3, ge=1, le=5)
    research_follow_up_seconds: float = Field(20.0, gt=0)
    #: Plan assessment: confidence, what is unverified or missing, and the rounds of further research
    #: an assessment may set off before the open point becomes a question for the traveller.
    enable_plan_assessment: bool = True
    #: Intake: a link in the message, or an attached PDF or picture, is read and fills the trip form.
    enable_intake: bool = True
    intake_max_urls: int = Field(2, ge=0, le=5)
    intake_max_attachments: int = Field(3, ge=0, le=5)
    #: The most bytes of one attached file, and of the whole chat request that carries the files as base64.
    intake_max_attachment_bytes: int = Field(4_194_304, ge=1)
    intake_max_body_bytes: int = Field(13_000_000, ge=1_048_576)
    #: The most bytes read from a link, and how long the link may take to answer.
    intake_max_fetch_bytes: int = Field(3_000_000, ge=1)
    intake_fetch_timeout_seconds: float = Field(12.0, gt=0)
    intake_timeout_seconds: float = Field(40.0, gt=0)
    #: The most characters of shared text the writer is given, all sources together.
    intake_max_chars: int = Field(24_000, ge=500)
    intake_max_pdf_pages: int = Field(30, ge=1)
    intake_max_places: int = Field(10, ge=0, le=20)
    assessment_max_research_rounds: int = Field(1, ge=0, le=2)
    day_max_activity_hours: float = Field(9.0, gt=0)
    day_max_walking_km: float = Field(12.0, gt=0)
    audit_max_follow_ups: int = Field(3, ge=0, le=5)
    #: 26 = the attraction agent's most place candidates (agents.MAX_PLACE_CANDIDATES, 20) plus its 6
    #: restaurants, so one audit call sees a long trip's whole attraction pool.
    audit_max_items_per_agent: int = Field(26, ge=1)
    agent_timeout_seconds: float = 30.0
    http_timeout_seconds: float = 15.0
    #: Retries of a request to the main data sources: flights, hotels, routes, places, the forecast,
    #: geocoding and web search. The other sources keep a count of their own, set where they are
    #: called: one retry for Google elevation and time zones, videos, the Hong Kong Observatory and
    #: archive weather; none for exchange rates, MTR status and the services whose usage policy asks
    #: clients not to repeat a request.
    http_retries: int = Field(2, ge=0)
    #: Tool calls one tool path (plan, edit, question, or the refresh of an outdated plan) may make
    #: before the rest are refused with a data gap. Each path counts its own: an edit of an outdated
    #: plan refreshes it first, so one request can make up to twice this many. A guard against a
    #: runaway loop; a normal trip makes far fewer. 0 turns the cap off.
    max_tool_calls_per_turn: int = Field(150, ge=0)
    max_days: int = 14
    max_attractions_per_day: int = 4
    #: The least a budget should allow per traveller per night, in US dollars (the exchange table's base; the
    #: check converts it to the trip's currency). A new trip whose budget is below it, or zero, is asked about
    #: before any agent runs. 0 turns the check off.
    min_budget_per_person_night: float = Field(150, ge=0)
    memory_similarity_cutoff: float = 0.72
    search_window_months: int = 24
    default_currency: str = "HKD"

    # freshness windows, seconds
    weather_fresh_seconds: int = 6 * 3600
    ticket_fresh_seconds: int = 24 * 3600
    hotel_fresh_seconds: int = 7 * 24 * 3600
    attraction_fresh_seconds: int = 30 * 24 * 3600

    # --- security ----------------------------------------------------------
    canary_token: str = "<<CANARY-PATHFINDER-7f3a9c>>"
    blocklist_enabled: bool = True
    fence_enabled: bool = True

    # --- sharing -----------------------------------------------------------
    # A public share link stops working this many days after it is created.
    share_ttl_days: int = 30

    # --- rate limits -------------------------------------------------------
    # Requests per client IP per minute, counted in memory per process.
    # 0 turns a limit off.
    rate_limit_auth_per_minute: int = 10
    rate_limit_ai_per_minute: int = 30
    rate_limit_create_per_minute: int = 60
    rate_limit_lookup_per_minute: int = 60
    rate_limit_key_test_per_minute: int = 6
    rate_limit_handbook_per_minute: int = 60

    # --- streaming ---------------------------------------------------------
    # The chat stream sends an SSE comment after this many quiet seconds so
    # proxies keep the connection open, and ends a turn that runs longer than
    # turn_deadline_seconds with an `error` event (code turn_timeout). The deadline counts the
    # turn's own work: the wait for an earlier message on the same trip is not part of it.
    sse_keepalive_seconds: float = 15.0
    turn_deadline_seconds: float = 180.0

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
    def local_llm_configured(self) -> bool:
        return bool(self.local_llm_base_url and self.local_llm_model)

    @property
    def langfuse_available(self) -> bool:
        return bool(
            self.langfuse_enabled
            and self.langfuse_public_key
            and self.langfuse_secret_key
            and self.langfuse_host
        )

    @property
    def local_admin_active(self) -> bool:
        """``LOCAL_ADMIN`` as it applies: only ever in development or test."""
        return self.local_admin and self.environment.strip().lower() in DEV_ENVIRONMENTS

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def check_secrets(self) -> None:
        """Refuse to run outside development/test with secrets from the repo.

        The bearer tokens are HS256-signed with ``auth_secret``, so a known
        secret lets anyone forge a sign-in; the admin token guards the admin
        endpoints. Raises ``RuntimeError`` naming every variable to set.
        """
        if self.local_admin_active and "*" in (item.strip() for item in os.environ.get("FORWARDED_ALLOW_IPS", "").split(",")):
            # uvicorn then trusts X-Forwarded-For from every peer and takes its left-most, caller-chosen address,
            # so any caller could name itself 127.0.0.1. The `--forwarded-allow-ips '*'` flag is not visible here.
            raise RuntimeError(
                "Refusing to start with LOCAL_ADMIN=true and FORWARDED_ALLOW_IPS=*: uvicorn would then take the "
                "caller's own X-Forwarded-For as its address, and any caller could pass as this machine. Unset "
                "FORWARDED_ALLOW_IPS (the default trusts 127.0.0.1 and ::1) or list the proxy's address, or unset LOCAL_ADMIN."
            )
        if self.environment.strip().lower() in DEV_ENVIRONMENTS:
            return
        shown = (
            repr(self.environment)
            if "environment" in self.model_fields_set
            else f"{self.environment!r} (ENVIRONMENT is not set)"
        )
        if self.local_admin:
            # Local admin opens every admin route without a token: it is for the owner's own machine only.
            raise RuntimeError(
                f"Refusing to start with ENVIRONMENT={shown} and LOCAL_ADMIN=true. Local admin mode opens the "
                "admin routes without a token and is for development only: unset LOCAL_ADMIN, "
                "or set ENVIRONMENT=development for local work."
            )
        missing: list[str] = []
        if self.auth_secret == DEFAULT_AUTH_SECRET or len(self.auth_secret) < MIN_AUTH_SECRET_LENGTH:
            missing.append(f"AUTH_SECRET (a random value of at least {MIN_AUTH_SECRET_LENGTH} characters)")
        if self.admin_token in PLACEHOLDER_ADMIN_TOKENS:
            missing.append("ADMIN_TOKEN (a random value, not the default or placeholder)")
        if not self.byok_encryption_key:
            missing.append(
                "BYOK_ENCRYPTION_KEY (a Fernet key: python -c "
                "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")"
            )
        if missing:
            raise RuntimeError(
                f"Refusing to start with ENVIRONMENT={shown} and insecure defaults. "
                f"Set {' and '.join(missing)}, or set ENVIRONMENT=development for local work."
            )

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.trace_dir, self.eval_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
