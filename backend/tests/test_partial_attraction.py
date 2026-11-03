"""An attraction agent cut off by the pre-planning budget keeps everything it had already fetched.

Not only the places it located: the restaurants and community notes fetched before, and the
places the traveller named (found with coordinates, or not yet looked up), so the plan loses
nothing the agent had in hand and says honestly what it could not check.

Most tests stop the agent at a point where a fake blocks, then take its partial result
directly, so they do not depend on how fast the machine is. The last two go through the real
runner and the real budget.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

from app.application.agents import AgentContext, AttractionAgent
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    DateRange,
    EvalCase,
    FailureKind,
    GeoLocation,
    GeoPoint,
    NoteItem,
    PlaceCandidate,
    SourceRef,
    TripContext,
)
from app.eval_efficiency import case_context, cost_sandbox
from app.infrastructure.observability import NoopTracer

KYOTO = GeoPoint(lat=35.0116, lng=135.7681)
NAMES = ["Kinkaku-ji", "Fushimi Inari Taisha", "Philosopher's Path", "Kiyomizu-dera", "Nijo Castle", "Arashiyama Bamboo Grove"]
COORDS = [(35.0394, 135.7292), (34.9671, 135.7727), (35.0270, 135.7949), (34.9949, 135.7850), (35.0142, 135.7482), (35.0170, 135.6713)]
KNOWN = {f"{name}, Kyoto": point for name, point in zip(NAMES, COORDS)}
KNOWN["Gion Corner, Kyoto"] = (35.0037, 135.7754)
RESTAURANTS = [("Nishiki Ramen", 35.005, 135.764), ("Gion Tempura", 35.003, 135.775), ("Pontocho Izakaya", 35.007, 135.770)]
GION_REF = SourceRef(title="Gion Corner", url="https://example.com/gion-corner", source="guide")
#: The same place as a source with coordinates knows it, and as one without.
GION = PlaceCandidate(name="Gion Corner", geo=GeoPoint(lat=35.0037, lng=135.7754), source_refs=[GION_REF])
GION_WITHOUT_COORDINATES = PlaceCandidate(name="Gion Corner", source_refs=[GION_REF])


def _ref(slug: str) -> SourceRef:
    return SourceRef(title=slug, url=f"https://example.com/{slug.lower().replace(' ', '-')}", source="guide")


class _Stop:
    """Shared by the fakes: whichever one blocks sets the event, so the test knows where the agent is."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()

    async def block(self) -> None:
        self.reached.set()
        await asyncio.Event().wait()


class _Guide:
    """A place source: guide listings (with or without coordinates), restaurants, and named places."""

    name = "guide"
    available = True
    ranks_by_relevance = False

    def __init__(self, stop: _Stop, *, located: bool, restaurants: bool = True, named: dict[str, PlaceCandidate] | None = None,
                 blocks_on: str | None = None) -> None:
        self.stop = stop
        self.located = located
        self.restaurants = restaurants
        self.named = named or {}
        self.blocks_on = blocks_on

    async def search(self, *, destination: str, geo: Any, category: str, limit: int, query: str | None = None) -> list[PlaceCandidate]:
        if query and query == self.blocks_on:
            await self.stop.block()
        if query and query in self.named:
            return [self.named[query].model_copy(deep=True)]
        if category == "restaurant":
            if not self.restaurants:
                return []
            return [
                PlaceCandidate(name=name, category="restaurant", geo=GeoPoint(lat=lat, lng=lng), rating=4.4, source_refs=[_ref(name)])
                for name, lat, lng in RESTAURANTS
            ][:limit]
        listing = [
            PlaceCandidate(
                name=name,
                geo=GeoPoint(lat=lat, lng=lng) if self.located else None,
                source_refs=[_ref(name)],
            )
            for name, (lat, lng) in zip(NAMES, COORDS)
        ]
        if query:
            return [place for place in listing if query.lower() in place.name.lower()][:limit]
        return listing[:limit]


class _VideoNotes:
    name = "notes"
    available = True

    async def search(self, query: str, *, limit: int = 5, window_months: int = 24) -> list[NoteItem]:
        return [NoteItem(title="Kyoto in 3 days", url="https://example.com/notes/kyoto", source="notes", snippet="A walk.")]


class _Geocoder:
    """Answers `free` lookups, then blocks on the next one."""

    name = "fake-geocoder"

    def __init__(self, stop: _Stop, free: int) -> None:
        self.stop = stop
        self.free = free
        self.asked: list[str] = []
        #: The trip's own destination is looked up too (by the plan path, not the agent).
        self.known = {**KNOWN, "Kyoto": (KYOTO.lat, KYOTO.lng)}

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation | None:
        self.asked.append(name)
        if len(self.asked) > self.free:
            await self.stop.block()
        point = self.known.get(name)
        return None if point is None else GeoLocation(name=name, geo=GeoPoint(lat=point[0], lng=point[1]), source=self.name)


def _context(*preferences: str) -> TripContext:
    start = date.today() + timedelta(days=30)
    return TripContext(
        session_id="s", destination="Kyoto", start_date=start, end_date=start + timedelta(days=2), preferences=list(preferences)
    )


def _request(settings: Settings, registry: Any, context: TripContext, progress: list[dict[str, Any]]) -> AgentContext:
    return AgentContext(
        context=context,
        destination=GeoLocation(name="Kyoto", geo=KYOTO),
        date_range=DateRange(start=context.start_date, end=context.end_date),
        request_id="req_partial",
        toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_partial"),
        settings=settings,
        progress=progress,
    )


def _cut(settings: Settings, stop: _Stop, registry: Any, context: TripContext) -> AgentResult | None:
    """Run the agent until a fake blocks, then take what it has found so far."""

    async def scenario() -> AgentResult | None:
        progress: list[dict[str, Any]] = []
        agent = AttractionAgent(registry, settings)
        task = asyncio.create_task(agent.run(_request(settings, registry, context, progress)))
        await stop.reached.wait()
        result = agent.partial_result(progress, context=context, centre=KYOTO, elapsed_ms=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return result

    return asyncio.run(scenario())


def _registry(guide: _Guide, geocoder: _Geocoder, *, notes: bool = True) -> Any:
    return SimpleNamespace(places=[guide], notes=[_VideoNotes()] if notes else [], geocoder=geocoder, web=[], web_deep=[])


def _names(rows: list[dict[str, Any]]) -> list[str]:
    return [row["name"] for row in rows]


def test_a_partial_result_keeps_the_restaurants_and_notes_already_fetched(settings: Settings) -> None:
    stop = _Stop()
    registry = _registry(_Guide(stop, located=False), _Geocoder(stop, free=2))

    result = _cut(settings, stop, registry, _context())

    assert result is not None and result.partial and result.status is AgentStatus.PARTIAL
    assert _names(result.payload["places"]) == ["Kinkaku-ji", "Fushimi Inari Taisha"]
    assert sorted(_names(result.payload["meals"])) == sorted(name for name, _, _ in RESTAURANTS)
    assert [note["url"] for note in result.payload["notes"]] == ["https://example.com/notes/kyoto"]
    # Their sources are on the record too.
    urls = {ref.url for ref in result.sources}
    assert "https://example.com/notes/kyoto" in urls and "https://example.com/nishiki-ramen" in urls
    assert [(gap.kind, gap.retryable) for gap in result.failures] == [(FailureKind.TIMEOUT, True)]


def test_a_partial_result_without_any_located_place_is_still_none(settings: Settings) -> None:
    stop = _Stop()
    registry = _registry(_Guide(stop, located=False), _Geocoder(stop, free=0))

    assert _cut(settings, stop, registry, _context()) is None


def test_a_named_place_found_with_coordinates_survives_a_later_stall(settings: Settings) -> None:
    stop = _Stop()
    guide = _Guide(stop, located=True, named={"Gion Corner": GION}, blocks_on="Stall Wish")
    registry = _registry(guide, _Geocoder(stop, free=0))

    result = _cut(settings, stop, registry, _context("Gion Corner", "Stall Wish"))

    assert result is not None
    places = {row["name"]: row for row in result.payload["places"]}
    assert "Gion Corner" in places and places["Gion Corner"]["tags"][0] == "requested"
    assert _names(result.payload["places"])[0] == "Gion Corner"
    assert result.payload["wishes"]["Gion Corner"] == "Gion Corner"


def test_a_named_place_the_stall_kept_from_being_looked_up_is_reported_as_not_checked(settings: Settings) -> None:
    stop = _Stop()
    registry = _registry(_Guide(stop, located=False), _Geocoder(stop, free=2))

    result = _cut(settings, stop, registry, _context("Arashiyama Bamboo Grove"))

    assert result is not None
    assert result.payload["wishes"] == {"Arashiyama Bamboo Grove": None}
    evidence = result.payload["wish_failures"]["Arashiyama Bamboo Grove"]
    assert "timeout" in evidence and "not checked" in evidence


def test_named_places_keep_their_tag_in_the_partial_result(settings: Settings) -> None:
    stop = _Stop()
    # "Nijo Castle" is a guide place the traveller also named; "Gion Corner" is found without
    # coordinates and placed by the geocoder in the second round; the third search stalls.
    guide = _Guide(stop, located=False, named={"Gion Corner": GION_WITHOUT_COORDINATES}, blocks_on="Stall Wish")
    registry = _registry(guide, _Geocoder(stop, free=7))

    result = _cut(settings, stop, registry, _context("Nijo Castle", "Gion Corner", "Stall Wish"))

    assert result is not None
    places = {row["name"]: row for row in result.payload["places"]}
    assert places["Nijo Castle"]["tags"][0] == "requested" and places["Gion Corner"]["tags"][0] == "requested"
    assert _names(result.payload["places"])[:2] == ["Nijo Castle", "Gion Corner"]
    assert result.payload["wishes"] == {"Nijo Castle": "Nijo Castle", "Gion Corner": "Gion Corner", "Stall Wish": None}


# -- through the real runner and the real budget ------------------------------------------------


class _StallsAfter(_Geocoder):
    """Answers `free` lookups at once, then keeps the next one waiting past the budget."""

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation | None:
        if len(self.asked) >= self.free:
            self.asked.append(name)
            await asyncio.sleep(30)
        return await super().geocode(name, country_hint=country_hint)


def _runner_context() -> TripContext:
    return case_context(EvalCase(id="partial", dataset="design", input="", destination="Kyoto", days=3), "s")


def test_the_runner_keeps_the_restaurants_and_notes_of_a_cut_off_agent(settings: Settings) -> None:
    settings = settings.model_copy(update={"preplan_budget_seconds": 1.0, "agent_timeout_seconds": 30})
    stop = _Stop()
    registry = _registry(_Guide(stop, located=False), _StallsAfter(stop, free=2))
    context = _runner_context().model_copy(update={"preferences": ["Arashiyama Bamboo Grove"]})

    async def scenario() -> AgentResult:
        results = await PlanPath(planner=None, registry=registry, settings=settings).run_agents(
            context=context,
            destination=GeoLocation(name="Kyoto", geo=KYOTO),
            date_range=context.date_range(),
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_runner"),
            emit=lambda *_: None,
            names={AgentName.ATTRACTION},
        )
        return results["attraction"]

    result = asyncio.run(scenario())

    assert result.status is AgentStatus.PARTIAL
    assert len(result.payload["meals"]) == 3 and len(result.payload["notes"]) == 1
    assert "Arashiyama Bamboo Grove" in result.payload["wishes"]


def test_a_plan_from_a_cut_off_attraction_agent_has_meals_and_the_check_for_a_named_place(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    settings = settings.model_copy(update={"preplan_budget_seconds": 1.0, "agent_timeout_seconds": 30})
    stop = _Stop()
    registry = cost_sandbox(settings, "Kyoto")
    registry.places = [_Guide(stop, located=False)]
    registry.notes = []
    # The trip's own destination is the first lookup; then two places answer and the third stalls.
    registry.geocoder = _StallsAfter(stop, free=3)
    context = _runner_context().model_copy(update={"preferences": ["Arashiyama Bamboo Grove"]})

    async def scenario():
        path = PlanPath(planner=Planner(offline_gateway, settings, registry.fx), registry=registry, settings=settings)
        return await path.run(
            context=context,
            session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_runner_plan"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )

    plan = asyncio.run(scenario())

    meals = [item for _, item in plan.iter_items() if item.type.value == "meal"]
    assert meals, "the restaurants fetched before the stall are on the plan"
    check = next(check for check in plan.constraints if check.text == "Include Arashiyama Bamboo Grove")
    assert check.status.value == "unknown" and "not checked" in check.evidence
