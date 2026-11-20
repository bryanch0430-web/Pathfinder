"""Restaurant and sight names come from a live listing, then the map."""

import pytest

from app.routing.places import suggestion_kind, suggestion_reply
from app.routing.tools import serp_keys


def test_a_question_does_not_keep_the_draft_and_a_named_place_is_found():
    from app.agents.trip_planner.nodes.planning_trip_node import clip_to_answer
    from app.routing.facts import fact_topics, place_in_question
    assert fact_topics("does Menya NOBUNAGA need bookings or opening hours") == {"booking", "hours"}
    reply = clip_to_answer(
        "I can check that.\n\nKevin's 10-Day Japan Food Trip — First Draft\n**Route:** Hong Kong → Tokyo\nDay 1 — Tokyo"
    )
    assert reply == "I can check that."
    assert "First Draft" not in reply
    name = place_in_question(
        "opening hours for Menya NOBUNAGA",
        None,
        [{"name": "Menya NOBUNAGA", "category": "food"}, {"name": "Kyoto", "category": "sight"}],
    )
    assert name == "Menya NOBUNAGA"


def test_a_hours_object_is_read_as_days_and_times():
    from app.routing.facts import plain_text, web_fact_lines
    hours = {
        "friday": {"opens": "10\u202fAM", "closes": "3\u202fAM"},
        "saturday": {"opens": "10\u202fAM", "closes": "3\u202fAM"},
        "monday_labor_day": {"opens": "11\u202fAM", "closes": "9\u202fPM"},
    }
    text = plain_text(hours)
    assert "{" not in text and "\\u202f" not in text
    assert "Friday–Saturday: 10 AM – 3 AM" in text
    assert "Monday (labor day): 11 AM – 9 PM" in text
    dumped = web_fact_lines([{"title": "A place", "text": str(hours)}])
    assert "From a web search:" in dumped and "{" not in dumped


def test_a_booking_question_across_days_names_restaurants_not_a_wander():
    from app.routing.facts import booking_roll_call, day_span, named_meals, venue_name
    assert day_span("which restaurant really need booking (from day 1 to 10)", 10) == (1, 10)
    assert venue_name("Dinner at Menya NOBUNAGA") == "Menya NOBUNAGA"
    assert venue_name("Dōtonbori street-food wander") is None
    days = [
        {"day": 1, "items": [{"name": "Dōtonbori street-food wander", "category": "food"}]},
        {"day": 3, "items": [{"name": "Dinner at Menya NOBUNAGA", "category": "food"}]},
        {"day": 4, "items": [{"name": "Lunch at Tsujita Ginza", "category": "meal"}]},
    ]
    assert named_meals(days, (1, 10)) == [(3, "Menya NOBUNAGA"), (4, "Tsujita Ginza")]
    text = booking_roll_call((1, 10), [(3, "Menya NOBUNAGA", True), (4, "Tsujita Ginza", None)])
    assert "Menya NOBUNAGA" in text and "take reservations" in text
    assert "doesn't say" in text and "street-food" not in text
    assert "I don't make any booking" in text


def test_a_google_row_becomes_a_trip_details_listing():
    from app.services.place_search import as_around, merge_places
    item = as_around({
        "name": "Endo Sushi", "lat": 34.66, "lon": 135.43, "label": "Osaka",
        "hours": ["Monday: 10 AM – 2 PM"], "maps_url": "https://maps.google.com/x",
        "website": "https://endo.example", "blurb": "Market sushi.", "price": "JPY 2000-4000",
        "rating": 4.5, "cuisine": "Sushi",
    }, "food", "eat", (34.69, 135.50))
    assert item["source"] == "Google Places" and item["tab"] == "food"
    assert item["hours"].startswith("Monday") and item["website"].startswith("https://endo")
    assert "Sushi" in item["blurb"] and item["source_url"].startswith("https://maps.google.com")
    merged = merge_places([item], [{"name": "Endo Sushi", "tab": "food", "source": "Wikivoyage"},
                                   {"name": "Osaka Castle", "tab": "see", "source": "Wikivoyage"}])
    assert [row["name"] for row in merged] == ["Endo Sushi", "Osaka Castle"]
    assert merged[0]["source"] == "Google Places"


def test_a_restaurant_request_asks_for_named_places():
    assert suggestion_kind("suggest a bunch of restaurants") == "restaurant"
    assert suggestion_kind("我叫佢suggest一堆餐廳俾我") == "restaurant"
    assert suggestion_kind("recommend some sights") == "sight"
    assert suggestion_kind("book the restaurant") is None
    text = suggestion_reply("Kyoto", "restaurant", [{
        "name": "Nishiki Market", "label": "Nakagyo Ward, Kyoto", "source": "google", "rating": 4.5,
    }])
    assert "Nishiki Market" in text and "Google Places" in text
    assert "Tell me the city" in suggestion_reply("", "restaurant", [])


def test_only_live_search_keys_are_used(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SERPAPI_KEY", "primary")
    monkeypatch.setattr(settings, "SERPAPI_KEY_POOL", "active|New|primary;ready|Next|second;paused|Later|third;exhausted|Used up|fourth")
    assert serp_keys() == ["primary", "second"]


@pytest.mark.asyncio
async def test_place_search_uses_a_listing_then_the_map(monkeypatch):
    from app.services import place_search

    async def google(text, kind, limit, lat=None, lon=None, radius=5000):
        assert "Kyoto" in text and kind == "restaurant"
        return [{"name": "Nishiki Market", "lat": 35.0, "lon": 135.7, "label": "Kyoto", "source": "google", "rating": 4.5, "reservable": True, "hours": []}]

    monkeypatch.setattr(place_search, "_google_text", google)
    rows = await place_search.search_places("restaurant", kind="restaurant", limit=3, destination="Kyoto")
    assert rows[0]["name"] == "Nishiki Market" and rows[0]["source"] == "google"

    async def empty(text, kind, limit, lat=None, lon=None, radius=5000):
        return []

    async def named(query, limit=6):
        return [{"name": "Pontocho", "lat": 1.0, "lon": 2.0, "label": "Kyoto"}]

    monkeypatch.setattr(place_search, "_google_text", empty)
    monkeypatch.setattr("app.services.enrichment.search_named", named)
    rows = await place_search.search_places("restaurant", kind="restaurant", limit=3, destination="Kyoto")
    assert rows[0]["name"] == "Pontocho" and rows[0]["source"] == "openstreetmap"
    sights, restaurants = await place_search.draft_place_names("Kyoto")
    assert restaurants == ["Pontocho — Kyoto"]
    assert sights == ["Pontocho — Kyoto"]
