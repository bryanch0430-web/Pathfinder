"""Proposal design behaviours DB-03 and DB-18 (Section 4, p2-p3).

DB-03: the four pre-planning agents run together inside a time budget; an agent
that overruns is cancelled, its section is marked unavailable and the plan is
still returned.
DB-18: the attraction agent turns guide notes into place records and every
record it keeps carries coordinates from a logged geocoder call.

Offline and deterministic: sandbox providers, fake agents that wait on each
other instead of the clock, and a budget of a few milliseconds.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.application import planning
from app.application.agents import AgentContext, AttractionAgent
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import BUDGET_EXCEEDED, PlanPath
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
    TripContext,
)
from app.eval_efficiency import case_context, cost_sandbox
from app.evaluation import BASE
from app.infrastructure.observability import NoopTracer, TraceRecorder
from app.infrastructure.providers.places import WikivoyagePlacesSource
from app.infrastructure.store import SQLiteStore

REQUEST_ID = "req_db03"
KYOTO = BASE["Kyoto"]


class Gate:
    """Opens once every agent has started. Agents run one after another would
    block on it until the budget ran out, so passing it proves they run together."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.started: list[str] = []
        self.opened = asyncio.Event()

    async def arrive(self, name: str) -> None:
        self.started.append(name)
        if len(self.started) == self.size:
            self.opened.set()
        await self.opened.wait()


class NeverAnswers:
    """A hotel source that starts, reaches the gate and never returns."""

    name = "slow-hotels"
    available = True

    def __init__(self, gate: Gate) -> None:
        self.gate = gate
        self.cancelled = False

    async def search(self, **_: Any) -> list:
        try:
            await self.gate.arrive("hotel")
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return []


def _source(name: str) -> list[dict[str, Any]]:
    return [{"title": name, "url": f"https://example.com/{name.lower().replace(' ', '-')}", "source": "test"}]


def _ready_result(agent: AgentName) -> AgentResult:
    if agent is AgentName.ATTRACTION:
        places = [
            {
                "id": f"p{index + 1}",
                "name": f"Stop {index + 1}",
                "category": "Attraction",
                "description": "A verified stop.",
                "geo": {"lat": KYOTO[0] + index * 0.01, "lng": KYOTO[1] + index * 0.01},
                "rating": 4.5,
                "source_refs": _source(f"Stop {index + 1}"),
            }
            for index in range(6)
        ]
        return AgentResult(agent=agent, status=AgentStatus.OK, payload={"places": places, "meals": []})
    if agent is AgentName.WEATHER:
        return AgentResult(agent=agent, status=AgentStatus.OK, payload={"days": []})
    return AgentResult(agent=agent, status=AgentStatus.OK, payload={"flights": []})


def _prompt_agents(monkeypatch: pytest.MonkeyPatch, gate: Gate) -> None:
    """Attraction, weather and ticket answer as soon as all four have started; the
    hotel agent is the real one, held up by its source."""

    def factory(agent: AgentName):
        async def run(request: Any) -> AgentResult:
            await gate.arrive(agent.value)
            return _ready_result(agent)

        return lambda registry, settings: SimpleNamespace(run=run)

    factories = {agent: factory(agent) for agent in (AgentName.ATTRACTION, AgentName.WEATHER, AgentName.TICKET)}
    factories[AgentName.HOTEL] = planning.HotelAgent
    monkeypatch.setattr(planning, "AGENT_FACTORIES", factories)


def _budget(settings: Settings) -> Settings:
    # The per-call deadline is far away, so only the pre-planning budget can stop the hotel agent.
    return settings.model_copy(update={"preplan_budget_seconds": 0.01, "agent_timeout_seconds": 30})


def _context():
    return case_context(EvalCase(id="db03", dataset="design", input="", destination="Kyoto", days=3), "s")


def _others() -> list[asyncio.Task]:
    return [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


def test_db03_agents_start_together_and_an_overrun_section_is_marked_unavailable(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _budget(settings)
    store = SQLiteStore(settings.database_path)
    tracer = TraceRecorder(REQUEST_ID, settings)

    async def scenario() -> None:
        gate = Gate(4)
        registry = cost_sandbox(settings, "Kyoto")
        slow = NeverAnswers(gate)
        registry.hotels = [slow]
        _prompt_agents(monkeypatch, gate)
        context = _context()
        events: list[tuple[str, dict]] = []
        results = await PlanPath(planner=None, registry=registry, settings=settings).run_agents(
            context=context,
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=KYOTO[0], lng=KYOTO[1])),
            date_range=context.date_range(),
            toolbox=Toolbox(path="plan", tracer=tracer, store=store, request_id=REQUEST_ID),
            emit=lambda name, data: events.append((name, data)),
        )
        # All four started before any could finish.
        assert sorted(gate.started) == ["attraction", "hotel", "ticket", "weather"]
        for name in ("attraction", "weather", "ticket"):
            assert results[name].status is AgentStatus.OK
        hotel = results["hotel"]
        assert hotel.status is AgentStatus.UNAVAILABLE
        assert hotel.payload == {}
        assert [(gap.section, gap.kind) for gap in hotel.failures] == [("hotels", FailureKind.TIMEOUT)]
        assert hotel.failures[0].retryable
        done = {data["agent"]: data for name, data in events if name == "agent" and data["phase"] == "done"}
        assert done["hotel"]["status"] == "unavailable"
        # The slow call was cancelled and nothing is left running.
        assert slow.cancelled
        assert _others() == []

    asyncio.run(scenario())

    rows = [row for row in store.tool_calls(REQUEST_ID) if row["tool"] == "hotels.search"]
    assert [(row["status"], row["error_class"]) for row in rows] == [("timeout", "timeout")]
    spans = [span for span in tracer.spans if span.name == "tool.hotels.search"]
    assert [(span.attrs["status"], span.attrs["reason"]) for span in spans] == [("timeout", BUDGET_EXCEEDED)]


def test_an_agent_that_raises_is_a_server_error_gap_without_the_error_text(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-M3/B-M6: an unexpected exception is a SERVER_ERROR (not a timeout) and its text stays out of the gap."""
    settings = _budget(settings)

    async def explodes(request: Any) -> AgentResult:
        raise RuntimeError("boom <html>secret key abc</html>")

    monkeypatch.setattr(
        planning, "AGENT_FACTORIES", {AgentName.WEATHER: lambda registry, settings: SimpleNamespace(run=explodes)}
    )

    async def scenario() -> AgentResult:
        context = _context()
        results = await PlanPath(planner=None, registry=cost_sandbox(settings, "Kyoto"), settings=settings).run_agents(
            context=context,
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=KYOTO[0], lng=KYOTO[1])),
            date_range=context.date_range(),
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id=REQUEST_ID),
            emit=lambda *_: None,
        )
        return results["weather"]

    weather = asyncio.run(scenario())
    assert weather.status is AgentStatus.UNAVAILABLE
    assert [(gap.section, gap.kind) for gap in weather.failures] == [("weather", FailureKind.SERVER_ERROR)]
    assert "secret" not in weather.failures[0].message and all("secret" not in note for note in weather.notes)


def test_db03_the_planner_still_returns_a_plan_when_an_agent_overruns(
    settings: Settings, offline_gateway: LLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _budget(settings)
    store = SQLiteStore(settings.database_path)

    async def scenario():
        gate = Gate(4)
        registry = cost_sandbox(settings, "Kyoto")
        registry.hotels = [NeverAnswers(gate)]
        _prompt_agents(monkeypatch, gate)
        path = PlanPath(planner=Planner(offline_gateway, settings, registry.fx), registry=registry, settings=settings)
        plan = await path.run(
            context=_context(),
            session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=store, request_id=REQUEST_ID),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
        assert _others() == []
        return plan

    plan = asyncio.run(scenario())
    assert len(plan.days) == 3
    attractions = [item for _, item in plan.iter_items() if item.type.value == "attraction"]
    assert attractions and all(item.geo is not None and item.source_refs for item in attractions)
    assert plan.hotels == []
    hotel_gaps = [gap for gap in plan.data_quality.gaps if gap.section == "hotels"]
    # One gap for the section, saying why: not a second "returned no usable data" one.
    assert [(gap.kind, gap.message) for gap in hotel_gaps] == [
        (FailureKind.TIMEOUT, "agent exceeded the pre-planning time budget")
    ]


def test_db03_an_agent_that_ignores_cancellation_cannot_hold_the_plan(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _budget(settings)
    monkeypatch.setattr(planning, "CANCEL_GRACE_SECONDS", 0.01)

    async def scenario() -> None:
        gate = Gate(4)
        release = asyncio.Event()

        async def stubborn(request: Any) -> AgentResult:
            await gate.arrive("weather")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()  # swallows the cancellation and keeps going
            return _ready_result(AgentName.WEATHER)

        def prompt(agent: AgentName):
            async def run(request: Any) -> AgentResult:
                await gate.arrive(agent.value)
                return _ready_result(agent)

            return lambda registry, settings: SimpleNamespace(run=run)

        monkeypatch.setattr(
            planning,
            "AGENT_FACTORIES",
            {
                AgentName.ATTRACTION: prompt(AgentName.ATTRACTION),
                AgentName.HOTEL: prompt(AgentName.HOTEL),
                AgentName.TICKET: prompt(AgentName.TICKET),
                AgentName.WEATHER: lambda registry, settings: SimpleNamespace(run=stubborn),
            },
        )
        context = _context()
        results = await PlanPath(planner=None, registry=cost_sandbox(settings, "Kyoto"), settings=settings).run_agents(
            context=context,
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=KYOTO[0], lng=KYOTO[1])),
            date_range=context.date_range(),
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id=REQUEST_ID),
            emit=lambda *_: None,
        )
        assert results["weather"].status is AgentStatus.UNAVAILABLE
        assert results["weather"].failures[0].section == "weather"
        assert all(results[name].status is AgentStatus.OK for name in ("attraction", "hotel", "ticket"))
        # The late agent is still running; let it finish so the loop closes cleanly.
        release.set()
        await asyncio.gather(*_others())

    asyncio.run(scenario())


# -- DB-18: guide notes become geocoded place records ------------------------

DB18_REQUEST = "req_db18"
KYOTO_CENTRE = GeoPoint(lat=KYOTO[0], lng=KYOTO[1])

# A travel guide naming several places: one with coordinates, the rest without;
# one place listed twice, one under two spellings, one no map knows and one a
# geocoder would put in the wrong city.
GUIDE = """== See ==
{{see | name=Kiyomizu-dera | alt=清水寺 | lat=34.9949 | long=135.7850 | content=Hilltop temple.}}
{{see | name=Fushimi Inari Taisha | alt=伏見稲荷大社 | content=Thousands of torii gates.}}
{{see | name=Kinkaku-ji | content=The golden pavilion.}}
{{see | name=Kinkaku-ji Temple | content=The golden pavilion by its pond.}}
{{see | name=Nowhere Garden | content=A garden no map knows.}}
{{see | name=Kyoto Castle Replica | content=Not in Kyoto at all.}}
== Do ==
{{do | name=Philosopher's Path | content=A canal-side walk.}}
{{do | name=Fushimi Inari Taisha | content=Hike to the summit.}}
== Eat ==
{{eat | name=Nishiki Market | lat=35.0050 | long=135.7650}}
"""

GEOCODER_KNOWS = {
    "Fushimi Inari Taisha, Kyoto": (34.9671, 135.7727),
    "Kinkaku-ji, Kyoto": (35.0394, 135.7292),
    "Kinkaku-ji Temple, Kyoto": (35.0395, 135.7293),
    "Philosopher's Path, Kyoto": (35.0270, 135.7949),
    "Kyoto Castle Replica, Kyoto": (35.6895, 139.6917),  # a match in Tokyo
}


class FakeGeocoder:
    name = "fake-geocoder"

    def __init__(self, known: dict[str, tuple[float, float]]) -> None:
        self.known = known
        self.asked: list[str] = []

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation | None:
        self.asked.append(name)
        point = self.known.get(name)
        if point is None:
            return None
        return GeoLocation(name=name, geo=GeoPoint(lat=point[0], lng=point[1]), source=self.name)


class VideoNotes:
    """Community notes that name places the guide does not list."""

    name = "fake-notes"
    available = True

    async def search(self, query: str, *, limit: int = 5, window_months: int = 24) -> list[NoteItem]:
        return [
            NoteItem(
                title="Kyoto in 3 days: Kiyomizu-dera and the Secret Bamboo Grove",
                url="https://example.com/notes/kyoto",
                source=self.name,
                snippet="We also loved Hidden Moss Temple.",
            )
        ]


class ToolLog:
    """Stands in for the store's tool log, keeping each call's arguments."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def log_tool_call(self, **row: Any) -> None:
        self.rows.append(row)

    def log_injection(self, *_: Any) -> None:
        return None


def _attractions(settings: Settings, guide_text: str, geocoder: FakeGeocoder, preferences: list[str]):
    guide = WikivoyagePlacesSource(settings)

    async def wikitext(destination: str) -> tuple[str, str]:
        return guide_text, destination

    guide._wikitext = wikitext  # the parser is real; only the fetch is replaced
    registry = SimpleNamespace(places=[guide], notes=[VideoNotes()], geocoder=geocoder, web=[], web_deep=[])
    log = ToolLog()
    start = date.today() + timedelta(days=30)
    context = TripContext(
        session_id="s",
        destination="Kyoto",
        start_date=start,
        end_date=start + timedelta(days=2),
        preferences=preferences,
    )
    request = AgentContext(
        context=context,
        destination=GeoLocation(name="Kyoto", geo=KYOTO_CENTRE),
        date_range=DateRange(start=start, end=start + timedelta(days=2)),
        request_id=DB18_REQUEST,
        toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=log, request_id=DB18_REQUEST),
        settings=settings,
    )
    return asyncio.run(AttractionAgent(registry, settings).run(request)), log


def test_db18_every_place_from_the_guide_is_geocoded_or_left_out(settings: Settings) -> None:
    geocoder = FakeGeocoder(GEOCODER_KNOWS)
    result, log = _attractions(settings, GUIDE, geocoder, ["must-see: Fushimi Inari Taisha"])

    assert result.status is AgentStatus.OK
    places = {row["name"]: row for row in result.payload["places"]}
    # The duplicates merged, the unlocatable and wrongly located places were left
    # out, and nothing the community notes merely mention became a place.
    assert set(places) == {"Kiyomizu-dera", "Fushimi Inari Taisha", "Kinkaku-ji", "Philosopher's Path"}
    assert "Kinkaku-ji Temple" in places["Kinkaku-ji"]["aliases"]
    assert places["Fushimi Inari Taisha"]["tags"][0] == "requested"
    assert result.payload["wishes"] == {"Fushimi Inari Taisha": "Fushimi Inari Taisha"}

    geocodes = [row for row in log.rows if row["tool"] == "geocode"]
    # Every geocoder call went through the tool log.
    assert [row["args"]["name"] for row in geocodes] == geocoder.asked
    assert all(row["status"] == "ok" for row in geocodes)
    logged = {row["args"]["name"] for row in geocodes}
    for name, row in places.items():
        point = (row["geo"]["lat"], row["geo"]["lng"])
        assert row["source_refs"], name
        if name == "Kiyomizu-dera":
            # The guide's own coordinates: no geocoder call was needed.
            assert point == (34.9949, 135.785)
            assert f"{name}, Kyoto" not in logged
        else:
            # Coordinates exactly as a logged geocoder call returned them.
            assert f"{name}, Kyoto" in logged
            assert point == GEOCODER_KNOWS[f"{name}, Kyoto"]
    # The place listed twice and asked for by name was geocoded once.
    assert geocoder.asked.count("Fushimi Inari Taisha, Kyoto") == 1
    assert "Nowhere Garden, Kyoto" in geocoder.asked and "Kyoto Castle Replica, Kyoto" in geocoder.asked
    assert any("Nowhere Garden" in note and "Kyoto Castle Replica" in note for note in result.notes)
    # The community note is kept as a note, not turned into place records.
    assert [note["url"] for note in result.payload["notes"]] == ["https://example.com/notes/kyoto"]


def test_db18_a_guide_whose_places_cannot_be_located_leaves_a_data_gap(settings: Settings) -> None:
    guide = """== See ==
{{see | name=Nowhere Garden | content=A garden no map knows.}}
{{see | name=Kyoto Castle Replica | content=Not in Kyoto at all.}}
"""
    geocoder = FakeGeocoder({"Kyoto Castle Replica, Kyoto": GEOCODER_KNOWS["Kyoto Castle Replica, Kyoto"]})
    result, log = _attractions(settings, guide, geocoder, ["must-see: Nowhere Garden"])

    assert result.status is AgentStatus.UNAVAILABLE
    assert result.payload["places"] == []
    # A wish the guide names but no map can place is not reported as found.
    assert result.payload["wishes"] == {"Nowhere Garden": None}
    gaps = [gap for gap in result.failures if gap.section == "attractions"]
    assert [gap.kind for gap in gaps] == [FailureKind.NO_DATA]
    assert "none could be located" in gaps[0].message
    assert len([row for row in log.rows if row["tool"] == "geocode"]) == len(geocoder.asked) >= 2
