"""Web search: result parsing, keyword queries, and the search-vs-lookup policy."""

from __future__ import annotations

import re

from app.application.asking import (
    DEEP_SEARCH_WORDS,
    QUALITY_WORDS,
    _find_item_strict,
    _keyword_query,
)
from app.infrastructure.providers.websearch import _deepseek_results, _grok_answer


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
