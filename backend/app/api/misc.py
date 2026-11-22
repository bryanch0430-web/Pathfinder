"""Smaller product surfaces: status, runtime, places, guides, wallet and popular trips."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.api.deps import container, identity_for, is_admin
from app.api.views import runtime_config
from app.evidence import atlas
from app.evidence.providers import Providers

router = APIRouter()

_FX_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

FALLBACK_RATES = {
    "HKD": 1.0, "USD": 0.128, "EUR": 0.118, "GBP": 0.101, "JPY": 19.4,
    "CNY": 0.92, "TWD": 4.1, "SGD": 0.172, "THB": 4.5, "KRW": 176.0,
}


@router.get("/api/ready")
async def ready(request: Request) -> dict[str, Any]:
    c = container(request)
    return {
        "status": "ok",
        "version": "5.0.0",
        "environment": c.settings.environment,
        "local_admin": is_admin(request),
        "sandbox": c.settings.sandbox,
        "writer": c.settings.writer[2] if c.settings.writer[1] else "",
    }


@router.get("/api/runtime")
async def runtime(request: Request) -> dict[str, Any]:
    return await runtime_config(container(request))


@router.get("/api/places/suggest")
async def suggest(request: Request, q: str = "") -> dict[str, Any]:
    c = container(request)
    query = (q or "").strip()
    if len(query) < 2:
        return {"items": []}
    if not c.settings.sandbox and c.settings.google_places_api_key:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.post(
                    "https://places.googleapis.com/v1/places:autocomplete",
                    json={"input": query, "languageCode": "en"},
                    headers={
                        "X-Goog-Api-Key": c.settings.google_places_api_key,
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                rows = response.json().get("suggestions") or []
                items = []
                for row in rows[:8]:
                    place = row.get("placePrediction") or {}
                    label = (place.get("text") or {}).get("text")
                    place_id = place.get("placeId") or ""
                    if label:
                        items.append({"label": label, "place_id": place_id})
                if items:
                    return {"items": items}
        except (httpx.HTTPError, ValueError):
            pass
    lowered = query.lower()
    items = [
        {"label": meta["name"], "place_id": f"atlas:{name}"}
        for name, meta in atlas.CITIES.items()
        if lowered in name or lowered in meta["name"].lower()
    ]
    return {"items": items[:8]}


@router.get("/api/images/place")
async def place_image(request: Request, name: str = "", q: str = "") -> RedirectResponse:
    title = (name or q or "").strip()
    if not title:
        raise HTTPException(status_code=404, detail="No place named.")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "prop": "pageimages",
                    "piprop": "thumbnail",
                    "pithumbsize": 900,
                    "titles": title,
                    "format": "json",
                    "redirects": 1,
                },
                headers={"User-Agent": container(request).settings.wikimedia_user_agent},
            )
            response.raise_for_status()
            pages = ((response.json().get("query") or {}).get("pages")) or {}
            for page in pages.values():
                thumb = (page.get("thumbnail") or {}).get("source")
                if thumb:
                    return RedirectResponse(thumb, status_code=302)
    except (httpx.HTTPError, ValueError):
        pass
    raise HTTPException(status_code=404, detail="No picture found.")


@router.get("/api/handbook")
async def handbook(request: Request, destination: str = "", q: str = "", limit: int = 3) -> dict[str, Any]:
    c = container(request)
    city = (destination or "").strip()
    meta = atlas.city_meta(city) or {}
    key = atlas._key(city)
    chunks: list[dict[str, Any]] = []
    if key:
        for index, item in enumerate([*atlas.attractions(key), *atlas.hotels(key), *atlas.meals(key)]):
            text = f"{item.name}. {item.summary}".strip()
            if q and q.lower() not in text.lower() and not any(word in text.lower() for word in q.lower().split()):
                continue
            chunks.append(
                {
                    "id": f"{key}-{index}",
                    "destination": meta.get("name", key),
                    "section": "attraction" if item.kind == "attraction" else ("stay" if item.kind == "hotel" else "food"),
                    "text": text,
                    "title": item.name,
                    "url": item.source_refs[0].url if item.source_refs else "",
                    "score": float(item.rating or 0),
                    "country_code": meta.get("country_code", ""),
                    "license": "Curated summary",
                    "last_verified": "2026-11-01",
                    "stale": False,
                    "content_hash": item.ref,
                    "corpus_version": "atlas-1",
                    "retrieval": {"mode": "atlas", "reranked": False},
                }
            )
    note = "Curated destination notes." if chunks else "No handbook notes for that destination yet."
    return {"items": chunks[: max(1, min(limit, 8))], "note": note, "retrieval": {"mode": "atlas", "reranked": False}}


@router.get("/api/wallet")
async def wallet(request: Request, base: str = "HKD") -> dict[str, Any]:
    code = (base or "HKD").strip().upper()[:3]
    cached = _FX_CACHE.get(code)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    rates: dict[str, float] = {}
    source = "Static reference rates"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(f"https://open.er-api.com/v6/latest/{code}")
            response.raise_for_status()
            payload = response.json()
            rates = {key: float(value) for key, value in (payload.get("rates") or {}).items()}
            source = "open.er-api.com"
    except (httpx.HTTPError, ValueError):
        rates = {}
    if not rates:
        if code == "HKD":
            rates = dict(FALLBACK_RATES)
        else:
            base_hkd = FALLBACK_RATES.get(code, 1.0)
            rates = {"HKD": 1.0 / base_hkd, **{k: v / base_hkd for k, v in FALLBACK_RATES.items()}}
    snapshot = {
        "base": code,
        "rates": rates,
        "source": source,
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": "Indicative rates for planning; the booking provider's rate is final.",
    }
    _FX_CACHE[code] = (time.time(), snapshot)
    return snapshot


@router.get("/api/wallet/convert")
async def convert(request: Request, amount: float = 1.0, base: str = "HKD", to: str = "JPY") -> dict[str, Any]:
    snapshot = await wallet(request, base)
    rate = (snapshot.get("rates") or {}).get((to or "").upper())
    if rate is None:
        raise HTTPException(status_code=404, detail="That currency is not listed.")
    return {"amount": amount, "base": base.upper(), "to": to.upper(), "rate": rate, "result": round(amount * rate, 2)}


@router.get("/api/popular")
async def popular(request: Request, q: str = "", destination: str = "", limit: int = 8) -> dict[str, Any]:
    c = container(request)
    identity = identity_for(request)
    rows = await c.store.list_wishlist(identity) if identity else []
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = (row.get("destination") or "").strip()
        if not name:
            continue
        if destination and destination.lower() not in name.lower():
            continue
        entry = grouped.setdefault(
            name,
            {"destination": name, "plans": 0, "likes": 0, "days": 0, "year": None, "places": [], "summary": ""},
        )
        entry["plans"] += 1
        entry["likes"] += int(row.get("stars") or 0)
        try:
            body = __import__("json").loads(row.get("body") or "{}")
        except ValueError:
            body = {}
        entry["days"] = max(entry["days"], len(body.get("days") or []))
        entry["places"] = (entry["places"] + [item.get("name") for day in (body.get("days") or []) for item in day.get("items", [])])[:6]
        entry["summary"] = entry["summary"] or (body.get("summary") or "")[:200]
    items = list(grouped.values())
    if q:
        items = [item for item in items if q.lower() in item["destination"].lower()]
    if not items and q:
        meta = atlas.city_meta(q)
        if meta:
            items = [
                {
                    "destination": meta["name"],
                    "plans": 0,
                    "likes": 0,
                    "days": 0,
                    "year": None,
                    "places": [item.name for item in atlas.attractions(atlas._key(q))][:5],
                    "summary": "Popular places from the Pathfinder atlas.",
                    "match": [q],
                }
            ]
    dropped = [] if items else [{"destination": destination or q, "reason": "no saved trips yet"}]
    return {
        "items": items[: max(1, limit)],
        "trace": {
            "note": "Built from the traveller's saved trips." if rows else "No saved trips yet; showing atlas ideas.",
            "filter": "personal" if identity else "none",
            "dropped": dropped,
            "mode": "wishlist" if rows else "atlas",
            "candidates": len(rows),
        },
    }


@router.get("/api/admin/serpapi-keys")
async def serp_keys(request: Request) -> dict[str, Any]:
    c = container(request)
    stored = await c.store.get_kv("serpapi:keys")
    if isinstance(stored, list):
        return {"keys": stored}
    keys = []
    for index, chunk in enumerate((c.settings.serpapi_key_pool or "").split(";")):
        parts = chunk.split("|")
        if len(parts) != 3:
            continue
        status, label, key = parts
        keys.append(
            {
                "id": f"env-{index}",
                "label": label,
                "key_tail": key[-4:],
                "status": status if status in {"active", "ready", "exhausted", "paused"} else "ready",
                "position": index + 1,
            }
        )
    return {"keys": keys}


@router.post("/api/admin/serpapi-keys")
async def add_serp_key(request: Request) -> dict[str, Any]:
    c = container(request)
    body = await request.json()
    stored = await c.store.get_kv("serpapi:keys") or []
    stored.append(
        {
            "id": f"key-{len(stored) + 1}",
            "label": str(body.get("label", ""))[:80],
            "key_tail": str(body.get("api_key", ""))[-4:],
            "status": body.get("status", "ready"),
            "position": len(stored) + 1,
            "_key": str(body.get("api_key", "")),
        }
    )
    await c.store.set_kv("serpapi:keys", stored)
    return {"keys": stored}


@router.patch("/api/admin/serpapi-keys/{key_id}")
async def set_serp_key(key_id: str, request: Request) -> dict[str, Any]:
    c = container(request)
    body = await request.json()
    stored = await c.store.get_kv("serpapi:keys") or []
    for row in stored:
        if row.get("id") == key_id:
            row["status"] = body.get("status", row.get("status"))
    await c.store.set_kv("serpapi:keys", stored)
    return {"keys": stored}
