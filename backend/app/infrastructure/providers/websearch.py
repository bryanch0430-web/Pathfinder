"""Web search sources.

Two very different animals, so they are two sources rather than one chain:

* ``DeepSeekSearchSource`` — a raw JSON search API. Sub-second, $0.75 per 1,000
  successful requests, returns title/url/summary/authority per result. This is
  the default for an interactive answer, because a traveller typing a question
  will not wait twenty seconds.
* ``GrokSearchSource`` — xAI's Agent Tools API. It runs a search *inside* a
  model call and returns a synthesised answer plus its source URLs, which is
  what you want for a broad "what is it like / is it worth it" question, and
  costs about twenty seconds. It is therefore never the default.

Both are read-only. Each reuses a key the project already has (DeepSeek judge
key, xAI writer key); Grok search uses the caller's own xAI key when they saved
one for search.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.application.llm_selection import LLMSelector, SearchKey, current_search_key
from app.config import Settings
from app.domain.errors import FailureKind, ProviderError, ProviderNoData
from app.infrastructure.http import request_json
from app.infrastructure.llm_providers import PROVIDERS
from app.security.scrub import scrub

UNREADABLE_SEARCH_KEY = "Your saved xAI key can no longer be read. Remove it and add it again in Settings."


@dataclass
class WebResult:
    title: str
    url: str
    summary: str = ""
    snippet: str = ""
    site_name: str = ""
    published: str = ""
    authority: int = -1
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "snippet": self.snippet,
            "site_name": self.site_name,
            "published": self.published,
            "authority": self.authority,
            "score": self.score,
        }


@dataclass
class WebAnswer:
    """A synthesised answer (Grok) as opposed to a result list (DeepSeek)."""

    text: str
    sources: list[str] = field(default_factory=list)
    source_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "sources": self.sources, "source": self.source_name}


class DeepSeekSearchSource:
    """DeepSeek's in-house search engine, opened to the API on 2026-10-17."""

    name = "deepseek-search"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = (settings.deepseek_search_base_url or "https://api.deepseek.com").rstrip("/")
        self.keys = _deepseek_keys(settings)
        self.key = self.keys[0] if self.keys else ""
        self.available = bool(self.keys)

    async def search(
        self,
        *,
        query: str,
        count: int = 5,
        ori_question: str | None = None,
        retries: int | None = None,
    ) -> list[WebResult]:
        if not self.keys:
            raise ProviderNoData("No DeepSeek search key is configured", source=self.name)
        payload: dict[str, Any] = {
            "query": query[:128],
            "count": max(1, min(count, 50)),
            "need_content": False,
        }
        if ori_question:
            # The engine takes keywords in `query` and the sentence in
            # `ori_question`; passing both is what the field is for.
            payload["ori_question"] = ori_question[:128]
        last: ProviderError | None = None
        for index, key in enumerate(self.keys):
            try:
                data = await request_json(
                    "POST",
                    f"{self.base}/v1/tools/search",
                    source=self.name,
                    headers={"Authorization": f"Bearer {key}"},
                    payload=payload,
                    timeout=20.0,
                    retries=self.settings.http_retries if retries is None else retries,
                    secrets=(key,),
                )
            except ProviderError as exc:
                # A dedicated search key can be rejected while the judge key on the same
                # account still searches. Try the next key before giving up.
                last = exc
                if exc.kind is FailureKind.AUTH and index < len(self.keys) - 1:
                    continue
                raise
            self.key = key
            return list(_deepseek_results(data))
        raise last or ProviderNoData("No DeepSeek search key is configured", source=self.name)


def _deepseek_keys(settings: Settings) -> list[str]:
    """The search key first, then the judge key when it is a different credential."""
    keys: list[str] = []
    for raw in (settings.deepseek_search_api_key, settings.judge_api_key):
        key = (raw or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def _deepseek_results(data: dict[str, Any]) -> Iterable[WebResult]:
    for row in data.get("results") or []:
        url = str(row.get("url") or "")
        if not url:
            continue
        yield WebResult(
            title=str(row.get("title") or url),
            url=url,
            summary=str(row.get("summary") or "")[:800],
            snippet=str(row.get("snippet") or "")[:300],
            site_name=str(row.get("site_name") or ""),
            published=str(row.get("date") or ""),
            authority=int(row["authority"]) if row.get("authority") is not None else -1,
            score=float(row["rerank_score"]) if row.get("rerank_score") is not None else 0.0,
        )


class SerpApiWebSource:
    """Google web results through the SerpAPI key the hotel and flight lookups already use.

    DeepSeek stays first. This source runs only when that call is refused or returns nothing.
    """

    name = "serpapi-web"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = bool((settings.serpapi_key or "").strip() or (settings.serpapi_key_pool or "").strip())

    async def search(
        self,
        *,
        query: str,
        count: int = 5,
        ori_question: str | None = None,
        retries: int | None = None,
    ) -> list[WebResult]:
        from app.infrastructure.serpapi_keys import serpapi_json

        if not self.available:
            raise ProviderNoData("SerpApi key is not configured", source=self.name)
        data = await serpapi_json(
            self.settings,
            source=self.name,
            params={
                "engine": "google",
                "q": (ori_question or query)[:200],
                "num": max(1, min(count, 10)),
                "hl": "en",
            },
            timeout=max(self.settings.http_timeout_seconds, 20),
            retries=self.settings.http_retries if retries is None else retries,
            user_agent=self.settings.user_agent,
        )
        return list(_serpapi_web_results(data, count))


def _serpapi_web_results(data: dict[str, Any], count: int) -> Iterable[WebResult]:
    for row in (data.get("organic_results") or [])[:count]:
        if not isinstance(row, dict):
            continue
        url = str(row.get("link") or "")
        if not url:
            continue
        snippet = str(row.get("snippet") or "")
        yield WebResult(
            title=str(row.get("title") or url),
            url=url,
            summary=snippet[:800],
            snippet=snippet[:300],
            site_name=str(row.get("source") or ""),
        )


class GrokSearchSource:
    """xAI Agent Tools web search: a model call that searches for itself.

    The caller's own xAI search key pays when the request has one (see ``current_search_key``), else
    the operator's ``XAI_*`` key when ``GROK_SEARCH_ENABLED`` is on. Without a selector (evals, the
    CLI) only the operator's key is used. A caller key that cannot be read is never replaced by the
    operator's.
    """

    name = "grok-web-search"

    def __init__(self, settings: Settings, selector: LLMSelector | None = None):
        self.settings = settings
        self.selector = selector
        self.base = (settings.xai_base_url or "https://api.x.ai/v1").rstrip("/")
        self.key = settings.xai_api_key
        self.model = settings.xai_model
        self.timeout = float(settings.grok_search_timeout_seconds or 90.0)

    @property
    def operator_available(self) -> bool:
        return bool(self.settings.grok_search_enabled and self.key and self.model)

    @property
    def available(self) -> bool:
        caller = self._caller_key()
        if caller is not None:
            return caller.usable
        return self.operator_available

    def _caller_key(self) -> SearchKey | None:
        return current_search_key(self.selector) if self.selector is not None else None

    async def answer(self, *, query: str, timeout: float | None = None) -> WebAnswer:
        caller = self._caller_key()
        if caller is not None:
            if not caller.usable:
                raise ProviderNoData(UNREADABLE_SEARCH_KEY, source=self.name)
            # The catalogue's xAI address, never XAI_BASE_URL: an operator's relay must not receive a caller's key.
            base, key, model = PROVIDERS["xai"].base_url, caller.api_key, caller.model
        elif self.operator_available:
            base, key, model = self.base, self.key, self.model
        else:
            raise ProviderNoData("No xAI key or model is configured", source=self.name)
        try:
            data = await request_json(
                "POST",
                f"{base.rstrip('/')}/responses",
                source=self.name,
                headers={"Authorization": f"Bearer {key}"},
                payload={
                    "model": model,
                    "input": query[:2000],
                    "tools": [{"type": "web_search"}],
                },
                timeout=self.timeout if timeout is None else timeout,
                retries=0,  # deliberate: a 90-second paid search is not repeated
                secrets=(key,) if caller is not None else (),
            )
        except ProviderError as exc:
            if caller is not None:
                exc.detail = scrub(exc.detail, [key])
            raise
        return _grok_answer(data, self.name)


def _grok_answer(data: dict[str, Any], source: str) -> WebAnswer:
    text_parts: list[str] = []
    sources: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "message":
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                    text_parts.append(str(content.get("text") or ""))
        elif kind == "web_search_call":
            action = item.get("action") or {}
            for entry in action.get("sources") or []:
                if isinstance(entry, dict) and entry.get("url"):
                    sources.append(str(entry["url"]))
    seen: list[str] = []
    for url in sources:
        if url not in seen:
            seen.append(url)
    return WebAnswer(text="\n".join(part for part in text_parts if part).strip(), sources=seen, source_name=source)
