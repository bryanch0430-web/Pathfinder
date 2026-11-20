"""What's around a stop: Wikivoyage listings (see, eat, stay…) plus Wikipedia landmarks and stations.

A new area costs up to six requests (two geosearches, one batch each of Wikivoyage wikitext,
Wikidata sitelinks, article lead photos and Commons credits) and is then cached for 30 days per ~1 km cell. No keys, no LLM calls, no ratings:
only what the sources actually say, each item credited to where it came from.
"""

import asyncio
import json
import logging
import re
import sqlite3
import time
from html import unescape
from urllib.parse import quote

import httpx

from app.services.place_media import CACHE_DB_PATH, HEADERS, WIKI_API, _credits, _km

logger = logging.getLogger(__name__)
WIKIVOYAGE_API = "https://en.wikivoyage.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
LISTING_KM = 3  # a listing further out belongs to the next neighbourhood
LANDMARK_RADIUS_M = 2000
PAGES = 3  # nearest Wikivoyage articles to read; a city page often keeps its listings in districts
PER_TAB = 12
TTL_SECONDS = 30 * 24 * 3600
THUMB_WIDTH = 480
TABS = {"see": "see", "do": "see", "buy": "see", "eat": "food", "drink": "food", "sleep": "stay"}
TRANSIT = re.compile(r"\b(station|airport|terminal|interchange|bus stop|ferry pier)\b", re.I)
# Geosearch also returns events and electoral areas pinned to a spot; they aren't places to visit.
NOT_A_PLACE = re.compile(r"constituency|electoral|election|olympics|championship|tournament|\bat the \d{4}\b", re.I)
LISTING = re.compile(r"\{\{\s*(see|do|eat|drink|sleep|buy|listing)\s*\|", re.I)
_db_lock = asyncio.Lock()


def _clean(value: str) -> str:
    value = re.sub(r"<ref[^>]*/>|<ref.*?</ref>|<[^>]+>", "", value, flags=re.S)
    value = re.sub(r"\[https?://\S+\s+([^\]]+)\]|\[https?://\S+\]", r"\1", value)
    value = value.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _first_sentence(text: str, limit: int = 180) -> str | None:
    """A short excerpt, not the listing: CC BY-SA text is quoted briefly and credited."""
    if not text:
        return None
    sentence = (re.match(r"(.+?[.!?])(?:\s|$)", text) or [None, text])[1]
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rsplit(" ", 1)[0] + "…"


def parse_listings(wikitext: str) -> list[dict]:
    """Every {{see}}, {{eat}}, {{listing|type=…}} … template in an article, as plain fields."""
    found = []
    for match in LISTING.finditer(wikitext):
        depth, i = 1, match.end()
        while depth and i < len(wikitext):
            pair = wikitext[i:i + 2]
            depth += 1 if pair == "{{" else -1 if pair == "}}" else 0
            i += 2 if pair in ("{{", "}}") else 1
        body = wikitext[match.end():i - 2]
        while re.search(r"\{\{[^{}]*\}\}", body):  # nested templates carry no listing fields
            body = re.sub(r"\{\{[^{}]*\}\}", "", body)
        body = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", body)
        fields = {}
        for part in body.split("|"):
            key, _, value = part.partition("=")
            if _ and value.strip():
                fields[key.strip().lower()] = _clean(value)
        kind = (fields.get("type", "") if match.group(1).lower() == "listing" else match.group(1)).lower()
        if fields.get("name") and kind in TABS:
            found.append({"kind": kind, **fields})
    return found


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _listing_item(listing: dict, page: dict, lat: float, lon: float) -> dict | None:
    spot = (_float(listing.get("lat")), _float(listing.get("long")))
    distance = _km(lat, lon, *spot) if None not in spot else None
    # ponytail: listings without coordinates are kept only from an article centred near the stop.
    if (distance if distance is not None else page["dist_km"]) > LISTING_KM:
        return None
    name = listing["name"]
    return {
        "id": listing.get("wikidata") or f"wv:{name.casefold()}",
        "name": name, "alt": listing.get("alt"), "kind": listing["kind"], "tab": TABS[listing["kind"]],
        "blurb": _first_sentence(listing.get("content", "")),
        "price": listing.get("price"), "hours": listing.get("hours"), "address": listing.get("address"),
        "website": listing.get("url") if (listing.get("url") or "").startswith("http") else None,
        "lat": spot[0], "lon": spot[1], "distance_km": round(distance, 2) if distance is not None else None,
        "file": listing.get("image"), "article": listing.get("wikipedia"),
        "source": "Wikivoyage", "source_url": page["url"],
        "wikipedia_url": f"https://en.wikipedia.org/wiki/{quote(listing['wikipedia'].replace(' ', '_'))}" if listing.get("wikipedia") else None,
    }


def _landmark_item(page: dict, lat: float, lon: float) -> dict | None:
    spot = (page.get("coordinates") or [{}])[0]
    if "lat" not in spot or "disambiguation" in page.get("pageprops", {}) \
            or NOT_A_PLACE.search(f"{page['title']} {page.get('description', '')}"):
        return None
    transit = bool(TRANSIT.search(f"{page['title']} {page.get('description', '')}"))
    return {
        "id": page.get("pageprops", {}).get("wikibase_item") or f"wp:{page['title'].casefold()}",
        "name": page["title"], "alt": None, "kind": "transit" if transit else "landmark", "tab": "landmarks",
        "blurb": page.get("description"), "price": None, "hours": None, "address": None, "website": None,
        "lat": spot["lat"], "lon": spot["lon"], "distance_km": round(_km(lat, lon, spot["lat"], spot["lon"]), 2),
        "file": page.get("pageimage"), "article": None,
        "source": "Wikipedia", "source_url": page.get("fullurl"), "wikipedia_url": page.get("fullurl"),
    }


def merge(listings: list[dict], landmarks: list[dict]) -> list[dict]:
    """One entry per place: a Wikivoyage listing wins (it has hours and prices), and borrows the
    landmark's photo when it has none. Each tab keeps its nearest PER_TAB entries."""
    by_id: dict[str, dict] = {}
    for item in listings + landmarks:
        seen = by_id.get(item["id"]) or by_id.get(f"name:{item['name'].casefold()}")
        if seen:
            seen["file"] = seen["file"] or item["file"]
            seen["wikipedia_url"] = seen["wikipedia_url"] or item["wikipedia_url"]
            continue
        by_id[item["id"]] = by_id[f"name:{item['name'].casefold()}"] = item
    unique = list({id(item): item for item in by_id.values()}.values())
    unique.sort(key=lambda item: (item["distance_km"] is None, item["distance_km"] or 0))
    kept, counts = [], {}
    for item in unique:
        counts[item["tab"]] = counts.get(item["tab"], 0) + 1
        if counts[item["tab"]] <= PER_TAB:
            kept.append(item)
    return kept


def _connect() -> sqlite3.Connection:
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(CACHE_DB_PATH, timeout=3)
    connection.execute("CREATE TABLE IF NOT EXISTS around_here (key TEXT PRIMARY KEY, data TEXT NOT NULL, saved REAL NOT NULL)")
    return connection


def _cache(key: str, data: dict | None = None) -> dict | None:
    connection = _connect()
    try:
        with connection:
            if data is not None:
                connection.execute("INSERT OR REPLACE INTO around_here VALUES (?, ?, ?)", (key, json.dumps(data), time.time()))
                return data
            row = connection.execute("SELECT data, saved FROM around_here WHERE key = ?", (key,)).fetchone()
    finally:
        connection.close()
    return json.loads(row[0]) if row and time.time() - row[1] < TTL_SECONDS else None


async def _fetch(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    async def get(url: str, **params) -> dict:
        response = await client.get(url, params={"action": "query", "format": "json", "formatversion": 2, **params})
        response.raise_for_status()
        return response.json().get("query", {})

    voyage, wiki = await asyncio.gather(
        get(WIKIVOYAGE_API, list="geosearch", gscoord=f"{lat}|{lon}", gsradius=10000, gslimit=PAGES),
        get(WIKI_API, generator="geosearch", ggscoord=f"{lat}|{lon}", ggsradius=LANDMARK_RADIUS_M, ggslimit=30,
            prop="pageimages|coordinates|description|pageprops|info", piprop="name", pilicense="free",
            ppprop="wikibase_item|disambiguation", inprop="url"),
    )
    pages = [{"title": hit["title"], "dist_km": hit["dist"] / 1000,
              "url": f"https://en.wikivoyage.org/wiki/{quote(hit['title'].replace(' ', '_'))}"} for hit in voyage.get("geosearch", [])]
    texts = {}
    if pages:
        content = await get(WIKIVOYAGE_API, prop="revisions", rvprop="content", rvslots="main",
                            titles="|".join(page["title"] for page in pages))
        texts = {page["title"]: page["revisions"][0]["slots"]["main"]["content"]
                 for page in content.get("pages", []) if page.get("revisions")}
    listings = [item for page in pages for listing in parse_listings(texts.get(page["title"], ""))
                if (item := _listing_item(listing, page, lat, lon))]
    landmarks = [item for page in wiki.get("pages", []) if (item := _landmark_item(page, lat, lon))]
    items = merge(listings, landmarks)
    # Listings without an image borrow a free lead photo from a Wikipedia article: the one they name,
    # the one their Wikidata item links to, or one titled like them that sits within 1 km.
    ids = sorted({item["id"] for item in items if not item["file"] and not item["article"] and re.fullmatch(r"Q\d+", item["id"])})[:50]
    if ids:
        response = await client.get(WIKIDATA_API, params={
            "action": "wbgetentities", "format": "json", "ids": "|".join(ids), "props": "sitelinks", "sitefilter": "enwiki"})
        response.raise_for_status()
        links = {qid: entity.get("sitelinks", {}).get("enwiki", {}).get("title") for qid, entity in response.json().get("entities", {}).items()}
        for item in items:
            item["article"] = item["article"] or links.get(item["id"])
    wanting = [item for item in items if not item["file"] and (item["article"] or item["lat"] is not None)]
    titles = sorted({item["article"] or item["name"] for item in wanting})[:50]
    if titles:
        lead = await get(WIKI_API, titles="|".join(titles), redirects=1, prop="pageimages|coordinates", piprop="name", pilicense="free")
        back = {hop["to"]: hop["from"] for hop in lead.get("normalized", []) + lead.get("redirects", [])}
        found = {}
        for page in lead.get("pages", []):
            title = page["title"]
            while title in back:  # undo redirect, then normalization, to get the title we asked for
                title = back[title]
            found[title] = page
        for item in wanting:
            page = found.get(item["article"] or item["name"], {})
            spot = (page.get("coordinates") or [{}])[0]
            near = "lat" in spot and item["lat"] is not None and _km(item["lat"], item["lon"], spot["lat"], spot["lon"]) <= 1
            if page.get("pageimage") and (item["article"] or near):
                item["file"] = page["pageimage"]
    credits = await _credits(client, sorted({item["file"].replace("_", " ") for item in items if item["file"]}), THUMB_WIDTH)
    for item in items:
        item.pop("article")
        credit = credits.get((item.pop("file") or "").replace("_", " "))
        # No credit means a non-free or unknown license: the item stays, the photo doesn't.
        item["image"] = credit.pop("thumb", None) if credit else None
        item["image_credit"] = credit if item["image"] else None
    used = {item["source_url"] for item in items}
    return {"items": items, "guides": [{"title": page["title"], "url": page["url"]} for page in pages if page["url"] in used]}


async def get_around_here(lat: float, lon: float) -> dict:
    key = f"{lat:.2f},{lon:.2f}"
    async with _db_lock:
        cached = _cache(key)
    if cached:
        return {**cached, "available": True}
    try:
        async with httpx.AsyncClient(timeout=12.0, headers=HEADERS) as client:
            # Looked up from the cell's centre, so every stop in the cell shares one answer.
            data = await _fetch(client, round(lat, 2), round(lon, 2))
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        logger.info("Around-here lookup failed for %s: %s", key, exc)
        return {"items": [], "guides": [], "available": False}  # not cached: retried next time
    async with _db_lock:
        _cache(key, data)
    return {**data, "available": True}
