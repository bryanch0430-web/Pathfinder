"""Pictures for places and stays, from what a source returned.

- Google Places: a photo's resource name becomes ``/api/images/place?ref=<name>``. The plan stores only that route
  address; the route adds the server's key, fetches the bytes, keeps them on disk and serves them. The key never
  reaches a plan, a response or a log line.
- Wikimedia: a page's lead image, looked up by the Wikipedia title a place source gave (OpenStreetMap's ``wikipedia``
  tag, a Wikivoyage listing's ``wikipedia`` field). The author and licence come from the file's own metadata.
- SerpApi hotels: the thumbnail address in the property's ``images`` list.

A place a source gave no picture for has none; nothing is looked up by name or guessed.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.domain.models import ImageRef
from app.infrastructure.http import current_transport, request_json

logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO, and a photo request carries the key in its URL.
logging.getLogger("httpx").setLevel(logging.WARNING)

#: A Places (New) photo resource name: ``places/<place id>/photos/<photo id>``. Nothing else is ever fetched.
PHOTO_REF = re.compile(r"^places/[A-Za-z0-9_-]+/photos/[A-Za-z0-9_-]+$")
IMAGE_ROUTE = "/api/images/place"
#: Width asked of Google; the page shows pictures at card size.
PHOTO_WIDTH_PX = 800
MAX_PHOTO_BYTES = 5_000_000
CACHE_SECONDS = 7 * 24 * 3600
WIKIMEDIA_THUMB_PX = 640
WIKIMEDIA_API = "https://en.wikipedia.org/w/api.php"
_TAGS = re.compile(r"<[^>]+>")


class PhotoUnavailable(Exception):
    """The photo could not be fetched; carries no text, so nothing about the request (the key) can leak."""


def google_image(photos: Any) -> ImageRef | None:
    """The first usable Google photo of a place as our route address with its author, else None."""
    if not isinstance(photos, list):
        return None
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        name = photo.get("name")
        if not isinstance(name, str) or not PHOTO_REF.fullmatch(name):
            continue
        authors = photo.get("authorAttributions") or []
        author = ""
        if isinstance(authors, list) and authors and isinstance(authors[0], dict):
            author = str(authors[0].get("displayName") or "").strip()
        return ImageRef(
            url=f"{IMAGE_ROUTE}?ref={quote(name, safe='/')}",
            source="google",
            attribution=author,
        )
    return None


def serpapi_image(row: dict) -> ImageRef | None:
    """A hotel row's first thumbnail when it is an https address."""
    images = row.get("images")
    if not isinstance(images, list):
        return None
    for entry in images:
        thumbnail = entry.get("thumbnail") if isinstance(entry, dict) else None
        if isinstance(thumbnail, str) and thumbnail.lower().startswith("https://"):
            return ImageRef(url=thumbnail.strip(), source="serpapi")
    return None


# --- Google photo bytes ------------------------------------------------------------------------------------------


def _cache_file(settings: Settings, ref: str) -> Path:
    return (
        settings.data_dir
        / "image_cache"
        / f"{hashlib.sha256(ref.encode('utf-8')).hexdigest()}.bin"
    )


def _read_cache(path: Path) -> tuple[bytes, str] | None:
    meta = path.with_suffix(".type")
    try:
        return path.read_bytes(), meta.read_text(
            encoding="utf-8"
        ).strip() or "image/jpeg"
    except OSError:
        return None


def _write_cache(path: Path, data: bytes, content_type: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.with_suffix(".type").write_text(content_type, encoding="utf-8")
    except OSError:
        logger.warning("could not cache a place photo")


async def fetch_place_photo(ref: str, settings: Settings) -> tuple[bytes, str]:
    """The bytes and content type of a Google photo: from the disk cache, else fetched once with the server's key.

    Raises ``PhotoUnavailable`` (without any text of the request) when there is no key, the ref is not a photo
    resource name, Google refuses, or what comes back is not an image."""
    if not PHOTO_REF.fullmatch(ref) or not settings.google_places_api_key:
        raise PhotoUnavailable
    cache = _cache_file(settings, ref)
    cached = await asyncio.to_thread(_read_cache, cache)
    if cached is not None:
        return cached
    base = (settings.google_places_proxy or "https://places.googleapis.com").rstrip("/")
    try:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout_seconds,
            follow_redirects=True,
            transport=current_transport(),
        ) as client:
            response = await client.get(
                f"{base}/v1/{ref}/media",
                params={
                    "maxWidthPx": PHOTO_WIDTH_PX,
                    "key": settings.google_places_api_key,
                },
                headers={"User-Agent": settings.user_agent},
            )
    except Exception as exc:  # noqa: BLE001 - the text of an httpx error holds the URL, key included
        logger.warning("place photo fetch failed: %s", type(exc).__name__)
        raise PhotoUnavailable from None
    content_type = (
        response.headers.get("content-type", "").split(";")[0].strip().lower()
    )
    if (
        response.status_code != 200
        or not content_type.startswith("image/")
        or len(response.content) > MAX_PHOTO_BYTES
    ):
        logger.warning("place photo fetch refused: HTTP %s", response.status_code)
        raise PhotoUnavailable
    await asyncio.to_thread(_write_cache, cache, response.content, content_type)
    return response.content, content_type


# --- Wikimedia ---------------------------------------------------------------------------------------------------------


def _plain(value: str) -> str:
    return html.unescape(_TAGS.sub("", value)).strip()


async def wikimedia_images(
    titles: list[str], settings: Settings
) -> dict[str, ImageRef]:
    """Page images for English Wikipedia titles, keyed by the title as given; titles with no free image are left out.

    Never raises: a failed lookup is no picture."""
    wanted = [title for title in dict.fromkeys(t.strip() for t in titles) if title][:20]
    if not wanted:
        return {}
    common = {"action": "query", "format": "json", "formatversion": 2, "redirects": 1}
    try:
        data = await request_json(
            "GET",
            WIKIMEDIA_API,
            source="wikimedia",
            params={
                **common,
                "prop": "pageimages",
                "piprop": "thumbnail|name",
                "pithumbsize": WIKIMEDIA_THUMB_PX,
                "titles": "|".join(wanted),
            },
            timeout=settings.http_timeout_seconds,
            retries=0,
            user_agent=settings.wikimedia_user_agent,
        )
        query = data.get("query") or {}
        aliases: dict[str, str] = {}
        for key in ("normalized", "redirects"):
            for move in query.get(key) or []:
                if isinstance(move, dict) and move.get("from") and move.get("to"):
                    aliases[str(move["to"])] = aliases.get(
                        str(move["from"]), str(move["from"])
                    )
        found: dict[str, tuple[str, str]] = {}
        for page in query.get("pages") or []:
            thumbnail = (
                (page.get("thumbnail") or {}).get("source")
                if isinstance(page, dict)
                else None
            )
            file_name = page.get("pageimage") if isinstance(page, dict) else None
            if (
                isinstance(thumbnail, str)
                and thumbnail.startswith("https://")
                and file_name
            ):
                title = str(page.get("title") or "")
                found[aliases.get(title, title)] = (thumbnail, str(file_name))
        if not found:
            return {}
        credits = await _credits(
            sorted({name for _, name in found.values()}), settings, common
        )
    except Exception as exc:  # noqa: BLE001 - a failed lookup is no picture
        logger.info("wikimedia image lookup failed: %s", type(exc).__name__)
        return {}
    given = {title.replace("_", " "): title for title in wanted}
    out: dict[str, ImageRef] = {}
    for title, (thumbnail, file_name) in found.items():
        original = given.get(title.replace("_", " "))
        if original is not None:
            out[original] = ImageRef(
                url=thumbnail,
                source="wikimedia",
                attribution=credits.get(file_name, ""),
            )
    return out


async def _credits(
    files: list[str], settings: Settings, common: dict
) -> dict[str, str]:
    """Author and licence for each file name, as "Author, Licence"; a file whose metadata is missing has none."""
    data = await request_json(
        "GET",
        WIKIMEDIA_API,
        source="wikimedia",
        params={
            **common,
            "prop": "imageinfo",
            "iiprop": "extmetadata",
            "iiextmetadatafilter": "Artist|LicenseShortName",
            "titles": "|".join(f"File:{name}" for name in files),
        },
        timeout=settings.http_timeout_seconds,
        retries=0,
        user_agent=settings.wikimedia_user_agent,
    )
    out: dict[str, str] = {}
    for page in (data.get("query") or {}).get("pages") or []:
        info = (page.get("imageinfo") or [{}])[0] if isinstance(page, dict) else {}
        meta = info.get("extmetadata") or {}
        author = _plain(str((meta.get("Artist") or {}).get("value") or ""))
        licence = _plain(str((meta.get("LicenseShortName") or {}).get("value") or ""))
        name = str(page.get("title") or "").removeprefix("File:").replace(" ", "_")
        out[name] = ", ".join(part for part in (author, licence) if part)
    return {name: text for name, text in out.items() if text}
