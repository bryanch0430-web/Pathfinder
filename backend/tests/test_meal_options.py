"""Meal options near each day's stops, attraction alternatives, and the facts they may carry.

No network and no model: a fake place source answers the nearby searches, and the writer is
the offline gateway (or a recording one where the call count is checked).
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from app.application.llm_gateway import LLMGateway
from app.application.meals import choose_meals, search_nearby
from app.application.planner import Planner
from app.application.prompts import PromptLibrary
from app.config import Settings
from app.domain.models import (
    DateRange,
    GeoLocation,
    GeoPoint,
    PlaceCandidate,
    PlanItem,
    SourceRef,
    TripContext,
    TripPlan,
)
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService
from app.infrastructure.providers.places import cuisine_label, price_range_text
from tests.test_planner import _agents
from tests.test_security import RecordingLLM

START = date.today() + timedelta(days=40)


class NearbyPlaces:
    """A place source that takes a location bias and returns venues around the asked centre."""

    name = "fake-nearby"
    available = True
    supports_bias = True

    def __init__(self, *, per_search: int = 8, fail: bool = False, delay: float = 0.0) -> None:
        self.per_search = per_search
        self.fail = fail
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def search(self, *, destination, geo, category, limit, query=None, bias=None) -> list[PlaceCandidate]:
        self.calls.append({"category": category, "bias": bias})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("places down")
        lat, lng, _ = bias
        out = []
        for n in range(min(limit, self.per_search)):
            name = f"{category.title()} {round(lat, 3)}-{round(lng, 3)} #{n + 1}"
            out.append(
                PlaceCandidate(
                    name=name,
                    category="Restaurant",
                    geo=GeoPoint(lat=lat + 0.001 * (n + 1), lng=lng),
                    address="Kyoto",
                    rating=4.0 + n * 0.05,
                    review_count=100 * (n + 1),
                    source_refs=[SourceRef(title=name, url=f"https://maps.example/{abs(hash(name))}", source=self.name)],
                    tags=["ramen_restaurant", "restaurant"] if category == "restaurant" else [category],
                    cuisine="Ramen" if category == "restaurant" else None,
                    # The source says nothing about reservations for most venues.
                    reservable=True if n == 0 and category == "restaurant" else None,
                )
            )
        return out


def _pool(count: int) -> list[dict]:
    return [
        {
            "id": f"m{index + 1}",
            "name": f"Pool Restaurant {index + 1}",
            "category": "Restaurant",
            "geo": {"lat": 35.0 + index * 0.002, "lng": 135.0},
            "address": "Kyoto",
            "source_refs": [{"title": f"Pool {index + 1}", "url": f"https://pool.example/{index}", "source": "pool"}],
            "tags": ["restaurant"],
        }
        for index in range(count)
    ]


def _compose(settings: Settings, gateway: LLMGateway, *, days: int, sources: list | None, pool: int = 6) -> TripPlan:
    results = _agents(count=3 * days)
    results["attraction"].payload["meals"] = _pool(pool)
    end = START + timedelta(days=days - 1)
    context = TripContext(session_id="s", destination="Kyoto", start_date=START, end_date=end, party_size=2)
    planner = Planner(gateway, settings, FxService())
    return asyncio.run(
        planner.compose(
            session_id="s",
            context=context,
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=35.0, lng=135.0)),
            date_range=DateRange(start=START, end=end),
            results=results,
            memory_summaries=[],
            meal_sources=sources,
        )
    )


def _meals(day) -> dict[str, PlanItem]:
    return {item.meal_slot: item for item in day.items if item.type.value == "meal"}


def test_a_long_trip_never_runs_out_of_meals(settings: Settings, offline_gateway: LLMGateway) -> None:
    plan = _compose(settings, offline_gateway, days=6, sources=[NearbyPlaces()], pool=6)
    for day in plan.days:
        meals = _meals(day)
        assert {"breakfast", "lunch", "dinner"} <= set(meals), (day.date, sorted(meals))
    item_ids = [item.id for day in plan.days for item in day.items]
    assert len(item_ids) == len(set(item_ids))


def test_each_meal_has_two_or_three_options_from_the_source(settings: Settings, offline_gateway: LLMGateway) -> None:
    plan = _compose(settings, offline_gateway, days=4, sources=[NearbyPlaces()])
    defaults: list[str] = []
    for day in plan.days:
        for slot in ("breakfast", "lunch", "dinner"):
            item = _meals(day)[slot]
            assert 1 <= len(item.alternatives) <= 2, (slot, item.alternatives)
            names = [item.name, *(alternative.name for alternative in item.alternatives)]
            assert len(names) == len(set(names))
            for alternative in item.alternatives:
                assert alternative.distance_m is not None and alternative.distance_m >= 0
                assert alternative.source_refs
            defaults.append(item.name)
        assert 8 * 60 <= _minutes(_meals(day)["breakfast"].start) <= 9 * 60 + 30
    # No venue is the default of two meals while the source has others.
    assert len(defaults) == len(set(defaults))


def test_facts_come_only_from_the_source(settings: Settings, offline_gateway: LLMGateway) -> None:
    plan = _compose(settings, offline_gateway, days=2, sources=[NearbyPlaces()])
    lunches = [_meals(day)["lunch"] for day in plan.days]
    for lunch in lunches:
        assert lunch.rating is not None and lunch.review_count is not None
        assert lunch.cuisine == "Ramen"
    breakfasts = [_meals(day)["breakfast"] for day in plan.days]
    for breakfast in breakfasts:
        # The fake gave no reservation flag, cuisine or price for breakfast places: none is invented.
        assert breakfast.reservable is None
        assert breakfast.cuisine is None
        assert breakfast.price_level is None
        assert all(alternative.reservable is None for alternative in breakfast.alternatives)
    stated = [item for item in lunches + [a for lunch in lunches for a in lunch.alternatives] if item.reservable is not None]
    assert all(item.reservable is True for item in stated)


def test_a_failed_nearby_search_falls_back_to_the_city_pool(settings: Settings, offline_gateway: LLMGateway) -> None:
    source = NearbyPlaces(fail=True)
    plan = _compose(settings, offline_gateway, days=3, sources=[source], pool=6)
    assert source.calls  # it was asked
    for day in plan.days:
        meals = _meals(day)
        assert meals["lunch"].name.startswith("Pool Restaurant")
        assert meals["dinner"].name.startswith("Pool Restaurant")
        assert "breakfast" not in meals  # a breakfast is never a guess from a generic restaurant list


def test_a_slow_nearby_search_is_cut_off_by_the_budget(settings: Settings, offline_gateway: LLMGateway) -> None:
    quick = settings.model_copy(update={"meal_search_budget_seconds": 0.2})
    source = NearbyPlaces(delay=5.0)
    loop_start = asyncio.new_event_loop()
    try:
        started = loop_start.time()
        found = loop_start.run_until_complete(
            search_nearby(
                sources=[source], toolbox=None, destination="Kyoto",
                centres={0: {"lat": 35.0, "lng": 135.0}}, settings=quick,
            )
        )
        elapsed = loop_start.time() - started
    finally:
        loop_start.close()
    assert found == {}
    assert elapsed < 2.0
    plan = _compose(quick, offline_gateway, days=2, sources=[source], pool=4)
    assert all(_meals(day)["lunch"].name.startswith("Pool Restaurant") for day in plan.days)


def test_a_source_without_a_location_bias_is_not_searched(settings: Settings, offline_gateway: LLMGateway) -> None:
    class CityOnly(NearbyPlaces):
        supports_bias = False

    source = CityOnly()
    plan = _compose(settings, offline_gateway, days=2, sources=[source])
    assert source.calls == []
    assert all(_meals(day)["lunch"].name.startswith("Pool Restaurant") for day in plan.days)


def test_a_venue_closed_that_weekday_is_not_offered() -> None:
    day = START
    weekday = day.strftime("%A")
    closed = {
        "id": "x1", "name": "Closed Diner", "geo": {"lat": 35.0, "lng": 135.0},
        "opening_hours": [f"{weekday}: Closed"], "source_refs": [], "tags": ["restaurant"],
    }
    open_one = {
        "id": "x2", "name": "Open Diner", "geo": {"lat": 35.01, "lng": 135.0},
        "opening_hours": [], "source_refs": [], "tags": ["restaurant"],
    }
    plan = choose_meals(
        pool=[closed, open_one], nearby={}, assignment={0: [{"geo": {"lat": 35.0, "lng": 135.0}}]},
        dates=[day], fallback_centre=None,
    )
    assert plan[0]["lunch"]["name"] == "Open Diner"
    assert all(row["name"] != "Closed Diner" for row in plan[0]["lunch"]["alternatives"])


def test_the_diet_filter_holds_for_nearby_venues() -> None:
    nearby = {
        0: {
            "restaurant": [
                {"id": "a", "name": "Meat Grill", "geo": {"lat": 35.0, "lng": 135.0}, "tags": ["restaurant"], "rating": 4.9, "review_count": 9000},
                {"id": "b", "name": "Green Leaf", "geo": {"lat": 35.0, "lng": 135.0}, "tags": ["vegan_restaurant"], "rating": 4.0, "review_count": 10},
            ]
        }
    }
    plan = choose_meals(
        pool=[], nearby=nearby, assignment={0: [{"geo": {"lat": 35.0, "lng": 135.0}}]}, dates=[START],
        fallback_centre=None, suitable=lambda row: "vegan_restaurant" in row.get("tags", []),
    )
    assert plan[0]["lunch"]["name"] == "Green Leaf"
    assert "dinner" not in plan[0]  # the only vegan venue is lunch; nothing unsuitable fills dinner


def test_attractions_carry_one_nearby_unused_alternative(settings: Settings, offline_gateway: LLMGateway) -> None:
    results = _agents(count=10)
    end = START + timedelta(days=1)
    context = TripContext(session_id="s", destination="Kyoto", start_date=START, end_date=end, party_size=2)
    plan = asyncio.run(
        Planner(offline_gateway, settings, FxService()).compose(
            session_id="s", context=context, destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=35.0, lng=135.0)),
            date_range=DateRange(start=START, end=end), results=results, memory_summaries=[],
        )
    )
    attractions = [item for day in plan.days for item in day.items if item.type.value == "attraction"]
    scheduled = {item.name for item in attractions}
    with_alternative = [item for item in attractions if item.alternatives]
    assert with_alternative
    for item in with_alternative:
        assert len(item.alternatives) == 1
        assert item.alternatives[0].name not in scheduled
        assert item.alternatives[0].distance_m is not None


def test_the_writer_is_called_as_often_with_nearby_meals_as_without(settings: Settings) -> None:
    counts = []
    for sources in (None, [NearbyPlaces()]):
        llm = RecordingLLM()
        gateway = LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)
        _compose(settings, gateway, days=3, sources=sources)
        counts.append(len(llm.prompts))
    assert counts[0] == counts[1]


def test_a_plan_stored_before_meal_options_still_loads(settings: Settings, offline_gateway: LLMGateway) -> None:
    plan = _compose(settings, offline_gateway, days=2, sources=None)
    stored = plan.model_dump(mode="json")
    for day in stored["days"]:
        for item in day["items"]:
            for field in ("meal_slot", "rating", "review_count", "price_level", "cuisine", "reservable", "alternatives"):
                item.pop(field, None)
    loaded = TripPlan.model_validate(stored)
    item = loaded.days[0].items[0]
    assert item.alternatives == [] and item.meal_slot is None and item.reservable is None


def test_cuisine_and_price_range_are_read_from_the_source_only() -> None:
    assert cuisine_label("ramen_restaurant", ["ramen_restaurant", "restaurant"]) == "Ramen"
    assert cuisine_label("restaurant", ["restaurant", "food"]) is None
    assert cuisine_label(None, []) is None
    assert price_range_text(None) == ""
    assert price_range_text({"startPrice": {"currencyCode": "JPY", "units": "1000"}}) == "JPY 1000+"
    assert (
        price_range_text({"startPrice": {"currencyCode": "JPY", "units": "1000"}, "endPrice": {"currencyCode": "JPY", "units": "2000"}})
        == "JPY 1000-2000"
    )


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)
