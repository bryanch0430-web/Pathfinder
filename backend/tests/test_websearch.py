"""Web search: result parsing, keyword queries, and the search-vs-lookup policy."""

from __future__ import annotations

import re

import asyncio

from app.application.asking import (
    DEEP_SEARCH_WORDS,
    OFF_TRIP_LOOKUP,
    QUALITY_WORDS,
    AskPath,
    _find_item_strict,
    _keyword_query,
    about_this_trip,
)
from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import FailureKind, ProviderError
from app.domain.models import TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.websearch import (
    DeepSeekSearchSource,
    WebResult,
    _deepseek_results,
    _grok_answer,
    _serpapi_web_results,
)


def test_deepseek_results_map_the_documented_fields() -> None:
    payload = {
        "results": [
            {
                "title": "Lan Kwai Fong Nightlife",
                "url": "https://example.com/lkf",
                "summary": "Bars and clubs in Central.",
                "snippet": "short",
                "site_name": "Example",
                "date": "2026-05-01T00:00:00+00:00",
                "authority": 3,
                "rerank_score": 1.96,
            },
            {"title": "no url", "url": ""},
        ]
    }

    rows = list(_deepseek_results(payload))

    assert len(rows) == 1, "a result without a URL is useless as a citation"
    row = rows[0]
    assert row.url == "https://example.com/lkf"
    assert row.authority == 3
    assert round(row.score, 2) == 1.96
    assert row.published.startswith("2026-05-01")


def test_grok_answer_collects_text_and_dedupes_sources() -> None:
    payload = {
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "type": "search",
                    "sources": [
                        {"type": "url", "url": "https://a.example/x"},
                        {"type": "url", "url": "https://b.example/y"},
                        {"type": "url", "url": "https://a.example/x"},
                    ],
                },
            },
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "Ocean Park is worth visiting for families."}
                ],
            },
        ]
    }

    answer = _grok_answer(payload, "grok-web-search")

    assert answer.text.startswith("Ocean Park is worth")
    assert answer.sources == ["https://a.example/x", "https://b.example/y"]
    assert answer.source_name == "grok-web-search"


def test_an_invalid_search_key_falls_through_to_the_judge_key(monkeypatch) -> None:
    used: list[str] = []

    async def fake(method, url, **kwargs):
        used.append(kwargs["headers"]["Authorization"][-4:])
        if used[-1] == "bad1":
            raise ProviderError("refused", kind=FailureKind.AUTH, source="deepseek-search")
        return {"results": [{"title": "Gundam", "url": "https://example.com/gundam", "summary": "robots"}]}

    monkeypatch.setattr("app.infrastructure.providers.websearch.request_json", fake)
    settings = Settings(deepseek_search_api_key="sk-search-bad1", judge_api_key="sk-judge-good")

    rows = asyncio.run(DeepSeekSearchSource(settings).search(query="gundam", count=1, retries=0))

    assert used == ["bad1", "good"]
    assert rows[0].title == "Gundam"


def test_serpapi_web_results_keep_the_link_and_snippet() -> None:
    rows = list(
        _serpapi_web_results(
            {"organic_results": [{"title": "Gundam", "link": "https://example.com/g", "snippet": "Giant robots.", "source": "Example"}]},
            4,
        )
    )
    assert rows == [WebResult(title="Gundam", url="https://example.com/g", summary="Giant robots.", snippet="Giant robots.", site_name="Example")]


def test_a_question_unrelated_to_the_trip_is_not_looked_up(settings: Settings) -> None:
    kyoto = TripContext(session_id="s", destination="Kyoto", preferences=["must-see: Gundam"])
    assert about_this_trip("what is gundam from japan", kyoto, None) is True
    assert about_this_trip("what is the weather on day 2?", kyoto, None) is True
    assert about_this_trip("how to earn revenue", kyoto, None) is False

    class Web:
        name = "fake-web"
        calls = 0

        async def search(self, **kwargs):
            self.calls += 1
            return []

    web = Web()
    path = AskPath(
        gateway=LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings),
        registry=type("R", (), {"web": [web], "web_deep": [], "weather": [], "routes": [], "places": []})(),
        settings=settings,
    )
    text, _related, source = asyncio.run(
        path.answer(
            question="how to earn revenue",
            plan=None,
            context=kyoto,
            toolbox=Toolbox(path="ask", tracer=NoopTracer(), store=None, request_id="r"),
            emit=None,
        )
    )
    assert text == OFF_TRIP_LOOKUP
    assert source == "refused"
    assert web.calls == 0


def test_what_is_this_uses_the_selected_stop(settings: Settings) -> None:
    from app.domain.models import TripContext, TripPlan

    plan = TripPlan.model_validate(
        {
            "trip_id": "t",
            "session_id": "s",
            "version": 1,
            "destination": {"name": "Hong Kong", "country_code": "HK", "timezone": "Asia/Hong_Kong"},
            "dates": {"start": "2027-03-02", "end": "2027-03-02"},
            "days": [
                {
                    "date": "2027-03-02",
                    "title": "Day two",
                    "items": [
                        {
                            "id": "m1",
                            "type": "attraction",
                            "name": "Mendel's",
                            "start": "11:00",
                            "end": "12:00",
                            "notes": "A small shop in Sham Shui Po.",
                            "geo": {"lat": 22.3, "lng": 114.1},
                            "source_refs": [{"title": "Mendel's", "url": "https://example.com/m", "source": "google-places"}],
                        }
                    ],
                }
            ],
            "budget": {"amount": 9000, "currency": "HKD"},
        }
    )
    path = AskPath(
        gateway=LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings),
        registry=type("R", (), {"web": [], "web_deep": [], "weather": [], "routes": [], "places": []})(),
        settings=settings,
    )
    context = TripContext(session_id="s", destination="Hong Kong")
    toolbox = Toolbox(path="ask", tracer=NoopTracer(), store=None, request_id="r")
    bare, _, bare_source = asyncio.run(
        path.answer(question="what is this", plan=plan, context=context, toolbox=toolbox, emit=None)
    )
    assert bare == OFF_TRIP_LOOKUP and bare_source == "refused"
    text, related, source = asyncio.run(
        path.answer(
            question="what is this",
            plan=plan,
            context=context,
            toolbox=toolbox,
            emit=None,
            scope_item_id="m1",
        )
    )
    assert source != "refused" and related is True
    assert "Mendel's" in text and "Sham Shui Po" in text



def test_keyword_query_strips_framing_and_keeps_the_city() -> None:
    assert _keyword_query("what's the nightlife like in Hong Kong?", "Hong Kong") == "nightlife hong kong"
    assert _keyword_query("is the food scene good?", "Kyoto").lower() == "food scene kyoto"


def test_search_policy_words_are_disjoint() -> None:
    """Synthesis questions may use the slow search; lookups may not."""
    assert re.search(DEEP_SEARCH_WORDS, "what is the nightlife like in hong kong")
    assert not re.search(DEEP_SEARCH_WORDS, "what is good about amber")
    assert re.search(QUALITY_WORDS, "what is good about amber")
    assert not re.search(QUALITY_WORDS, "what is the nightlife like in hong kong")


def test_strict_item_match_ignores_the_destination_name(settings) -> None:
    from app.domain.models import DayPlan, GeoPoint, ItemType, PlanItem, TripPlan

    plan = TripPlan.model_validate(
        {
            "trip_id": "t",
            "session_id": "s",
            "version": 1,
            "destination": {"name": "Hong Kong", "country_code": "HK", "timezone": "Asia/Hong_Kong"},
            "dates": {"start": "2027-03-01", "end": "2027-03-01"},
            "days": [
                {
                    "date": "2027-03-01",
                    "title": "Day one",
                    "items": [
                        {
                            "id": "i1",
                            "type": "attraction",
                            "name": "Hong Kong Disneyland",
                            "start": "09:00",
                            "end": "12:00",
                            "geo": {"lat": 22.3, "lng": 114.1},
                            "source_refs": [
                                {"title": "google-places: Disneyland", "url": "https://example.com/d", "source": "google-places"}
                            ],
                        }
                    ],
                }
            ],
            "budget": {"amount": 1000, "currency": "HKD"},
        }
    )

    assert _find_item_strict(plan, "what is the nightlife like in Hong Kong?") is None
    assert _find_item_strict(plan, "is Disneyland worth it?") is not None
