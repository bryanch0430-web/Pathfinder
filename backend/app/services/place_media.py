"""Free, credited place photos from Wikipedia and Wikimedia Commons.

One Wikipedia search per place returns candidates with coordinates and a *free-licensed*
lead image. A candidate is accepted only when it sits near the stop, so "Central Kyoto"
can never borrow a photo from a namesake elsewhere. Credits come from one batched
Commons lookup. Results live in SQLite: hits forever, misses for a week.
"""

import asyncio
import json
import logging
import re
import sqlite3
import time
from html import unescape
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import httpx

from app.schemas.places import PlaceMediaItem, PlaceMediaRequest

logger = logging.getLogger(__name__)
CACHE_DB_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "place_media.sqlite3"
WIKI_API = "https://en.wikipedia.org/w/api.php"
# Wikimedia's API policy asks every client to identify itself.
HEADERS = {"User-Agent": "Pathfinder/0.1 (https://github.com/watermelon588/Tripverse; trip planner portfolio project) httpx"}
# ponytail: one radius for cities and landmarks; tighten per kind if city photos leak onto landmarks.
MAX_DISTANCE_KM = 20
MISS_TTL_SECONDS = 7 * 24 * 3600
THUMB_WIDTH = 800
_db_lock = asyncio.Lock()


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    arc = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(arc))


def _key(place: PlaceMediaItem) -> str:
    where = f"{place.lat:.2f},{place.lon:.2f}" if place.lat is not None and place.lon is not None else "-"
    return f"{place.name.strip().casefold()}|{where}"


def _text(html: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", html or ""))).strip()


def _connect() -> sqlite3.Connection:
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(CACHE_DB_PATH, timeout=3)
    connection.execute("CREATE TABLE IF NOT EXISTS place_media (key TEXT PRIMARY KEY, media TEXT, saved REAL NOT NULL)")
    return connection


def _cache_get(keys: list[str]) -> dict[str, dict | None]:
    connection = _connect()
    try:
        rows = connection.execute(
            f"SELECT key, media, saved FROM place_media WHERE key IN ({','.join('?' * len(keys))})", keys,
        ).fetchall()
    finally:
        connection.close()
    now = time.time()
    return {key: json.loads(media) if media else None for key, media, saved in rows
            if media or now - saved < MISS_TTL_SECONDS}


def _cache_put(entries: dict[str, dict | None]) -> None:
    connection = _connect()
    try:
        with connection:
            connection.executemany(
                "INSERT OR REPLACE INTO place_media (key, media, saved) VALUES (?, ?, ?)",
                [(key, json.dumps(media) if media else None, time.time()) for key, media in entries.items()],
            )
    finally:
        connection.close()


def pick_candidate(place: PlaceMediaItem, pages: list[dict]) -> dict | None:
    """Best search result that has a free photo, isn't a disambiguation page, and is near the stop."""
    for page in sorted(pages, key=lambda item: item.get("index", 99)):
        if "thumbnail" not in page or "disambiguation" in page.get("pageprops", {}):
            continue
        if place.lat is not None and place.lon is not None:
            spot = (page.get("coordinates") or [{}])[0]
            if "lat" not in spot or _km(place.lat, place.lon, spot["lat"], spot["lon"]) > MAX_DISTANCE_KM:
                continue
        return page
    return None


async def _search(client: httpx.AsyncClient, place: PlaceMediaItem) -> dict | None:
    response = await client.get(WIKI_API, params={
        "action": "query", "format": "json", "formatversion": 2, "redirects": 1,
        "generator": "search", "gsrsearch": place.name, "gsrlimit": 6, "gsrnamespace": 0,
        "prop": "pageimages|coordinates|description|extracts|pageprops|info",
        "piprop": "thumbnail|name", "pithumbsize": THUMB_WIDTH, "pilicense": "free",
        "exintro": 1, "explaintext": 1, "exsentences": 2, "exlimit": "max",
        "ppprop": "disambiguation", "inprop": "url",
    })
    response.raise_for_status()
    page = pick_candidate(place, response.json().get("query", {}).get("pages", []))
    if not page:
        return None
    thumb = page["thumbnail"]
    return {
        "title": page["title"], "description": page.get("description"),
        "extract": (page.get("extract") or "").strip() or None, "article_url": page.get("fullurl"),
        "image": thumb["source"], "width": thumb.get("width"), "height": thumb.get("height"),
        "file": page.get("pageimage"),
    }


async def _credits(client: httpx.AsyncClient, files: list[str], thumb_width: int | None = None) -> dict[str, dict]:
    """Author and license per Commons file, in one request. Non-free files are dropped.
    With `thumb_width`, each credit also carries a `thumb` URL of that width."""
    if not files:
        return {}
    response = await client.get(WIKI_API, params={
        "action": "query", "format": "json", "formatversion": 2,
        "titles": "|".join(f"File:{name}" for name in files[:50]),
        "prop": "imageinfo", "iiprop": "extmetadata|url",
        "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|NonFree",
        **({"iiurlwidth": thumb_width} if thumb_width else {}),
    })
    response.raise_for_status()
    found = {}
    for page in response.json().get("query", {}).get("pages", []):
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        if meta.get("NonFree", {}).get("value") == "true":
            continue
        found[page["title"].removeprefix("File:").replace("_", " ")] = {
            "author": _text(meta.get("Artist", {}).get("value")) or None,
            "license": meta.get("LicenseShortName", {}).get("value"),
            "license_url": meta.get("LicenseUrl", {}).get("value"),
            "file_page": info.get("descriptionurl"),
            **({"thumb": info.get("thumburl")} if thumb_width else {}),
        }
    return found


async def get_place_media(request: PlaceMediaRequest) -> dict:
    keys = {place.id: _key(place) for place in request.places}
    async with _db_lock:
        cached = _cache_get(list(set(keys.values())))
    missing = [place for place in request.places if keys[place.id] not in cached]
    fresh: dict[str, dict | None] = {}
    if missing:
        limit = asyncio.Semaphore(4)
        async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
            async def lookup(place: PlaceMediaItem):
                async with limit:
                    try:
                        return place, await _search(client, place), True
                    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                        logger.info("Place media unavailable for %s: %s", place.name, exc)
                        return place, None, False  # a failed call is retried next time, not cached as a miss
            results = await asyncio.gather(*(lookup(place) for place in missing))
            files = sorted({media["file"].replace("_", " ") for _, media, _ in results if media and media.get("file")})
            try:
                credits = await _credits(client, files)
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                logger.info("Photo credits unavailable: %s", exc)
                credits = None
        for place, media, succeeded in results:
            if not succeeded or (media and credits is None):
                continue
            credit = credits.get((media or {}).get("file", "").replace("_", " ")) if media else None
            # No credit means non-free or unknown license: show nothing rather than an uncredited photo.
            fresh[keys[place.id]] = {**media, "credit": credit} if media and credit else None
        if fresh:
            async with _db_lock:
                _cache_put(fresh)
    merged = {**cached, **fresh}
    return {"provider": "wikipedia", "places": [
        {"id": place.id, **merged[keys[place.id]]} for place in request.places if merged.get(keys[place.id])
    ]}
