"""Blocked days, arrival/departure windows and named place wishes.

These rules must hold without a model: they are read from the traveller's own
constraints and tags, so an offline planner honours them too.
"""

import asyncio
from datetime import date, timedelta

from app.application.agents import AgentContext, AttractionAgent
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.toolbox import Toolbox
from app.application.trip_rules import blocked_dates, place_wishes, travel_windows, wish_matches
from app.config import Settings
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    DateRange,
    GeoLocation,
    GeoPoint,
    PlaceCandidate,
    SourceRef,
    TripContext,
)
from app.evaluation import SandboxRegistry
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService

START = date(2026, 11, 18)
DATES = [START + timedelta(days=offset) for offset in range(6)]


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


# --- blocked days ----------------------------------------------------------


def test_a_day_the_traveller_keeps_free_is_blocked() -> None:
    assert set(blocked_dates(["19/11 cannot have anyplan"], DATES)) == {date(2026, 11, 19)}
    assert set(blocked_dates(["No plans on Nov 20"], DATES)) == {date(2026, 11, 20)}
    assert set(blocked_dates(["Day 3 is a free day"], DATES)) == {date(2026, 11, 20)}
    assert set(blocked_dates(["21/11 唔好安排任何行程"], DATES)) == {date(2026, 11, 21)}


def test_a_constraint_without_a_free_day_blocks_nothing() -> None:
    assert blocked_dates(["must include Lotte World", "no spicy food on 19/11"], DATES) == {}
    # A date outside the trip is not a trip day.
    assert blocked_dates(["01/12 cannot have any plan"], DATES) == {}


# --- arrival and departure -------------------------------------------------


def test_arrival_time_moves_the_first_day_start() -> None:
    windows = travel_windows(["18:35 到仁川機場"], DATES)
    assert windows[DATES[0]].start == 18 * 60 + 35 + 120
    windows = travel_windows(["We land at 9:10am"], DATES)
    assert windows[DATES[0]].start == 9 * 60 + 10 + 120


def test_departure_time_ends_the_last_day_early() -> None:
    windows = travel_windows(["19:35 上機"], DATES)
    assert windows[DATES[-1]].end == 19 * 60 + 35 - 210
    windows = travel_windows(["flight home at 7:35pm"], DATES)
    assert windows[DATES[-1]].end == 19 * 60 + 35 - 210
    # Leaving for the airport is the end of the day itself.
    windows = travel_windows(["15:30 返酒店攞行李出機場"], DATES)
    assert windows[DATES[-1]].end == 15 * 60 + 30


def test_a_dated_arrival_applies_to_that_date() -> None:
    windows = travel_windows(["arrive 18:35 on 19/11"], DATES)
    assert DATES[0] not in windows
    assert windows[date(2026, 11, 19)].start == 18 * 60 + 35 + 120


def test_a_time_without_travel_words_sets_no_window() -> None:
    assert travel_windows(["Dinner at 19:00 in Sinsa", "和談林 09:00-17:00"], DATES) == {}


# --- named place wishes ----------------------------------------------------


def test_free_text_tags_become_place_wishes_but_known_interests_do_not() -> None:
    context = TripContext(
        session_id="s",
        preferences=["museums", "slow pace", "聖水洞", "must-see: Gyeongbokgung", "和談林賞楓"],
        hard_constraints=["must include Lotte World", "19/11 cannot have anyplan", "arrive 18:35"],
    )
    assert place_wishes(context) == ["Gyeongbokgung", "Lotte World", "聖水洞", "和談林賞楓"]


def test_a_wish_matches_a_name_or_an_alias_but_not_noise() -> None:
    lotte = PlaceCandidate(name="롯데월드", aliases=["Lotte World", "樂天世界"])
    assert wish_matches(lotte, "Lotte World")
    assert not wish_matches(PlaceCandidate(name="T"), "聖水洞")
    assert wish_matches(PlaceCandidate(name="Seongsu-dong Cafe Street"), "Seongsu-dong")


# --- planner ---------------------------------------------------------------


def _place(index: int, name: str | None = None) -> dict:
    label = name or f"Stop {index}"
    return {
        "id": f"p{index}",
        "name": label,
        "category": "Attraction",
        "description": "A verified stop.",
        "geo": {"lat": 37.55 + index * 0.003, "lng": 126.97 + index * 0.003},
        "address": "Seoul",
        "rating": 4.5,
        "source_refs": [{"title": label, "url": f"https://example.com/{index}", "source": "test"}],
        "tags": [],
    }


def _meal(index: int) -> dict:
    row = _place(100 + index, f"Restaurant {index}")
    row["id"] = f"m{index}"
    return row


def _results(places: list[dict], wishes: dict | None = None) -> dict[str, AgentResult]:
    payload = {"places": places, "meals": [_meal(i) for i in range(1, 13)]}
    if wishes is not None:
        payload["wishes"] = wishes
    empty = {"hotel": "hotels", "weather": "days", "ticket": "flights"}
    results = {"attraction": AgentResult(agent=AgentName.ATTRACTION, status=AgentStatus.OK, payload=payload)}
    for key, field in empty.items():
        results[key] = AgentResult(agent=AgentName(key), status=AgentStatus.UNAVAILABLE, payload={field: []})
    return results


def _compose(settings: Settings, gateway: LLMGateway, context: TripContext, results: dict[str, AgentResult]):
    planner = Planner(gateway, settings, FxService())
    return asyncio.run(
        planner.compose(
            session_id="s",
            context=context,
            destination=GeoLocation(name="Seoul", geo=GeoPoint(lat=37.56, lng=126.97)),
            date_range=DateRange(start=DATES[0], end=DATES[-1]),
            results=results,
            memory_summaries=[],
        )
    )


def _seoul(**overrides) -> TripContext:
    values = dict(
        session_id="s",
        destination="Seoul",
        origin="Hong Kong",
        start_date=DATES[0],
        end_date=DATES[-1],
        party_size=2,
        budget=100000,
        currency="HKD",
    )
    values.update(overrides)
    return TripContext(**values)


def test_planner_keeps_a_blocked_day_empty_and_respects_flight_times(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    context = _seoul(hard_constraints=["19/11 cannot have anyplan", "18:35 到仁川機場", "19:35 上機"])
    plan = _compose(settings, offline_gateway, context, _results([_place(i) for i in range(1, 13)]))

    first, blocked, last = plan.days[0], plan.days[1], plan.days[-1]
    assert blocked.items == []
    assert "free" in blocked.note.lower()
    # Landing at 18:35 leaves no time for a sight or a 19:00 dinner.
    assert [item for item in first.items if item.type.value == "attraction"] == []
    assert all(_minutes(item.start) >= 18 * 60 + 35 + 120 for item in first.items)
    # A 19:35 flight means the last stop ends by 16:05 and there is no dinner.
    assert last.items
    assert all(_minutes(item.end) <= 16 * 60 + 5 for item in last.items)
    # The other days still carry the stops.
    assert all(day.items for day in plan.days[2:5])

    by_text = {check.text: check for check in plan.constraints}
    assert by_text["19/11 cannot have anyplan"].status.value == "pass"
    assert by_text["18:35 到仁川機場"].status.value == "pass"
    assert by_text["19:35 上機"].status.value == "pass"
    # A day kept free on purpose is not a missing stop.
    assert "2026-11-19" not in by_text["Every day has at least one verified stop"].evidence


def test_planner_schedules_a_found_wish_and_reports_one_it_could_not_find(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    places = [_place(i) for i in range(1, 9)] + [_place(9, "롯데월드")]
    context = _seoul(preferences=["和談林賞楓"], hard_constraints=["must include Lotte World"])
    plan = _compose(
        settings, offline_gateway, context, _results(places, wishes={"Lotte World": "롯데월드", "和談林賞楓": None})
    )

    names = [item.name for day in plan.days for item in day.items]
    assert "롯데월드" in names
    by_text = {check.text: check for check in plan.constraints}
    assert by_text["must include Lotte World"].status.value == "pass"
    assert by_text["Include 和談林賞楓"].status.value == "unknown"


# --- attraction agent ------------------------------------------------------


class _WishSource:
    name = "sandbox-wish-places"

    def __init__(self) -> None:
        self.queries: list[str | None] = []

    async def search(self, *, query=None, category="attraction", **_):
        self.queries.append(query)
        ref = [SourceRef(title="x", url="https://example.com/x", source=self.name)]
        if category != "attraction":
            return []
        if query is None:
            return [
                PlaceCandidate(name=f"Stop {i}", geo=GeoPoint(lat=37.5 + i / 100, lng=127.0), rating=4.9, source_refs=ref)
                for i in range(12)
            ]
        if query == "Lotte World":
            return [
                PlaceCandidate(name="롯데월드", aliases=["Lotte World"], geo=GeoPoint(lat=37.51, lng=127.1), rating=3.0, source_refs=ref)
            ]
        return [PlaceCandidate(name="T", geo=GeoPoint(lat=37.6, lng=126.8), source_refs=ref)]


class _NullStore:
    def log_tool_call(self, **_row) -> None:
        return None


def test_attraction_agent_searches_each_wish_and_keeps_only_real_matches(settings: Settings) -> None:
    registry = SandboxRegistry(settings, "Seoul")
    source = _WishSource()
    registry.places = [source]
    registry.notes = []
    registry.web = []
    request = AgentContext(
        context=_seoul(preferences=["聖水洞"], hard_constraints=["must include Lotte World"]),
        destination=GeoLocation(name="Seoul", country_code="KR", geo=GeoPoint(lat=37.56, lng=126.97), source="sandbox"),
        date_range=DateRange(start=DATES[0], end=DATES[-1]),
        request_id="req_wish",
        toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=_NullStore(), request_id="req_wish"),
        settings=settings,
    )
    result = asyncio.run(AttractionAgent(registry, settings).run(request))

    assert "Lotte World" in source.queries and "聖水洞" in source.queries
    assert result.payload["wishes"] == {"Lotte World": "롯데월드", "聖水洞": None}
    names = [row["name"] for row in result.payload["places"]]
    # A low-rated wish is kept even though ten better-rated stops came back.
    assert "롯데월드" in names
    assert "T" not in names
