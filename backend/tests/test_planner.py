import asyncio
from datetime import date, timedelta

from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.validation import validate_plan
from app.config import Settings
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    DateRange,
    GeoLocation,
    GeoPoint,
    TripContext,
)
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService


def _source(name: str) -> list[dict]:
    return [{"title": name, "url": "https://example.com/" + name.lower(), "source": "test"}]


def _agents(count: int = 4) -> dict[str, AgentResult]:
    places = []
    for index in range(count):
        places.append(
            {
                "id": f"p{index + 1}",
                "name": f"Stop {index + 1}",
                "category": "Attraction",
                "description": "A verified stop.",
                "geo": {"lat": 35.0 + index * 0.01, "lng": 135.0 + index * 0.01},
                "address": "Kyoto",
                "rating": 4.5,
                "source_refs": _source(f"Stop {index + 1}"),
                "tags": [],
            }
        )
    return {
        "attraction": AgentResult(agent=AgentName.ATTRACTION, status=AgentStatus.OK, payload={"places": places, "meals": []}),
        "hotel": AgentResult(
            agent=AgentName.HOTEL,
            status=AgentStatus.OK,
            payload={
                "hotels": [
                    {
                        "id": "h1",
                        "name": "Hotel One",
                        "geo": {"lat": 35.0, "lng": 135.0},
                        "nightly_price": {"amount": 100, "currency": "USD"},
                        "price_kind": "reference",
                        "rating": 4.5,
                        "source_refs": _source("Hotel One"),
                    }
                ]
            },
        ),
        "weather": AgentResult(
            agent=AgentName.WEATHER,
            status=AgentStatus.OK,
            payload={"days": []},
        ),
        "ticket": AgentResult(agent=AgentName.TICKET, status=AgentStatus.UNAVAILABLE, payload={"flights": []}),
    }


def test_planner_builds_a_continuous_grounded_plan(settings: Settings, offline_gateway: LLMGateway) -> None:
    start = date.today() + timedelta(days=30)
    context = TripContext(
        session_id="s",
        destination="Kyoto",
        start_date=start,
        end_date=start + timedelta(days=2),
        party_size=2,
        budget=400000,
        currency="JPY",
    )
    planner = Planner(offline_gateway, settings, FxService())
    plan = asyncio.run(
        planner.compose(
            session_id="s",
            context=context,
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=35.0, lng=135.0)),
            date_range=DateRange(start=start, end=start + timedelta(days=2)),
            results=_agents(),
            memory_summaries=[],
        )
    )
    assert len(plan.days) == 3
    assert plan.item_count >= 4
    assert validate_plan(plan, settings) == []
    for _, item in plan.iter_items():
        if item.type.value == "attraction":
            assert item.source_refs
            assert item.geo is not None
    assert plan.cost.status == "within"
    assert plan.cost.total > 0


def test_planner_never_invents_an_attraction_from_the_model(settings: Settings, offline_gateway: LLMGateway) -> None:
    # Offline planner can only select payload ids. With no places, the days are empty and the gap is visible.
    start = date.today() + timedelta(days=30)
    context = TripContext(
        session_id="s",
        destination="Kyoto",
        start_date=start,
        end_date=start + timedelta(days=1),
        party_size=1,
        budget=100000,
        currency="JPY",
    )
    results = _agents()
    results["attraction"] = AgentResult(agent=AgentName.ATTRACTION, status=AgentStatus.UNAVAILABLE, payload={"places": [], "meals": []})
    plan = asyncio.run(
        Planner(offline_gateway, settings, FxService()).compose(
            session_id="s",
            context=context,
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=35.0, lng=135.0)),
            date_range=DateRange(start=start, end=start + timedelta(days=1)),
            results=results,
            memory_summaries=[],
        )
    )
    assert plan.item_count == 0
    assert plan.days[0].note


def _place(place_id: str, name: str, lat: float, lng: float, **extra) -> dict:
    row = {
        "id": place_id,
        "name": name,
        "geo": {"lat": lat, "lng": lng},
        "description": "A verified stop.",
        "address": "Kyoto",
        "source_refs": _source(name),
        "tags": [],
    }
    row.update(extra)
    return row


def test_day_schedule_is_chronological_and_uses_real_transport() -> None:
    from app.application.planner import _schedule_day

    places = [
        _place("nijo", "Nijo Castle", 35.0142, 135.7481),
        _place("arashi", "Arashiyama Bamboo Forest", 35.0170, 135.6712),
        _place("kinkaku", "Kinkaku-ji", 35.0394, 135.7292),
    ]
    lunch = _place("lunch", "Lunch Place", 35.011, 135.75)
    items = _schedule_day(date(2027, 4, 4), places, lunch, None, pace="balanced", must_see=[])

    starts = [item.start for item in items]
    assert starts == sorted(starts), "items must be chronological"
    lunch_item = next(item for item in items if item.type == "meal")
    assert lunch_item.start == "12:30"
    # Lunch breaks the day; the afternoon stop comes after it.
    assert items[-1].start > lunch_item.start

    # A multi-kilometre leg is described as transit, never as a two-hour walk.
    arrivals = [item for item in items if item.type == "attraction"]
    assert "on foot" not in arrivals[1].notes
    assert "metro" in arrivals[1].notes or "bus" in arrivals[1].notes


def test_slow_pace_sees_fewer_stops_per_day(settings: Settings, offline_gateway: LLMGateway) -> None:
    planner = Planner(offline_gateway, settings, FxService())
    names = [
        "Alpha Hall", "Beta Hall", "Gamma Hall", "Delta Hall", "Epsilon Hall", "Zeta Hall",
        "Theta Hall", "Iota Hall", "Kappa Hall", "Lambda Hall", "Mu Hall", "Nu Hall",
    ]
    places = [
        _place(f"p{i}", name, 35.0 + i * 0.02, 135.0 + i * 0.02) for i, name in enumerate(names)
    ]
    slow = planner._assign_places(places, 3, None, pace="slow")
    balanced = planner._assign_places(places, 3, None, pace="balanced")
    assert all(len(rows) <= 3 for rows in slow.values())
    assert all(len(rows) <= 4 for rows in balanced.values())
    assert sum(len(rows) for rows in slow.values()) == 9
    assert sum(len(rows) for rows in balanced.values()) == 12


def test_near_duplicate_attractions_are_not_repeated(settings: Settings, offline_gateway: LLMGateway) -> None:
    planner = Planner(offline_gateway, settings, FxService())
    places = [
        _place("a1", "Arashiyama", 35.0094, 135.6667),
        _place("a2", "Arashiyama Bamboo Forest", 35.0170, 135.6712),
        _place("k", "Kinkaku-ji", 35.0394, 135.7292),
        _place("m1", "Kyoto National Museum", 34.9898, 135.7727),
        _place("m2", "Kyoto Railway Museum", 35.0117, 135.7418),
    ]
    assignment = planner._assign_places(places, 2, None, pace="balanced")
    names = [row["name"] for rows in assignment.values() for row in rows]
    assert sum("arashiyama" in name.lower() for name in names) == 1
    # Similar-looking but genuinely different museums both survive.
    assert any("national museum" in name.lower() for name in names)
    assert any("railway museum" in name.lower() for name in names)


def test_early_start_comes_from_data_not_a_site_name() -> None:
    from app.application.planner import _schedule_day

    quiet = _place("q", "Quiet Garden", 35.0, 135.0)
    busy = _place("b", "Busy Shrine", 35.01, 135.01, review_count=42000)
    early = _place("e", "Early Temple", 35.02, 135.02, opening_hours=["Monday: 6:00 AM - 4:00 PM"])

    def first_start(row: dict) -> str:
        return _schedule_day(date(2027, 4, 2), [row], None, None, pace="balanced", must_see=[])[0].start

    assert first_start(quiet) == "09:00"
    assert first_start(busy) == "08:00"
    assert first_start(early) == "08:00"


def test_named_must_see_opens_the_day_before_the_crowds() -> None:
    from app.application.planner import _schedule_day

    places = [
        _place("k", "Kinkaku-ji", 35.0394, 135.7292),
        _place("f", "Fushimi Inari Taisha", 34.9671, 135.7727),
    ]
    items = _schedule_day(
        date(2027, 4, 2), places, None, None, pace="balanced", must_see=["Fushimi Inari"]
    )
    first = items[0]
    assert "Fushimi Inari" in first.name
    assert first.start == "08:00"


def test_a_single_stop_day_is_rebalanced(settings: Settings, offline_gateway: LLMGateway) -> None:
    from app.application.planner import PlannerAssignment, PlannerDraft

    planner = Planner(offline_gateway, settings, FxService())
    names = [
        "Alpha Hall", "Beta Hall", "Gamma Hall", "Delta Hall", "Epsilon Hall",
        "Zeta Hall", "Theta Hall", "Iota Hall", "Kappa Hall",
    ]
    places = [
        _place(f"p{i}", name, 35.0 + i * 0.02, 135.0 + i * 0.02) for i, name in enumerate(names)
    ]
    draft = PlannerDraft(
        assignments=[
            PlannerAssignment(day_index=0, item_ids=["p0", "p1", "p2"]),
            PlannerAssignment(day_index=1, item_ids=["p3", "p4"]),
            PlannerAssignment(day_index=2, item_ids=["p5"]),
            PlannerAssignment(day_index=3, item_ids=["p6", "p7", "p8"]),
        ]
    )
    assignment = planner._assign_places(places, 4, draft, pace="balanced")
    counts = [len(assignment[index]) for index in range(4)]
    assert min(counts) >= 2
    assert sum(counts) == 9


def test_generated_day_title_uses_the_trusted_english_name(settings: Settings, offline_gateway: LLMGateway) -> None:
    """A Korean stop with a trusted English name must not put Hangul in an English day title."""
    start = date.today() + timedelta(days=30)
    agents = _agents()
    for place in agents["attraction"].payload["places"]:
        place["names"] = {"en": place["name"] + " (English)"}
        place["name"] = "환구단 " + place["id"]
    context = TripContext(session_id="s", destination="Seoul", start_date=start, end_date=start, party_size=2)
    plan = asyncio.run(
        Planner(offline_gateway, settings, FxService()).compose(
            session_id="s",
            context=context,
            destination=GeoLocation(name="Seoul", geo=GeoPoint(lat=35.0, lng=135.0)),
            date_range=DateRange(start=start, end=start),
            results=agents,
            memory_summaries=[],
        )
    )
    title = plan.days[0].title
    assert title.endswith(" and nearby stops"), title
    assert "환구단" not in title and "(English)" in title


def test_a_korean_stop_without_an_english_name_gets_a_romanized_day_title(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    """No English name at the source: the English day title romanizes the Korean one."""
    start = date.today() + timedelta(days=30)
    agents = _agents()
    for place in agents["attraction"].payload["places"]:
        place["names"] = {}
        place["name"] = "목척교"
    context = TripContext(session_id="s", destination="Daejeon", start_date=start, end_date=start, party_size=2)
    plan = asyncio.run(
        Planner(offline_gateway, settings, FxService()).compose(
            session_id="s",
            context=context,
            destination=GeoLocation(name="Daejeon", geo=GeoPoint(lat=35.0, lng=135.0)),
            date_range=DateRange(start=start, end=start),
            results=agents,
            memory_summaries=[],
        )
    )
    assert plan.days[0].title == "Mokcheokgyo Bridge and nearby stops"
