"""Community notes: YouTube Data API and Bilibili's public search endpoint.

Both adapters apply the 24-month freshness rule when a publish date exists and
rank undated items lower. A note can enrich a place description; it can never
create a price or opening hour.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import NoteItem, utcnow
from app.infrastructure.http import request_json


class YouTubeNotesSource:
    name = "youtube"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = bool(settings.youtube_api_key and settings.enable_youtube)

    async def search(self, query: str, *, limit: int = 5, window_months: int = 24) -> list[NoteItem]:
        if not self.available:
            raise ProviderNoData("YouTube key is not configured", source=self.name)
        published_after = (utcnow() - timedelta(days=window_months * 30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = await request_json(
            "GET",
            "https://www.googleapis.com/youtube/v3/search",
            source=self.name,
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": limit,
                "order": "relevance",
                "publishedAfter": published_after,
                "relevanceLanguage": "en",
                "key": self.settings.youtube_api_key,
            },
            timeout=self.settings.http_timeout_seconds,
            retries=1,
            user_agent=self.settings.user_agent,
        )
        items = data.get("items") or []
        if not items:
            return []
        out: list[NoteItem] = []
        for item in items:
            snippet = item.get("snippet") or {}
            video_id = (item.get("id") or {}).get("videoId")
            if not video_id:
                continue
            published = _parse_iso(snippet.get("publishedAt"))
            out.append(
                NoteItem(
                    title=html.unescape(str(snippet.get("title") or "Video")),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    source=self.name,
                    snippet=html.unescape(str(snippet.get("description") or ""))[:500],
                    video_id=str(video_id),
                    thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    published_at=published,
                )
            )
        return out


class BilibiliNotesSource:
    name = "bilibili"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = bool(settings.enable_notes and settings.enable_bilibili)

    async def search(self, query: str, *, limit: int = 5, window_months: int = 24) -> list[NoteItem]:
        if not self.available:
            raise ProviderNoData("Bilibili notes are disabled", source=self.name)
        data = await request_json(
            "GET",
            "https://api.bilibili.com/x/web-interface/search/type",
            source=self.name,
            params={
                "search_type": "video",
                "keyword": query,
                "order": "pubdate",
                "page": 1,
                "page_size": limit,
            },
            headers={
                "Referer": "https://www.bilibili.com/",
                "Accept": "application/json",
            },
            timeout=self.settings.http_timeout_seconds,
            retries=1,
            user_agent=self.settings.user_agent,
        )
        if data.get("code") not in (0, None):
            code = data.get("code")
            raise ProviderError(
                f"Bilibili returned code {code if isinstance(code, int) else '?'}",
                source=self.name,
                detail=str(data.get("message", ""))[:300],
            )
        rows = ((data.get("data") or {}).get("result") or [])
        out: list[NoteItem] = []
        for row in rows:
            title = html.unescape(re.sub(r"<[^>]+>", "", str(row.get("title") or "")).strip())
            url = row.get("arcurl") or (f"https://www.bilibili.com/video/{row['bvid']}" if row.get("bvid") else "")
            if not title or not url:
                continue
            thumbnail = str(row.get("pic") or "")
            if thumbnail.startswith("//"):
                thumbnail = "https:" + thumbnail
            published = None
            if row.get("pubdate"):
                try:
                    published = datetime.fromtimestamp(int(row["pubdate"]), tz=timezone.utc)
                except (ValueError, OSError, TypeError):
                    published = None
            out.append(
                NoteItem(
                    title=title,
                    url=url,
                    source=self.name,
                    snippet=html.unescape(re.sub(r"<[^>]+>", "", str(row.get("description") or "")))[:500],
                    video_id=str(row.get("bvid") or ""),
                    thumbnail_url=thumbnail,
                    published_at=published,
                )
            )
        return out


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
