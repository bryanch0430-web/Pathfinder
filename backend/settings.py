"""Single settings module for every tunable value in Pathfinder.

All thresholds named in the proposal live here with the proposal's defaults, so a change is
one edit in one place (and visible in `.env.example`). Values the proposal leaves open use the
smallest reasonable default; each one is listed in DECISIONS.md.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PATHFINDER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- System 1 router -------------------------------------------------------------------
    # Proposal: "If the route probability is at least a set threshold, say 0.8, the request is
    # sent down that path. Below that threshold ... the turn returns to the user."
    router_confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)

    # ---- Bounded model repair ----------------------------------------------------------------
    # Proposal: the model "may repair it at most twice" (tool validation errors) and the
    # TripPlan parser "may repair it a bounded number of times".
    max_model_repairs: int = Field(default=2, ge=0)

    # ---- Tool retry policy (timeout / rate limit / brief server fault) -----------------------
    tool_max_attempts: int = Field(default=3, ge=1)
    tool_backoff_base_s: float = Field(default=0.2, gt=0)
    tool_backoff_max_s: float = Field(default=2.0, gt=0)
    tool_call_timeout_s: float = Field(default=4.0, gt=0)
    # Upper bound on wall-clock spent on one logical tool call including all retries.
    tool_retry_budget_s: float = Field(default=8.0, gt=0)

    # ---- Planning time budget ----------------------------------------------------------------
    # Proposal: the four pre-planning agents then the planner run "inside a time budget"; when
    # exceeded, unfinished tool results are marked unavailable and the plan is still returned.
    preplanning_time_budget_s: float = Field(default=20.0, gt=0)

    # ---- Staleness limits (seconds) -----------------------------------------------------------
    # Proposal: "Weather, fares, and ticket availability also go stale: each tool result carries
    # a timestamp, and an item older than a set limit is marked for refresh."
    stale_after_weather_s: int = Field(default=6 * 3600, gt=0)
    stale_after_ticket_s: int = Field(default=1 * 3600, gt=0)
    stale_after_hotel_s: int = Field(default=24 * 3600, gt=0)
    stale_after_attraction_s: int = Field(default=7 * 24 * 3600, gt=0)

    # ---- Long-term memory -----------------------------------------------------------------------
    # Proposal: "A saved trip is retrieved only when it is above the similarity cutoff, so a
    # weak match cannot steer a new plan."
    similarity_cutoff: float = Field(default=0.75, ge=-1.0, le=1.0)
    retrieval_top_k: int = Field(default=3, ge=1)
    history_window: int = Field(default=10, ge=1)
    embedding_dim: int = Field(default=1536, ge=8)

    # ---- Security -------------------------------------------------------------------------------
    # Canary token placed in every system prompt; leakage is detected by a string check.
    # Empty -> a random token is generated once per process.
    canary_token: SecretStr = Field(default=SecretStr(""))

    # ---- Storage --------------------------------------------------------------------------------
    # Team decision: PostgreSQL + pgvector behind a repository interface. "memory" keeps the app
    # and the tests runnable without a database; "cosmos" is a provisional alternative.
    storage_backend: Literal["memory", "postgres", "cosmos"] = "memory"
    database_url: str = "postgresql+asyncpg://pathfinder:pathfinder@localhost:5432/pathfinder"

    # ---- Model providers (all TODO(provisional): Appendix B says assignment is provisional) ----
    # TODO(provisional): router model is "Jev" (TypeSafe AI) per Appendix B.
    router_provider: Literal["mock", "jev"] = "mock"
    router_model: str = "jev"
    # TODO(provisional): agents / planner / note cleaning / chat use "Grok 4.7" via the xAI API.
    agent_llm_provider: Literal["mock", "xai"] = "mock"
    agent_model: str = "grok-4.7"
    # TODO(provisional): judge model from a different family (e.g. GPT-4) to avoid self-scoring.
    judge_provider: Literal["mock", "openai"] = "mock"
    judge_model: str = "gpt-4"
    # TODO(provisional): embeddings from OpenAI text-embedding-3-large.
    embedding_provider: Literal["mock", "openai"] = "mock"
    embedding_model: str = "text-embedding-3-large"
    # TODO(provisional): reranker over the retrieved short list, provisionally also Jev.
    reranker_provider: Literal["mock", "jev"] = "mock"
    xai_api_key: SecretStr = Field(default=SecretStr(""))
    openai_api_key: SecretStr = Field(default=SecretStr(""))
    jev_api_key: SecretStr = Field(default=SecretStr(""))

    # ---- Tool providers (all TODO(provisional)) ----------------------------------------------
    search_provider: Literal["mock", "web"] = "mock"
    # Ordered fallback chain: proposal says "maps with Google and AMap as fallback".
    maps_providers: list[Literal["mock", "google", "amap"]] = Field(default_factory=lambda: ["mock"])
    weather_provider: Literal["mock", "live"] = "mock"
    places_provider: Literal["mock", "google"] = "mock"
    ticket_provider: Literal["mock", "live"] = "mock"
    google_maps_api_key: SecretStr = Field(default=SecretStr(""))
    amap_api_key: SecretStr = Field(default=SecretStr(""))

    # ---- Observability ----------------------------------------------------------------------------
    langfuse_public_key: SecretStr = Field(default=SecretStr(""))
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""))
    langfuse_host: str = "https://cloud.langfuse.com"
    log_dir: Path = Path("var/logs")
    # Write every LLM and tool call record as JSON lines under log_dir (in addition to memory).
    call_log_jsonl: bool = True

    # ---- API ---------------------------------------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    def resolved_canary(self) -> str:
        """Return the canary token, generating a process-wide random one if unset."""
        value = self.canary_token.get_secret_value()
        if value:
            return value
        return _process_canary()

    @property
    def langfuse_enabled(self) -> bool:
        return bool(
            self.langfuse_public_key.get_secret_value() and self.langfuse_secret_key.get_secret_value()
        )


@lru_cache(maxsize=1)
def _process_canary() -> str:
    return f"PF-CANARY-{secrets.token_hex(8)}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
