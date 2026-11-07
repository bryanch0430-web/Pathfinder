"""The providers a user can bring their own key for, and how to talk to each."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind
from app.domain.ports import LLM
from app.infrastructure.anthropic_llm import ANTHROPIC_VERSION, AnthropicLLM
from app.infrastructure.http import current_transport
from app.infrastructure.llm import OpenAICompatibleLLM
from app.security.scrub import scrub


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    wire: Literal["openai", "anthropic"]
    base_url: str
    default_model: str
    key_prefix: str
    supports_search: bool


# Default models are starting values; the UI fills the real list from /models after a successful test.
PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        "anthropic", "Anthropic Claude", "anthropic", "https://api.anthropic.com/v1",
        "claude-sonnet-5-5", "sk-ant-", False,
    ),
    "openai": ProviderSpec(
        "openai", "OpenAI", "openai", "https://api.openai.com/v1", "gpt-4o-mini", "sk-", False,
    ),
    "poe": ProviderSpec(
        "poe", "Poe", "openai", "https://api.poe.com/v1", "GPT-4o-mini", "", False,
    ),
    "xai": ProviderSpec(
        "xai", "xAI Grok", "openai", "https://api.x.ai/v1", "grok-4.20-0309-non-reasoning", "xai-", True,
    ),
    "gemini": ProviderSpec(
        "gemini", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.5-flash", "AIza", False,
    ),
}


def build_llm(spec: ProviderSpec, api_key: str, model: str, settings: Settings) -> LLM:
    if spec.wire == "anthropic":
        return AnthropicLLM(settings, api_key=api_key, model=model)
    return OpenAICompatibleLLM(settings, base_url=spec.base_url, api_key=api_key, model=model, label=spec.label)


async def list_models(spec: ProviderSpec, api_key: str) -> list[str]:
    """The model ids the key can use, sorted. ``[]`` on any error; never raises."""
    headers = {"Authorization": f"Bearer {api_key}"}
    if spec.wire == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    params = {"limit": 1000} if spec.wire == "anthropic" else None
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=current_transport()) as client:
            response = await client.get(f"{spec.base_url}/models", headers=headers, params=params)
        if response.status_code >= 400:
            return []
        rows = response.json().get("data")
        if not isinstance(rows, list):
            return []
        ids = {str(row["id"]).removeprefix("models/") for row in rows if isinstance(row, dict) and row.get("id")}
        return sorted(ids)
    except Exception:
        return []


@dataclass
class KeyTestResult:
    ok: bool
    kind: str  # ok, auth, model, rate_limit, credit, network or other
    message: str
    latency_ms: int
    models: list[str] = field(default_factory=list)


async def probe_key(spec: ProviderSpec, api_key: str, model: str, settings: Settings) -> KeyTestResult:
    """Make one tiny request with the key, then fetch the model list."""
    llm = build_llm(spec, api_key, model, settings)
    started = time.perf_counter()
    try:
        await llm.complete(
            system="Reply with the word OK.",
            user="OK?",
            purpose="key_test",
            json_mode=False,
            temperature=0,
            max_tokens=16,
        )
    except ProviderNoData as exc:
        # A 2xx with no text (a reasoning model given a tiny budget) still proves the key works.
        # A 404 is also ProviderNoData, but its message is "llm returned HTTP 404".
        if not str(exc).startswith("writer model returned"):
            return _failure(exc, api_key, started)
        latency = int((time.perf_counter() - started) * 1000)
        return KeyTestResult(True, "ok", "The model answered without text; the key works.", latency, await list_models(spec, api_key))
    except ProviderError as exc:
        return _failure(exc, api_key, started)
    latency = int((time.perf_counter() - started) * 1000)
    return KeyTestResult(True, "ok", "OK", latency, await list_models(spec, api_key))


#: ``ProviderError`` keeps no status: the adapters' message is "<label> returned HTTP <status>".
_HTTP_402 = re.compile(r"\bHTTP 402\b")
_OUT_OF_CREDIT = re.compile(r"credit|quota|billing|insufficient", re.IGNORECASE)


def _failure(exc: ProviderError, api_key: str, started: float) -> KeyTestResult:
    detail = scrub(exc.detail or "", [api_key])
    if exc.kind == FailureKind.AUTH:
        kind = "auth"
    elif _HTTP_402.search(str(exc)) or _OUT_OF_CREDIT.search(detail):
        # The key authenticated but its account cannot pay (Anthropic answers 400 "credit balance is too low").
        kind = "credit"
    elif exc.kind == FailureKind.RATE_LIMIT:
        kind = "rate_limit"
    elif exc.kind in {FailureKind.NETWORK, FailureKind.TIMEOUT}:
        kind = "network"
    elif exc.kind in {FailureKind.INVALID_REQUEST, FailureKind.NO_DATA} and "model" in detail.lower():
        kind = "model"
    else:
        kind = "other"
    message = scrub(f"{exc} {detail}".strip(), [api_key])[:200]
    return KeyTestResult(False, kind, message, int((time.perf_counter() - started) * 1000))
