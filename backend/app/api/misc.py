"""Smaller product surfaces: status, runtime, places, guides, wallet and popular trips."""

from __future__ import annotations

import html
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.api.deps import container, identity_for, is_admin
from app.api.views import runtime_config
from app.core.currencies import normalise_currency
from app.evidence import place_corpus as corpus
from app.evidence.providers import Providers
from app.security.redact import GOOGLE_PHOTO_NAME, clamp_width, google_photo_url

router = APIRouter()

_FX_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PHOTO_CACHE: dict[tuple[str, str], tuple[float, str]] = {}
_GOOGLE_PHOTO_CACHE: dict[tuple[str, int], tuple[float, bytes, str]] = {}
GOOGLE_PHOTO_CACHE_MAX = 64
GOOGLE_PHOTO_MAX_BYTES = 5_000_000
GOOGLE_PHOTO_TTL = 6 * 3600

FALLBACK_RATES = {
    "HKD": 1.0, "USD": 0.128, "EUR": 0.118, "GBP": 0.101, "JPY": 19.4,
    "CNY": 0.92, "TWD": 4.1, "SGD": 0.172, "THB": 4.5, "KRW": 176.0,
    "AUD": 0.195, "CAD": 0.175, "CHF": 0.113, "NZD": 0.212, "INR": 10.7,
    "IDR": 2050.0, "MYR": 0.57, "PHP": 7.3, "VND": 3250.0, "AED": 0.47,
    "SAR": 0.48, "QAR": 0.466, "KWD": 0.039, "BHD": 0.048, "OMR": 0.049,
    "JOD": 0.091, "ILS": 0.47, "TRY": 4.4, "ZAR": 2.35, "EGP": 6.3,
    "MXN": 2.2, "BRL": 0.70, "ARS": 128.0, "CLP": 122.0, "COP": 530.0,
    "PEN": 0.48, "UYU": 5.2, "CZK": 2.95, "PLN": 0.51, "HUF": 45.0,
    "RON": 0.59, "BGN": 0.23, "SEK": 1.35, "NOK": 1.38, "DKK": 0.88,
    "ISK": 17.6, "RUB": 11.6, "UAH": 5.3,
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
        {"label": meta["name"], "place_id": f"corpus:{name}"}
        for name, meta in corpus.CITIES.items()
        if lowered in name or lowered in meta["name"].lower()
    ]
    return {"items": items[:8]}


@router.get("/api/images/place")
async def place_image(request: Request, name: str = "", q: str = "", city: str = "") -> Response:
    """Resolve a place picture from Wikipedia, with a city-aware search and a soft fallback.

    Exact-title lookup fails for disambiguated names (Longshan Temple, Elephant Mountain)
    and for small hotels; a search with the city resolves most of those, and a generated
    neutral tile replaces the broken-image icon when nothing is found.
    """
    title = (name or q or "").strip()
    if not title:
        raise HTTPException(status_code=404, detail="No place named.")
    key = (title.lower(), city.lower())
    cached = _PHOTO_CACHE.get(key)
    if cached and time.time() - cached[0] < 86400:
        return RedirectResponse(cached[1], status_code=302)
    settings = container(request).settings
    queries = [{"titles": title}]
    search = " ".join(part for part in (title, city) if part).strip()
    if search:
        queries.append({"generator": "search", "gsrsearch": search, "gsrlimit": 3})
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for extra in queries:
                response = await client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "prop": "pageimages",
                        "piprop": "thumbnail",
                        "pithumbsize": 900,
                        "format": "json",
                        "redirects": 1,
                        **extra,
                    },
                    headers={"User-Agent": settings.wikimedia_user_agent},
                )
                response.raise_for_status()
                pages = ((response.json().get("query") or {}).get("pages")) or {}
                ordered = sorted(pages.values(), key=lambda page: page.get("index") or 0)
                for page in ordered:
                    thumb = (page.get("thumbnail") or {}).get("source")
                    if thumb:
                        _PHOTO_CACHE[key] = (time.time(), thumb)
                        return RedirectResponse(thumb, status_code=302)
    except (httpx.HTTPError, ValueError):
        pass
    # A named business often has no Wikipedia page but does have a Places photo.
    api_key = settings.google_places_api_key
    if api_key:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.post(
                    "https://places.googleapis.com/v1/places:searchText",
                    json={"textQuery": " ".join(part for part in (title, city) if part), "maxResultCount": 1},
                    headers={
                        "X-Goog-Api-Key": api_key,
                        "X-Goog-FieldMask": "places.photos",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                places = response.json().get("places") or []
                photos = (places[0].get("photos") if places else None) or []
                if photos:
                    photo_name = photos[0].get("name")
                    if photo_name:
                        if GOOGLE_PHOTO_NAME.fullmatch(str(photo_name)):
                            # The key stays on the server: send the browser to our own picture route.
                            url = google_photo_url(str(photo_name), 1200)
                            _PHOTO_CACHE[key] = (time.time(), url)
                            return RedirectResponse(url, status_code=302)
        except (httpx.HTTPError, ValueError, IndexError):
            pass
    initials = "".join(word[0] for word in title.split()[:2]).upper() or "?"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="560" viewBox="0 0 900 560">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#e8f3f5"/><stop offset="1" stop-color="#f6efe6"/></linearGradient></defs>'
        '<rect width="900" height="560" fill="url(#g)"/>'
        f'<text x="60" y="300" font-family="system-ui,sans-serif" font-size="120" fill="#0b5c66" opacity="0.35">{html.escape(initials)}</text>'
        f'<text x="60" y="380" font-family="system-ui,sans-serif" font-size="30" fill="#3d4b55">{html.escape(title[:60])}</text>'
        '<text x="60" y="430" font-family="system-ui,sans-serif" font-size="22" fill="#6b7a86">No photo found for this place yet.</text>'
        "</svg>"
    )
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


def _google_photo_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))


def _is_google_cdn(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "googleusercontent.com" or host.endswith(".googleusercontent.com"))


@router.get("/api/images/google")
async def google_image(request: Request, photo: str = "", w: str = "900") -> Response:
    """A Google Places photo fetched with the server's key, so the key never reaches a page.

    Not an open proxy: the host is fixed, the photo name is validated, the answer must be an
    image and is capped in size. Errors say nothing about the upstream URL.
    """
    if not GOOGLE_PHOTO_NAME.fullmatch(photo or ""):
        raise HTTPException(status_code=404, detail="No such picture.")
    api_key = container(request).settings.google_places_api_key
    if not api_key:
        raise HTTPException(status_code=404, detail="No such picture.")
    width = clamp_width(w)
    cached = _GOOGLE_PHOTO_CACHE.get((photo, width))
    if cached and time.time() - cached[0] < GOOGLE_PHOTO_TTL:
        return Response(cached[1], media_type=cached[2], headers={"Cache-Control": "public, max-age=86400"})
    try:
        async with _google_photo_client() as client:
            response = await client.get(
                f"https://places.googleapis.com/v1/{photo}/media",
                params={"maxWidthPx": width},
                headers={"X-Goog-Api-Key": api_key},
                follow_redirects=False,
            )
            if response.is_redirect:
                # Google answers with a redirect to its image host; follow it without the key.
                target = urljoin(str(response.url), response.headers.get("location", ""))
                if not _is_google_cdn(target):
                    raise HTTPException(status_code=502, detail="The picture is unavailable.")
                response = await client.get(target, follow_redirects=False)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            data = response.content
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=502, detail="The picture is unavailable.") from None
    if not content_type.startswith("image/") or content_type == "image/svg+xml":
        raise HTTPException(status_code=502, detail="The picture is unavailable.")
    if not data or len(data) > GOOGLE_PHOTO_MAX_BYTES:
        raise HTTPException(status_code=502, detail="The picture is unavailable.")
    if len(_GOOGLE_PHOTO_CACHE) >= GOOGLE_PHOTO_CACHE_MAX:
        _GOOGLE_PHOTO_CACHE.pop(next(iter(_GOOGLE_PHOTO_CACHE)), None)
    _GOOGLE_PHOTO_CACHE[(photo, width)] = (time.time(), data, content_type)
    return Response(data, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/handbook")
async def handbook(request: Request, destination: str = "", q: str = "", limit: int = 3) -> dict[str, Any]:
    c = container(request)
    city = (destination or "").strip()
    meta = corpus.city_meta(city) or {}
    key = corpus._key(city)
    chunks: list[dict[str, Any]] = []
    if key:
        for index, item in enumerate([*corpus.attractions(key), *corpus.hotels(key), *corpus.meals(key)]):
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
                    "corpus_version": "place-corpus-1",
                    "retrieval": {"mode": "place_corpus", "reranked": False},
                }
            )
    note = "Curated destination notes." if chunks else "No handbook notes for that destination yet."
    return {"items": chunks[: max(1, min(limit, 8))], "note": note, "retrieval": {"mode": "place_corpus", "reranked": False}}


@router.get("/api/wallet")
async def wallet(request: Request, base: str = "HKD") -> dict[str, Any]:
    code = normalise_currency(base)
    if not code:
        raise HTTPException(status_code=404, detail="That currency is not supported.")
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
    saved_rows = await c.store.list_wishlist(identity) if identity else []
    # Trending is earned by a positive verdict and a 1–5 star rating. Pending or
    # "not useful" saved trips stay in My Trips but never enter this pool.
    rows = [
        row for row in saved_rows
        if row.get("useful") is not None
        and bool(row.get("useful"))
        and isinstance(row.get("stars"), int)
        and 1 <= int(row["stars"]) <= 5
    ]
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = (row.get("destination") or "").strip()
        if not name:
            continue
        if destination and destination.lower() not in name.lower():
            continue
        entry = grouped.setdefault(
            name,
            {"destination": name, "plans": 0, "likes": 0, "average_stars": None, "days": 0, "year": None, "places": [], "summary": ""},
        )
        entry["plans"] += 1
        stars = int(row.get("stars") or 0)
        entry["likes"] += stars
        # The average is computed on the server from the same useful+starred
        # rows that feed the rail; the client never divides a partial list.
        entry.setdefault("_stars_total", 0)
        entry["_stars_total"] += stars
        try:
            body = __import__("json").loads(row.get("body") or "{}")
        except ValueError:
            body = {}
        entry["days"] = max(entry["days"], len(body.get("days") or []))
        entry["places"] = (entry["places"] + [item.get("name") for day in (body.get("days") or []) for item in day.get("items", [])])[:6]
        entry["summary"] = entry["summary"] or (body.get("summary") or "")[:200]
    items = []
    for entry in grouped.values():
        total = int(entry.pop("_stars_total", 0))
        entry["average_stars"] = round(total / entry["plans"], 1) if entry["plans"] else None
        items.append(entry)
    if q:
        items = [item for item in items if q.lower() in item["destination"].lower()]
    # Only show a place-corpus idea when this traveller has no saved trips at all.
    # A not-useful trip must not come back as a generic "trending" destination.
    if not items and q and not saved_rows:
        meta = corpus.city_meta(q)
        if meta:
            items = [
                {
                    "destination": meta["name"],
                    "plans": 0,
                    "likes": 0,
                    "days": 0,
                    "year": None,
                    "places": [item.name for item in corpus.attractions(corpus._key(q))][:5],
                    "summary": "Popular places from the Pathfinder curated place corpus.",
                    "match": [q],
                }
            ]
    dropped = [] if items else [{"destination": destination or q, "reason": "no useful starred trips yet"}]
    return {
        "items": items[: max(1, limit)],
        "trace": {
            "note": (
                "Built from the traveller's useful, starred trips."
                if rows
                else ("No useful starred trips yet." if saved_rows else "No saved trips yet; showing curated place-corpus ideas.")
            ),
            "filter": "personal" if identity else "none",
            "dropped": dropped,
            "mode": "wishlist" if rows else ("filtered" if saved_rows else "place_corpus"),
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
