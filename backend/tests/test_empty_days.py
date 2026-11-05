"""A long trip is not left with empty days while other days hold several stops, and the days that stay empty are said.

The live case: a 13-day trip got its places packed onto the first six days by the model, the
other seven came back with no stop, and the plan still said "No data gaps".
"""

import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.application.agents import AgentContext, AttractionAgent, _choose_places
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner, PlannerAssignment, PlannerDraft, _gaps
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    DataGap,
    DateRange,
    FailureKind,
    GeoLocation,
    GeoPoint,
    PlaceCandidate,
    SourceRef,
    TripContext,
)
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService
from tests.test_planner import _agents, _place
from tests.test_security import RecordingLLM

WORDS = [
    "Alder", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Ginkgo", "Hazel", "Juniper", "Larch",
    "Maple", "Oak", "Pine", "Rowan", "Spruce", "Willow", "Yew", "Aspen", "Beech", "Cypress",
]
START = date.today() + timedelta(days=40)


def _places(count: int) -> list[dict]:
    return [
        _place(f"p{i + 1}", f"{WORDS[i]} Garden", 35.0 + i * 0.01, 135.0 + (i % 4) * 0.01, rating=4.5)
        for i in range(count)
    ]


def _packed(ids: list[str], days: int = 6) -> list[dict]:
    """The model's draft: every id on the first `days` days, at most three a day."""
    per_day: list[list[str]] = [[] for _ in range(days)]
    for position, item_id in enumerate(ids):
        per_day[position % days].append(item_id)
    return [{"day_index": index, "item_ids": rows} for index, rows in enumerate(per_day) if rows]


class _ReplyLLM(RecordingLLM):
    def __init__(self, reply: dict) -> None:
        super().__init__()
        self.reply = json.dumps(reply)

    async def complete(self, *, system, user, purpose, json_mode=True, temperature=0.2, max_tokens=4096):
        from app.infrastructure.llm import LLMResult

        self.prompts.append(user)
        return LLMResult(text=self.reply, model="reply")


def _compose(settings: Settings, places: list[dict], days: int, reply: dict | None, *, hard_constraints=None, results=None):
    llm = _ReplyLLM(reply) if reply is not None else RecordingLLM()
    planner = Planner(LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings), settings, FxService())
    end = START + timedelta(days=days - 1)
    context = TripContext(
        session_id="s", destination="Kyoto", start_date=START, end_date=end, party_size=2,
        hard_constraints=list(hard_constraints or []),
    )
    results = results or _agents()
    results["attraction"].payload["places"] = places
    return asyncio.run(
        planner.compose(
            session_id="s",
            context=context,
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=35.0, lng=135.0)),
            date_range=DateRange(start=START, end=end),
            results=results,
            memory_summaries=[],
        )
    )


def _stops(day) -> list[str]:
    return [item.name for item in day.items if item.type.value == "attraction"]


# --- planner: the draft's packing is spread over the trip --------------------


def test_a_draft_that_packs_the_first_days_leaves_no_day_empty(settings: Settings) -> None:
    places = _places(16)
    planner = Planner(None, settings, FxService())  # type: ignore[arg-type]
    draft = PlannerDraft(assignments=[PlannerAssignment(**row) for row in _packed([p["id"] for p in places])])
    assignment = planner._assign_places(places, 13, draft, pace="balanced")
    counts = [len(assignment[index]) for index in range(13)]
    assert min(counts) >= 1, counts
    ids = [row["id"] for index in range(13) for row in assignment[index]]
    assert sorted(ids) == sorted(p["id"] for p in places)  # every place once, none invented


def test_a_packed_draft_composes_into_a_plan_with_a_stop_every_day(settings: Settings) -> None:
    places = _places(16)
    plan = _compose(settings, places, 13, {"assignments": _packed([p["id"] for p in places])})
    assert len(plan.days) == 13
    assert all(_stops(day) for day in plan.days), [len(_stops(day)) for day in plan.days]
    names = [name for day in plan.days for name in _stops(day)]
    assert len(names) == len(set(names)) == 16
    item_ids = [item.id for day in plan.days for item in day.items]
    assert len(item_ids) == len(set(item_ids))
    assert not any("days have verified stops" in gap.message for gap in plan.data_quality.gaps)


def test_a_day_with_two_stops_is_not_split_while_no_day_is_empty(settings: Settings) -> None:
    """The spread only fills an empty day; it does not flatten days that all have stops."""
    places = _places(9)
    planner = Planner(None, settings, FxService())  # type: ignore[arg-type]
    draft = PlannerDraft(
        assignments=[
            PlannerAssignment(day_index=0, item_ids=["p1", "p2", "p3"]),
            PlannerAssignment(day_index=1, item_ids=["p4", "p5"]),
            PlannerAssignment(day_index=2, item_ids=["p6", "p7"]),
            PlannerAssignment(day_index=3, item_ids=["p8", "p9"]),
        ]
    )
    assignment = planner._assign_places(places, 4, draft, pace="balanced")
    assert [len(assignment[index]) for index in range(4)] == [3, 2, 2, 2]


# --- fewer places than days: the rest stay open, and the plan says so -------


def test_fewer_places_than_days_leaves_the_rest_open_and_says_so(settings: Settings) -> None:
    places = _places(10)
    plan = _compose(settings, places, 13, {"assignments": _packed([p["id"] for p in places])})
    covered = [day for day in plan.days if _stops(day)]
    assert len(covered) == 10
    names = [name for day in plan.days for name in _stops(day)]
    assert len(names) == len(set(names)) == 10  # nothing padded, nothing repeated
    gaps = [gap for gap in plan.data_quality.gaps if gap.section == "attractions"]
    assert [(gap.kind, gap.message) for gap in gaps] == [
        (FailureKind.NO_DATA, "Only 10 of 13 days have verified stops; the rest are open.")
    ]


def test_the_open_days_gap_survives_tidying(settings: Settings) -> None:
    from app.application.validation import tidy_data_gaps

    places = _places(10)
    plan = _compose(settings, places, 13, {"assignments": _packed([p["id"] for p in places])})
    for drop in (False, True):
        tidy = tidy_data_gaps(plan.model_copy(deep=True), drop_edit_gaps=drop)
        assert any(gap.message.startswith("Only 10 of 13 days") for gap in tidy.data_quality.gaps)


# --- no empty day: nothing new ------------------------------------------------


def test_a_plan_with_no_empty_day_gets_no_new_gap(settings: Settings) -> None:
    results = _agents()
    failure = DataGap(section="attractions", kind=FailureKind.TIMEOUT, message="google-places timed out.")
    results["attraction"].failures = [failure]
    plan = _compose(settings, _places(4), 3, None, results=results)
    assert all(_stops(day) for day in plan.days)
    assert failure in plan.data_quality.gaps
    assert plan.data_quality.gaps == _gaps(results)  # the agents' gaps only, in their order


def test_a_day_kept_free_is_not_an_open_day(settings: Settings) -> None:
    results = _agents()
    plan = _compose(settings, _places(6), 4, None, hard_constraints=["Day 4 is a free day"], results=results)
    assert plan.days[3].items == []
    assert [len(_stops(day)) > 0 for day in plan.days] == [True, True, True, False]
    assert plan.data_quality.gaps == _gaps(results)


def test_no_places_at_all_is_reported_by_the_agent_gap_only(settings: Settings) -> None:
    """With no places the attraction agent's own gap says it; the open-days gap does not repeat it."""
    results = _agents()
    results["attraction"] = AgentResult(
        agent=AgentName.ATTRACTION, status=AgentStatus.UNAVAILABLE, payload={"places": [], "meals": []}
    )
    plan = _compose(settings, [], 3, None, results=results)
    assert plan.data_quality.gaps == _gaps(results)
    assert "attraction agent returned no usable data." in [gap.message for gap in plan.data_quality.gaps]


# --- attraction agent: the candidate pool scales with the trip ---------------


class _CountingGuide:
    name = "guide"
    available = True
    ranks_by_relevance = False

    def __init__(self, count: int = 20) -> None:
        self.count = count
        self.limits: list[tuple[str, int]] = []

    async def search(self, *, destination: str, geo: Any, category: str, limit: int, query: str | None = None):
        self.limits.append((category, limit))
        if category == "restaurant":
            return []
        return [
            PlaceCandidate(
                name=f"{WORDS[i]} Garden",
                geo=GeoPoint(lat=35.0 + i * 0.01, lng=135.0),
                rating=4.0 + (i % 10) / 10,
                source_refs=[SourceRef(title=str(i), url=f"https://example.com/{i}", source="guide")],
            )
            for i in range(self.count)
        ][:limit]


def _run_agent(settings: Settings, days: int, guide: _CountingGuide) -> AgentResult:
    end = START + timedelta(days=days - 1)
    context = TripContext(session_id="s", destination="Kyoto", start_date=START, end_date=end)
    registry = SimpleNamespace(places=[guide], notes=[], geocoder=None, web=[], web_deep=[])
    request = AgentContext(
        context=context,
        destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=35.0, lng=135.0)),
        date_range=DateRange(start=START, end=end),
        request_id="req_days",
        toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_days"),
        settings=settings,
        progress=[],
    )
    return asyncio.run(AttractionAgent(registry, settings).run(request))


@pytest.mark.parametrize("days", [1, 2, 3])
def test_a_short_trip_asks_for_ten_places_as_before(settings: Settings, days: int) -> None:
    guide = _CountingGuide()
    result = _run_agent(settings, days, guide)
    assert ("attraction", 10) in guide.limits
    assert len(result.payload["places"]) == 10


def test_a_thirteen_day_trip_asks_for_more_places_within_what_a_source_returns(settings: Settings) -> None:
    from app.application.agents import MAX_PLACE_CANDIDATES

    guide = _CountingGuide()
    result = _run_agent(settings, 13, guide)
    asked = dict(guide.limits)["attraction"]
    assert asked == min(13 * 3, MAX_PLACE_CANDIDATES) > 10
    assert len(result.payload["places"]) == asked
    ids = [row["id"] for row in result.payload["places"]]
    assert len(ids) == len(set(ids))


def test_a_five_day_trip_asks_for_three_a_day(settings: Settings) -> None:
    guide = _CountingGuide()
    _run_agent(settings, 5, guide)
    assert dict(guide.limits)["attraction"] == 15


def test_choose_places_keeps_ten_by_default() -> None:
    places = [PlaceCandidate(name=f"{WORDS[i]} Garden", geo=GeoPoint(lat=35.0, lng=135.0 + i * 0.01)) for i in range(15)]
    assert len(_choose_places(places, [])) == 10
    assert len(_choose_places(places, [], limit=14)) == 14


# --- providers: a larger ask is clamped to one page of what the API serves ---


def _capture(monkeypatch: pytest.MonkeyPatch, response: Any) -> list[dict]:
    calls: list[dict] = []

    async def fake_request_json(method, url, **kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr("app.infrastructure.providers.places.request_json", fake_request_json)
    return calls


def test_google_places_asks_for_one_page_at_most(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure.providers.places import GooglePlacesSource

    calls = _capture(monkeypatch, {"places": []})
    source = GooglePlacesSource(settings.model_copy(update={"google_places_api_key": "test-key"}))
    for limit, expected in ((10, 20), (6, 12), (20, 20), (39, 20)):
        asyncio.run(source.search(destination="Kyoto", geo=None, category="attraction", limit=limit))
        assert calls[-1]["payload"]["maxResultCount"] == expected
    assert len(calls) == 4  # one request per search, never a second page


def test_nominatim_asks_for_at_most_its_documented_forty(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infrastructure.providers.places import NominatimPlacesSource

    calls = _capture(monkeypatch, {"data": []})
    source = NominatimPlacesSource(settings)
    for limit, expected in ((10, 20), (6, 12), (3, 12), (20, 40), (39, 40)):
        asyncio.run(source.search(destination="Kyoto", geo=None, category="attraction", limit=limit))
        assert calls[-1]["params"]["limit"] == expected
    assert len(calls) == 5
