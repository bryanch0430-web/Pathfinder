"""A reference-only web step for the four planning agents.

DeepSeek search runs first. Relevance is our own check that the page is about
the city and the question. The provider's rerank score is not a confidence:
on a Tokyo query it sat between 1.66 and 1.73 and ranked an advertisement first.

Grok runs once, and only when that relevance is zero. A page that is on-topic
but does not state a price or a temperature is a different failure, so Grok is
not asked to invent the missing fact.

Trying a second public web backend only after the first returns nothing usable
is adapted from Agent Reach (MIT), https://github.com/Panniantong/Agent-Reach.
No source from that repository is included.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.domain.errors import ProviderError, ToolBudgetExceeded
from app.domain.models import DataGap, FailureKind
from app.infrastructure.providers.websearch import WebAnswer, WebResult

#: Hosts that are a video or a shop. A match there is not a planning source.
_BLOCKED_HOSTS = (
    "youtube.com",
    "youtu.be",
    "bilibili.com",
    "amazon.",
    "taobao.com",
    "tmall.com",
)

#: Words that say the page is about this section's question, not merely the city.
_SECTION_TERMS: dict[str, tuple[str, ...]] = {
    "attraction": (
        "things to do",
        "what to do",
        "attraction",
        "visit",
        "museum",
        "temple",
        "park",
        "garden",
        "sight",
    ),
    "hotel": ("hotel", "stay", "ryokan", "inn", "accommodation", "hostel"),
    "weather": ("weather", "climate", "temperature", "forecast", "rain"),
    "flight": ("flight", "airfare", "airline", "fly"),
}

_FACT_WORDS = {
    "attraction": "place that could be located",
    "hotel": "nightly rate for these dates",
    "weather": "temperature for these dates",
    "flight": "fare for these dates",
}


@dataclass
class WebLookup:
    """References plus the only gap this step is allowed to raise."""

    references: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    gap: DataGap | None = None

    @property
    def found(self) -> bool:
        return self.confidence > 0 and bool(self.references)


def relevance(row: WebResult, *, city: str, section: str) -> float:
    """How sure we are that this page is about the question. Never a fact score.

    Zero means the page does not mention the city, does not match the question,
    or is only a video or a shop. Authority can raise a match. It cannot create one.
    """
    city_name = (city or "").strip().casefold()
    if not city_name or section not in _SECTION_TERMS:
        return 0.0
    title = (row.title or "").casefold()
    body = " ".join((row.title or "", row.snippet or "", row.summary or "")).casefold()
    if city_name not in body:
        return 0.0
    host = urlparse(row.url or "").netloc.casefold()
    if any(blocked in host for blocked in _BLOCKED_HOSTS):
        return 0.0
    if not any(term in body for term in _SECTION_TERMS[section]):
        return 0.0
    score = 0.55
    if city_name in title:
        score += 0.25
    if 0 <= row.authority <= 4:
        score += 0.05 * row.authority
    return round(min(1.0, score), 2)


def inadequate_gap(section: str, city: str) -> DataGap:
    """The pages are about the city, and they still do not state the fact."""
    fact = _FACT_WORDS.get(section, "fact for these dates")
    return DataGap(
        section=_gap_section(section),
        kind=FailureKind.NO_DATA,
        message=f"Pages mention {city}, but none state a {fact}.",
        retryable=False,
    )


def _gap_section(section: str) -> str:
    return {
        "attraction": "attractions",
        "hotel": "hotels",
        "weather": "weather",
        "flight": "tickets",
    }.get(section, section)


def _reference(row: WebResult, *, section: str, confidence: float, source: str) -> dict[str, Any]:
    return {
        "title": row.title,
        "url": row.url,
        "snippet": (row.snippet or row.summary or "")[:300],
        "confidence": confidence,
        "section": section,
        "source": source,
        "kind": "reference",
    }


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if isinstance(exc, ProviderError):
        return exc.kind in {FailureKind.TIMEOUT, FailureKind.RATE_LIMIT, FailureKind.NETWORK}
    return False


async def lookup_web_references(
    *,
    section: str,
    city: str,
    query: str,
    deepseek: Any | None,
    grok: Any | None,
    tracer: Any | None = None,
) -> WebLookup:
    """One DeepSeek search, then Grok only when no row is relevant.

    Each backend is called at most once. A timeout or a rate limit on both is
    the only retryable gap. An empty relevant set is not retried.
    """
    rows: list[WebResult] = []
    deepseek_failed_open = False
    if deepseek is not None and getattr(deepseek, "available", True):
        try:
            found = await deepseek.search(query=query, count=5, retries=0)
            rows = list(found or [])
        except (ProviderError, asyncio.TimeoutError) as exc:
            if isinstance(exc, ToolBudgetExceeded):
                # The cap refused the search: say that, not that the search found nothing.
                return WebLookup(gap=DataGap(section=_gap_section(section), kind=exc.kind, message=str(exc)))
            deepseek_failed_open = _retryable(exc)
            rows = []

    scored = [(relevance(row, city=city, section=section), row) for row in rows]
    _record_search(
        tracer,
        provider="deepseek-search",
        query=query,
        results=[
            {
                "title": row.title,
                "url": row.url,
                "snippet": (row.snippet or row.summary or "")[:300],
                "authority": row.authority,
                "rerank_score": row.score,
                "relevance": score,
                "kept": score > 0,
            }
            for score, row in scored
        ],
    )
    kept = [(score, row) for score, row in scored if score > 0]
    if kept:
        kept.sort(key=lambda pair: pair[0], reverse=True)
        top = kept[:5]
        return WebLookup(
            references=[
                _reference(row, section=section, confidence=score, source="deepseek-search")
                for score, row in top
            ],
            confidence=top[0][0],
        )

    grok_rows, grok_failed_open = await _grok_once(
        grok, query=query, city=city, section=section, tracer=tracer
    )
    if grok_rows:
        return WebLookup(references=grok_rows[:5], confidence=max(row["confidence"] for row in grok_rows))
    if deepseek_failed_open or grok_failed_open:
        return WebLookup(
            gap=DataGap(
                section=_gap_section(section),
                kind=FailureKind.TIMEOUT,
                message="Search did not answer in time.",
                retryable=True,
            )
        )
    return WebLookup(
        gap=DataGap(
            section=_gap_section(section),
            kind=FailureKind.NO_DATA,
            message=f"No relevant page found for {city}.",
            retryable=False,
        )
    )


def _record_search(tracer: Any | None, *, provider: str, query: str, results: list[dict[str, Any]]) -> None:
    record = getattr(tracer, "record_search", None)
    if record is None:
        return
    record(provider=provider, query=query, results=results)


async def _grok_once(
    grok: Any | None,
    *,
    query: str,
    city: str,
    section: str,
    tracer: Any | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    if grok is None or not getattr(grok, "available", True):
        return [], False
    try:
        answer = await grok.answer(query=query, timeout=25.0)
    except (ProviderError, asyncio.TimeoutError) as exc:
        _record_search(tracer, provider="grok-web-search", query=query, results=[{"error": str(exc)[:200]}])
        return [], _retryable(exc)
    if not isinstance(answer, WebAnswer):
        return [], False
    _record_search(
        tracer,
        provider="grok-web-search",
        query=query,
        results=[{"text": (answer.text or "")[:500], "url": url, "kept": bool(answer.sources)} for url in (answer.sources or ["none"])],
    )
    city_name = (city or "").strip().casefold()
    text = (answer.text or "").casefold()
    if not answer.sources or not city_name or city_name not in text:
        # Unsourced prose is not a page. Relevance stays zero.
        return [], False
    snippet = (answer.text or "").strip()[:300]
    rows = [
        {
            "title": urlparse(url).netloc or url,
            "url": url,
            "snippet": snippet,
            "confidence": 0.4,
            "section": section,
            "source": "grok-web-search",
            "kind": "reference",
        }
        for url in answer.sources[:5]
    ]
    return rows, False
