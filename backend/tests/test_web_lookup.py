"""Relevance is our check. Grok runs only when that relevance is zero."""

from __future__ import annotations

import asyncio

from app.application.web_lookup import lookup_web_references, relevance
from app.domain.errors import ProviderError
from app.domain.models import FailureKind
from app.infrastructure.providers.websearch import WebAnswer, WebResult


def _row(title: str, url: str = "https://guide.example/tokyo", **kwargs: object) -> WebResult:
    return WebResult(title=title, url=url, summary=str(kwargs.get("summary") or ""), snippet=str(kwargs.get("snippet") or ""), authority=int(kwargs.get("authority") or -1))


def test_an_advertisement_is_not_relevant_and_a_guide_is() -> None:
    ad = _row("Why Private-Guided Travel Is Worth the Expense", authority=3)
    guide = _row("10 Things To Do in Tokyo", summary="Temples, parks and museums.", authority=3)

    assert relevance(ad, city="Tokyo", section="attraction") == 0
    assert relevance(guide, city="Tokyo", section="attraction") > 0


def test_a_video_host_is_not_a_source_even_when_the_title_matches() -> None:
    video = _row(
        "Things to do in Tokyo",
        url="https://www.youtube.com/watch?v=abc",
        summary="A walking tour.",
    )

    assert relevance(video, city="Tokyo", section="attraction") == 0


def test_rerank_score_does_not_create_relevance() -> None:
    """The API score is not a confidence. A high score without the city is zero."""
    row = _row("Best private tours", summary="Worth the expense.", authority=4)
    row.score = 9.5

    assert relevance(row, city="Tokyo", section="attraction") == 0


class _DeepSeek:
    available = True

    def __init__(self, rows: list[WebResult] | None = None, exc: Exception | None = None) -> None:
        self.rows = rows or []
        self.exc = exc
        self.calls = 0

    async def search(self, *, query: str, count: int = 5, retries: int = 1) -> list[WebResult]:
        self.calls += 1
        assert retries == 0
        if self.exc:
            raise self.exc
        return self.rows


class _Grok:
    available = True

    def __init__(self, answer: WebAnswer | None = None, exc: Exception | None = None) -> None:
        self.answer_value = answer or WebAnswer(text="", sources=[])
        self.exc = exc
        self.calls = 0

    async def answer(self, *, query: str, timeout: float | None = None) -> WebAnswer:
        self.calls += 1
        assert timeout == 25.0
        if self.exc:
            raise self.exc
        return self.answer_value


def test_a_relevant_page_does_not_call_grok_and_has_no_price() -> None:
    deepseek = _DeepSeek([_row("Hotels in Tokyo", summary="A ryokan near the station.")])
    grok = _Grok()

    found = asyncio.run(
        lookup_web_references(
            section="hotel",
            city="Tokyo",
            query="hotels in Tokyo",
            deepseek=deepseek,
            grok=grok,
        )
    )

    assert grok.calls == 0
    assert found.confidence > 0
    assert found.gap is None
    assert found.references[0]["kind"] == "reference"
    assert "price" not in found.references[0]


def test_zero_relevance_calls_grok_once_and_keeps_the_urls() -> None:
    deepseek = _DeepSeek([_row("Private guided travel", summary="Worth the expense.")])
    grok = _Grok(
        WebAnswer(
            text="A published guide to hotels in Tokyo.",
            sources=["https://guide.example/hotels"],
        )
    )

    found = asyncio.run(
        lookup_web_references(
            section="hotel",
            city="Tokyo",
            query="hotels in Tokyo",
            deepseek=deepseek,
            grok=grok,
        )
    )

    assert deepseek.calls == 1
    assert grok.calls == 1
    assert found.references[0]["url"] == "https://guide.example/hotels"
    assert found.references[0]["source"] == "grok-web-search"
    assert found.gap is None


def test_grok_prose_without_a_url_is_not_a_page() -> None:
    deepseek = _DeepSeek([])
    grok = _Grok(WebAnswer(text="Tokyo is mild in December, about 12 C.", sources=[]))

    found = asyncio.run(
        lookup_web_references(
            section="weather",
            city="Tokyo",
            query="typical weather in Tokyo in December",
            deepseek=deepseek,
            grok=grok,
        )
    )

    assert found.references == []
    assert found.gap is not None
    assert found.gap.retryable is False
    assert "No relevant page found for Tokyo" in found.gap.message


def test_both_timing_out_is_the_only_retryable_gap() -> None:
    deepseek = _DeepSeek(exc=ProviderError("slow", kind=FailureKind.TIMEOUT, source="deepseek-search"))
    grok = _Grok(exc=ProviderError("limited", kind=FailureKind.RATE_LIMIT, source="grok-web-search"))

    found = asyncio.run(
        lookup_web_references(
            section="flight",
            city="Tokyo",
            query="Hong Kong to Tokyo flights",
            deepseek=deepseek,
            grok=grok,
        )
    )

    assert grok.calls == 1
    assert found.gap is not None
    assert found.gap.retryable is True
    assert found.gap.message == "Search did not answer in time."
