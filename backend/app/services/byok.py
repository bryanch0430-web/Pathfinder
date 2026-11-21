"""Bring-your-own writer key.

A saved key is encrypted with a key derived from LOCAL_AUTH_SECRET. The browser
only ever receives a masked tail. When a key is chosen, the next planner call
uses that provider and model and does not fall back to the server key.
"""

from __future__ import annotations

import base64
import hashlib
import re
import uuid
from dataclasses import dataclass

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.llm_key import LlmKey

_KEY_FORMAT = re.compile(r"[!-~]{8,512}")


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    wire: str
    base_url: str
    default_model: str


PROVIDERS: dict[str, ProviderSpec] = {
    "poe": ProviderSpec("poe", "Poe", "openai", "https://api.poe.com/v1", "claude-sonnet-5-5"),
    "xai": ProviderSpec("xai", "xAI Grok", "openai", "https://api.x.ai/v1", "grok-4.7"),
    "deepseek": ProviderSpec("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1", "deepseek-chat"),
    "anthropic": ProviderSpec("anthropic", "Anthropic Claude", "anthropic", "https://api.anthropic.com/v1", "claude-sonnet-5-5"),
    "openai": ProviderSpec("openai", "OpenAI", "openai", "https://api.openai.com/v1", "gpt-4o-mini"),
    "gemini": ProviderSpec(
        "gemini", "Google Gemini", "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash",
    ),
}


def mask_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "…"
    return f"{api_key[:3]}…{api_key[-4:]}"


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.LOCAL_AUTH_SECRET.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode()).decode()


def decrypt_key(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("unreadable") from exc


def provider_rows() -> list[dict]:
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "default_model": spec.default_model,
            "efforts": effort_options(spec.id, spec.default_model),
        }
        for spec in PROVIDERS.values()
    ]


def valid_key(api_key: str) -> bool:
    return bool(_KEY_FORMAT.fullmatch(api_key))


async def list_keys(db: AsyncSession) -> list[LlmKey]:
    return list((await db.execute(select(LlmKey).order_by(LlmKey.created_at))).scalars().all())


async def active_key(db: AsyncSession) -> LlmKey | None:
    return (await db.execute(select(LlmKey).where(LlmKey.is_active.is_(True)))).scalar_one_or_none()


def public_key(row: LlmKey) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "label": PROVIDERS[row.provider].label if row.provider in PROVIDERS else row.provider,
        "model": row.model,
        "hint": row.key_hint,
        "active": row.is_active,
        "temperature": row.temperature,
        "reasoning": row.reasoning,
    }


def server_catalog() -> list[dict]:
    """DeepSeek, Poe, and Grok as configured in the server file. No secrets leave this process."""
    return [
        {
            "id": "deepseek",
            "label": "DeepSeek",
            "model": settings.DEEPSEEK_MODEL,
            "ready": bool(settings.DEEPSEEK_API_KEY.strip()),
            "efforts": effort_options("deepseek", settings.DEEPSEEK_MODEL),
        },
        {
            "id": "poe",
            "label": "Poe · Claude Sonnet",
            "model": settings.POE_MODEL or "claude-sonnet-5-5",
            "ready": bool(settings.POE_API_KEY.strip()),
            "efforts": effort_options("poe", settings.POE_MODEL or "claude-sonnet-5-5"),
        },
        {
            "id": "xai",
            "label": "xAI Grok",
            "model": settings.XAI_MODEL or "grok-4.7",
            "ready": bool(settings.XAI_API_KEY.strip()),
            "efforts": effort_options("xai", settings.XAI_MODEL or "grok-4.7"),
        },
    ]


def chosen_server_writer() -> str:
    from app.services.admin_settings import _cache

    saved = (_cache.get("server_writer") or settings.SERVER_WRITER or "deepseek").strip().lower()
    if saved == "grok":
        saved = "xai"
    if saved not in {"deepseek", "poe", "xai"}:
        saved = "deepseek"
    return saved


def effort_options(provider: str, model: str) -> list[str]:
    """Levels this provider will accept. xhigh is only offered where the vendor documents it."""
    name = (model or "").casefold()
    kind = (provider or "").casefold()
    if kind in {"xai", "grok"} or name.startswith("grok-"):
        return ["low", "medium", "high", "xhigh"]
    if kind == "deepseek" or "deepseek" in name:
        return ["none", "low", "high", "max"]
    if kind in {"anthropic", "poe"} or "claude" in name:
        if any(token in name for token in ("sonnet-5", "opus-5", "opus-4-7", "opus-4.7", "opus-4-8", "opus-4.8", "haiku-5")):
            return ["low", "medium", "high", "xhigh", "max"]
        return ["low", "medium", "high"]
    if kind == "gemini" or "gemini" in name:
        return ["low", "medium", "high"]
    if kind == "openai" and any(token in name for token in ("gpt-5", "gpt-6", "o1", "o3", "o4")):
        return ["low", "medium", "high"]
    return []


def clean_sampling(
    temperature: str | float | None, reasoning: str | None, provider: str, model: str,
) -> tuple[str | None, str | None]:
    """Blank means keep the code default. Temperature is 0 to 2."""
    if temperature is None or str(temperature).strip() == "":
        temp = None
    else:
        try:
            number = float(temperature)
        except (TypeError, ValueError) as exc:
            raise ValueError("Temperature must be a number from 0 to 2.") from exc
        if number < 0 or number > 2:
            raise ValueError("Temperature must be a number from 0 to 2.")
        temp = f"{number:.1f}"
    effort = (reasoning or "").strip().lower()
    if not effort:
        effort_value = None
    elif effort not in set(effort_options(provider, model)):
        allowed = effort_options(provider, model)
        if not allowed:
            raise ValueError(f"{model or provider} does not take a reasoning effort. Leave it on default.")
        raise ValueError(f"{model or provider} accepts {', '.join(allowed)}.")
    else:
        effort_value = effort
    return temp, effort_value


def server_sampling() -> dict:
    from app.services.admin_settings import _cache

    return {
        "temperature": _cache.get("server_temperature") or None,
        "reasoning": _cache.get("server_reasoning") or None,
    }


def server_view() -> dict:
    rows = server_catalog()
    selected = chosen_server_writer()
    picked = next(row for row in rows if row["id"] == selected)
    effective = picked if picked["ready"] else next(row for row in rows if row["id"] == "deepseek")
    sample = server_sampling()
    return {
        "selected": selected,
        "effective": effective["id"],
        "ready": picked["ready"],
        "choices": rows,
        "temperature": sample["temperature"],
        "reasoning": sample["reasoning"],
    }


def server_provider():
    """The env-backed writer, or None when that choice should use the DeepSeek client already built."""
    from app.services.llm.providers.byok_chat import ByokChatProvider

    view = server_view()
    sample = server_sampling()
    if view["effective"] == "poe":
        return ByokChatProvider(
            api_key=settings.POE_API_KEY.strip(),
            base_url=settings.POE_BASE_URL,
            model=settings.POE_MODEL or "claude-sonnet-5-5",
            sampling_temperature=sample["temperature"],
            sampling_reasoning=sample["reasoning"],
        )
    if view["effective"] == "xai":
        return ByokChatProvider(
            api_key=settings.XAI_API_KEY.strip(),
            base_url=settings.XAI_BASE_URL,
            model=settings.XAI_MODEL or "grok-4.7",
            sampling_temperature=sample["temperature"],
            sampling_reasoning=sample["reasoning"],
        )
    return None


async def save_server_writer(
    db: AsyncSession, writer: str, temperature: str | float | None = None, reasoning: str | None = None,
) -> dict:
    from app.services.admin_settings import _write

    if writer not in {"deepseek", "poe", "xai"}:
        raise ValueError(writer)
    model = next(row["model"] for row in server_catalog() if row["id"] == writer)
    temp, effort = clean_sampling(temperature, reasoning, writer, model)
    await _write(db, "server_writer", writer)
    await _write(db, "server_temperature", temp)
    await _write(db, "server_reasoning", effort)
    return server_view()


async def public_status(db: AsyncSession) -> dict:
    row = await active_key(db)
    if row is not None:
        return {"source": "byok", "provider": row.provider, "model": row.model, "hint": row.key_hint}
    view = server_view()
    effective = next(item for item in view["choices"] if item["id"] == view["effective"])
    return {
        "source": "server",
        "provider": effective["id"],
        "model": effective["model"],
        "hint": "",
        "selected": view["selected"],
        "ready": view["ready"],
    }


async def save_sampling(db: AsyncSession, key_id: str, temperature: str | float | None, reasoning: str | None) -> LlmKey | None:
    row = await db.get(LlmKey, key_id)
    if row is None:
        return None
    temp, effort = clean_sampling(temperature, reasoning, row.provider, row.model)
    row.temperature = temp
    row.reasoning = effort
    await db.commit()
    await db.refresh(row)
    return row


async def save_key(
    db: AsyncSession, provider: str, api_key: str, model: str,
    temperature: str | float | None = None, reasoning: str | None = None,
) -> LlmKey:
    for row in await list_keys(db):
        row.is_active = False
    temp, effort = clean_sampling(temperature, reasoning, provider, model)
    saved = LlmKey(
        id=uuid.uuid4().hex,
        provider=provider,
        model=model,
        key_ciphertext=encrypt_key(api_key),
        key_hint=mask_key(api_key),
        is_active=True,
        temperature=temp,
        reasoning=effort,
    )
    db.add(saved)
    await db.commit()
    await db.refresh(saved)
    return saved


async def choose_key(db: AsyncSession, key_id: str | None) -> None:
    rows = await list_keys(db)
    for row in rows:
        row.is_active = row.id == key_id
    await db.commit()


async def delete_key(db: AsyncSession, key_id: str) -> bool:
    row = await db.get(LlmKey, key_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def probe(provider: str, api_key: str, model: str) -> tuple[bool, str]:
    """One short completion. The returned text never includes the key."""
    spec = PROVIDERS[provider]
    try:
        if spec.wire == "anthropic":
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{spec.base_url}/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 8,
                        "messages": [{"role": "user", "content": "Reply with the word ready."}],
                    },
                )
        else:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{spec.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "max_tokens": 8,
                        "messages": [{"role": "user", "content": "Reply with the word ready."}],
                    },
                )
    except httpx.HTTPError:
        return False, "The provider could not be reached."
    if response.status_code in {401, 403}:
        return False, "The key was refused."
    if response.status_code == 404:
        return False, "That model was not found for this key."
    if response.status_code >= 400:
        return False, f"The provider answered {response.status_code}."
    return True, "The key can call this model."
