"""Bring-your-own model keys: list providers, test a key, save, choose, delete.

Keys live in the local SQLite file (this is a single-machine app). The API only ever
returns a masked tail; the raw value is used to call the provider and never sent back.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.api.deps import container, is_admin, require_admin
from app.config import Settings

router = APIRouter()

PROVIDERS: list[dict[str, Any]] = [
    {"id": "poe", "label": "Poe", "default_model": "claude-sonnet-5.5", "key_prefix": "sk-poe", "supports_search": False},
    {"id": "openai", "label": "OpenAI", "default_model": "gpt-5", "key_prefix": "sk-", "supports_search": False},
    {"id": "anthropic", "label": "Anthropic", "default_model": "claude-sonnet-5.5", "key_prefix": "sk-ant-", "supports_search": False},
    {"id": "deepseek", "label": "DeepSeek", "default_model": "deepseek-chat", "key_prefix": "sk-", "supports_search": True},
    {"id": "xai", "label": "xAI", "default_model": "grok-4.20-0309-non-reasoning", "key_prefix": "xai-", "supports_search": True},
    {"id": "google", "label": "Google AI", "default_model": "gemini-3.5-flash-lite", "key_prefix": "AIza", "supports_search": True},
    {"id": "openrouter", "label": "OpenRouter", "default_model": "anthropic/claude-sonnet-4", "key_prefix": "sk-or-", "supports_search": False},
    {"id": "custom", "label": "OpenAI-compatible endpoint", "default_model": "", "key_prefix": "", "supports_search": False, "needs_base_url": True},
]

BASE_URLS = {
    "poe": "https://api.poe.com/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com",
    "xai": "https://api.x.ai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
}


def _tail(key: str) -> str:
    return ("••••" + key[-4:]) if key and len(key) > 4 else ""


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "provider": row["provider"],
        "label": row.get("label", ""),
        "model": row.get("model", ""),
        "base_url": row.get("base_url", ""),
        "key_tail": _tail(row.get("key", "")),
        "active": bool(row.get("active")),
        "use_for_search": bool(row.get("use_for_search")),
        "last_test_ok": row.get("last_test_ok"),
        "last_tested_at": row.get("last_tested_at"),
        "last_error": row.get("last_error", ""),
        "unreadable": False,
    }


async def load_keys(c) -> list[dict[str, Any]]:
    return await c.store.get_kv("llm:keys") or []


async def chosen_key(c) -> dict[str, Any] | None:
    choice = await c.store.get_kv("llm:choice")
    if not choice:
        return None
    for row in await load_keys(c):
        if row["id"] == choice:
            return row
    return None


async def status(c) -> dict[str, Any]:
    keys = await load_keys(c)
    chosen = await chosen_key(c)
    if chosen is not None:
        return {
            "source": "byok",
            "provider": chosen["provider"],
            "model": chosen.get("model", ""),
            "problem": "",
            "local": {"configured": False, "reachable": False, "model": ""},
        }
    settings = c.settings
    base, key, model = settings.writer
    source = "env" if key else "none"
    return {
        "source": source,
        "provider": settings.llm_provider,
        "model": model,
        "problem": "",
        "local": {"configured": False, "reachable": False, "model": ""},
    }


async def effective_writer(c, settings: Settings) -> Settings:
    """A Settings copy whose writer is the traveller's chosen key, when there is one."""
    chosen = await chosen_key(c)
    if chosen is None:
        return settings
    provider = chosen["provider"]
    base = chosen.get("base_url") or BASE_URLS.get(provider, "")
    update = {
        "llm_provider": provider,
        "llm_base_url": base,
        "llm_api_key": chosen.get("key", ""),
        "llm_model": chosen.get("model") or settings.writer[2],
        "openai_base_url": base,
        "openai_api_key": chosen.get("key", ""),
        "openai_model": chosen.get("model") or settings.writer[2],
    }
    return settings.model_copy(update=update)


@router.get("/api/llm/providers")
async def providers() -> dict[str, Any]:
    return {"providers": PROVIDERS}


@router.get("/api/llm/keys")
async def keys(request: Request, scope: str = "") -> dict[str, Any]:
    c = container(request)
    if scope == "server":
        require_admin(request)
    rows = await load_keys(c)
    return {"keys": [_public(row) for row in rows], "status": await status(c)}


@router.post("/api/llm/keys/test")
async def test_key(request: Request, scope: str = "") -> dict[str, Any]:
    c = container(request)
    if scope == "server":
        require_admin(request)
    body = await request.json()
    result = await _test(body.get("provider", ""), body.get("api_key", ""), body.get("model", ""), body.get("base_url", ""))
    return result


@router.post("/api/llm/keys")
async def save_key(request: Request, scope: str = "") -> dict[str, Any]:
    c = container(request)
    if scope == "server":
        require_admin(request)
    body = await request.json()
    provider = str(body.get("provider", ""))
    key = str(body.get("api_key", "")).strip()
    model = str(body.get("model", "")).strip()
    if provider not in {row["id"] for row in PROVIDERS}:
        raise HTTPException(status_code=400, detail="Unknown provider.")
    if not key or not model:
        raise HTTPException(status_code=400, detail="A key and a model are needed.")
    result = await _test(provider, key, model, body.get("base_url", ""))
    if result["kind"] in {"auth", "model"}:
        raise HTTPException(status_code=400, detail=result["message"])
    rows = await load_keys(c)
    row = {
        "id": secrets.token_hex(8),
        "provider": provider,
        "label": str(body.get("label") or PROVIDERS[[p["id"] for p in PROVIDERS].index(provider)]["label"]),
        "model": model,
        "base_url": str(body.get("base_url") or ""),
        "key": key,
        "use_for_search": bool(body.get("use_for_search")),
        "active": not any(item.get("active") for item in rows),
        "last_test_ok": result["ok"],
        "last_tested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "last_error": "" if result["ok"] else result["message"],
    }
    rows.append(row)
    await c.store.set_kv("llm:keys", rows)
    answer: dict[str, Any] = {"key": _public(row)}
    if not result["ok"]:
        answer["warning"] = result["message"]
    return answer


@router.post("/api/llm/keys/{key_id}/test")
async def retest(key_id: str, request: Request, scope: str = "") -> dict[str, Any]:
    c = container(request)
    if scope == "server":
        require_admin(request)
    rows = await load_keys(c)
    row = next((item for item in rows if item["id"] == key_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="That key is not there.")
    result = await _test(row["provider"], row["key"], row.get("model", ""), row.get("base_url", ""))
    row["last_test_ok"] = result["ok"]
    row["last_tested_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row["last_error"] = "" if result["ok"] else result["message"]
    await c.store.set_kv("llm:keys", rows)
    return {"key": _public(row), "result": result}


@router.patch("/api/llm/keys/{key_id}")
async def patch_key(key_id: str, request: Request, scope: str = "") -> dict[str, Any]:
    c = container(request)
    if scope == "server":
        require_admin(request)
    body = await request.json()
    rows = await load_keys(c)
    row = next((item for item in rows if item["id"] == key_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="That key is not there.")
    for field in ("model", "base_url"):
        if body.get(field):
            row[field] = str(body[field])
    if "use_for_search" in body:
        row["use_for_search"] = bool(body["use_for_search"])
    await c.store.set_kv("llm:keys", rows)
    return {"key": _public(row)}


@router.delete("/api/llm/keys/{key_id}")
async def delete_key(key_id: str, request: Request, scope: str = "") -> dict[str, Any]:
    c = container(request)
    if scope == "server":
        require_admin(request)
    rows = await load_keys(c)
    kept = [row for row in rows if row["id"] != key_id]
    if len(kept) != len(rows):
        for row in kept:
            row["active"] = True if not any(item.get("active") for item in kept) else row.get("active", False)
        await c.store.set_kv("llm:keys", kept)
        if await c.store.get_kv("llm:choice") == key_id:
            await c.store.delete_kv("llm:choice")
    return {"deleted": True}


@router.put("/api/llm/choice")
async def choose(request: Request, scope: str = "") -> dict[str, Any]:
    c = container(request)
    if scope == "server":
        require_admin(request)
    body = await request.json()
    key_id = body.get("key_id")
    rows = await load_keys(c)
    for row in rows:
        row["active"] = row["id"] == key_id
    await c.store.set_kv("llm:keys", rows)
    if key_id:
        await c.store.set_kv("llm:choice", key_id)
    else:
        await c.store.delete_kv("llm:choice")
    return {"status": await status(c)}


async def _test(provider: str, key: str, model: str, base_url: str = "") -> dict[str, Any]:
    base = (base_url or BASE_URLS.get(provider, "")).rstrip("/")
    if not base:
        return {"ok": False, "kind": "network", "message": "This provider needs an endpoint URL.", "latency_ms": 0, "models": []}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{base}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with the single word ok."}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
        latency = round((time.perf_counter() - started) * 1000, 1)
        if response.status_code < 400:
            return {"ok": True, "kind": "ok", "message": "The key works.", "latency_ms": latency, "models": [model]}
        text = response.text[:300]
        kind = "other"
        if response.status_code in {401, 403}:
            kind = "auth"
        elif response.status_code == 404:
            kind = "model"
        elif response.status_code == 429:
            kind = "rate_limit"
        elif response.status_code == 402 or "credit" in text.lower() or "quota" in text.lower():
            kind = "credit"
        message = {
            "auth": "The provider rejected this key.",
            "model": "This key cannot use that model.",
            "rate_limit": "The provider is rate-limiting this key; it was kept and can be retried.",
            "credit": "The account is out of credit; the key was kept.",
        }.get(kind, "The provider refused the test call.")
        return {"ok": False, "kind": kind, "message": message, "latency_ms": latency, "models": []}
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "kind": "network",
            "message": f"The endpoint could not be reached: {exc}",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "models": [],
        }
