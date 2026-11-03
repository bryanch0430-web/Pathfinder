"""Proposal design behaviours DB-08, DB-10, DB-13, DB-15 and DB-16 (Section 4, p3).

Offline: sandbox providers, and the metered writer where a model reply matters.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.application.freshness import FreshnessService
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.application.validation import repair_plan
from app.config import Settings
from app.domain.errors import PlanInvalidError, ProviderError
from app.domain.models import (
    AgentName,
    EvalCase,
    FailureKind,
    GeoPoint,
    HotelCandidate,
    Money,
    PriceKind,
    utcnow,
)
from app.eval_efficiency import accepted_plan, build_rig, case_context, cost_sandbox
from app.eval_metering import MeteredLLM, RunLedger
from app.evaluation import BASE, _ref
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.interface.api import create_app

PREFERENCE_MESSAGE = "We are vegetarian and love museums."


def _session(ledger: RunLedger, client_token: str, destination: str = "Kyoto") -> str:
    session = ledger.store.create_session(client_token)
    case = EvalCase(id="t", dataset="design", input="", destination=destination)
    ledger.store.save_context(case_context(case, session.id))
    return session.id


async def _events(rig, session_id: str, message: str) -> list:
    return [event async for event in rig.turns.handle_turn(session_id=session_id, message=message)]


def _kyoto_context(session_id: str = "s"):
    return case_context(EvalCase(id="t", dataset="design", input="", destination="Kyoto"), session_id)


NOT_A_PLAN = json.dumps({"not": "a trip plan"})


# ---------------------------------------------------------------------------
# DB-08: an invalid plan gets a bounded number of repairs, then an error
# ---------------------------------------------------------------------------


def test_db08_invalid_plan_gets_at_most_max_repairs_then_is_rejected(settings: Settings) -> None:
    """Proposal DB-08 (p3 l.99-103): the repair loop is bounded and never returns an invalid plan."""
    plan = asyncio.run(accepted_plan(settings, _kyoto_context()))
    llm = MeteredLLM(replies={"plan.repair": NOT_A_PLAN})
    gateway = LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)
    with pytest.raises(PlanInvalidError) as failure:
        asyncio.run(repair_plan(plan, ["day 1: an item has no source"], gateway, settings))
    assert settings.max_repairs == 2
    assert llm.purposes == ["plan.repair"] * settings.max_repairs
    assert failure.value.retryable is True
    assert "bounded repair" in str(failure.value)


def test_db08_a_repair_that_returns_a_valid_plan_is_accepted(settings: Settings) -> None:
    """Proposal DB-08 (p3 l.99-103): a schema-valid, grounded repair ends the loop."""
    plan = asyncio.run(accepted_plan(settings, _kyoto_context()))
    llm = MeteredLLM(replies={"plan.repair": plan.model_dump_json()})
    gateway = LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)
    repaired = asyncio.run(repair_plan(plan, ["day 1: an item has no source"], gateway, settings))
    assert llm.purposes == ["plan.repair"]
    assert repaired.trip_id == plan.trip_id
    assert [item.name for day in repaired.days for item in day.items] == [
        item.name for day in plan.days for item in day.items
    ]


def test_db08_unrepairable_plan_turn_shows_a_retryable_error_and_saves_no_plan(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proposal DB-08 (p3 l.99-103): the console shows an error and asks to retry; no plan is kept."""
    monkeypatch.setattr("app.application.planning.validate_plan", lambda plan, settings: ["forced schema error"])
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        rig.llm.replies["plan.repair"] = NOT_A_PLAN
        rig.llm.route = {"intent": "plan", "confidence": 0.95}
        session_id = _session(ledger, "guest-db08")
        events = asyncio.run(_events(rig, session_id, "Plan the Kyoto trip now."))
        saved = ledger.store.get_plan(session_id)
    finally:
        ledger.close()
    names = [event.event for event in events]
    errors = [event.data for event in events if event.event == "error"]
    assert "plan" not in names
    assert len(errors) == 1 and errors[0]["code"] == "plan_invalid" and errors[0]["retryable"] is True
    assert rig.llm.purposes.count("plan.repair") == settings.max_repairs
    assert saved is None


# ---------------------------------------------------------------------------
# DB-10: a malformed request gets a structured error and two repair chances
# ---------------------------------------------------------------------------


def test_db10_malformed_tool_request_gets_a_structured_error(settings: Settings) -> None:
    """Proposal DB-10 (p3 l.120-122): the adapter answers a malformed tool request with a structured error."""
    ledger = RunLedger()
    try:
        registry = ledger.instrument(cost_sandbox(settings, "Kyoto"))
        toolbox = Toolbox(path="plan", tracer=NoopTracer(), store=ledger.store, request_id="r")
        with pytest.raises(ProviderError) as missing:
            asyncio.run(toolbox.call("places.search", registry.places[0].search, destination="Kyoto"))
        with pytest.raises(ProviderError) as unknown:
            asyncio.run(toolbox.call("geocode", registry.geocoder.geocode, name="Kyoto", radius_km=5))
        rows = ledger.store.tool_calls("r")
        made = ledger.made_total
    finally:
        ledger.close()
    for failure in (missing.value, unknown.value):
        error = failure.as_dict()
        assert (error["kind"], error["retryable"], error["code"]) == ("invalid_request", False, "provider_error")
    assert "geo" in str(missing.value) and "radius_km" in str(unknown.value)
    assert made == 0  # the provider was never called with the malformed request
    assert [(row["tool"], row["status"], row["error_class"]) for row in rows] == [
        ("places.search", "invalid", "invalid_request"),
        ("geocode", "invalid", "invalid_request"),
    ]


class _Sequenced(MeteredLLM):
    """Returns the scripted answers in order, then repeats the last one."""

    def __init__(self, answers: list[str]):
        super().__init__()
        self.answers = list(answers)

    def reply(self, purpose: str) -> str:
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class _SearchRequest(BaseModel):
    query: str
    count: int


def _gateway(settings: Settings, llm: MeteredLLM) -> LLMGateway:
    return LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)


def test_db10_malformed_model_request_gets_two_repairs_then_a_structured_error(settings: Settings) -> None:
    """Proposal DB-10 (p3 l.120-122): invalid JSON gets up to two repair chances, then a typed error."""
    llm = _Sequenced(["this is not json"])
    with pytest.raises(ProviderError) as failure:
        asyncio.run(_gateway(settings, llm).json("modify", user="Swap the hotel.", purpose="modify.extract"))
    assert llm.purposes == ["modify.extract"] + ["modify.extract.repair"] * 2
    assert failure.value.kind == FailureKind.PARSE
    assert failure.value.as_dict()["kind"] == "parse"


def test_db10_request_with_missing_fields_gets_two_repairs(settings: Settings) -> None:
    """Proposal DB-10 (p3 l.120-122): a schema miss is repaired too, and the repaired request is used."""
    llm = _Sequenced(['{"query": "temples"}', '{"count": 3}', '{"query": "temples", "count": 3}'])
    data = asyncio.run(
        _gateway(settings, llm).json("ask", user="Find temples.", schema=_SearchRequest, purpose="ask.search")
    )
    assert data == {"query": "temples", "count": 3}
    assert llm.purposes == ["ask.search", "ask.search.repair", "ask.search.repair"]

    stuck = _Sequenced(['{"query": "temples"}'])
    with pytest.raises(ProviderError) as failure:
        asyncio.run(_gateway(settings, stuck).json("ask", user="Find temples.", schema=_SearchRequest, purpose="ask.search"))
    assert len(stuck.purposes) == 1 + settings.max_repairs
    assert failure.value.kind == FailureKind.PARSE and "_SearchRequest" in str(failure.value)


# ---------------------------------------------------------------------------
# DB-13: time-sensitive data carry timestamps and are refreshed when outdated
# ---------------------------------------------------------------------------


def test_db13_time_sensitive_data_carry_timestamps(settings: Settings) -> None:
    """Proposal DB-13 (p3 l.125-128): forecasts, fares, stays and stops are stamped when fetched."""
    before = utcnow()
    plan = asyncio.run(accepted_plan(settings, _kyoto_context()))
    after = utcnow()
    stamps = [day.weather.fetched_at for day in plan.days if day.weather is not None]
    assert len(stamps) == len(plan.days)
    stamps += [ticket.fetched_at for ticket in plan.tickets] + [hotel.fetched_at for hotel in plan.hotels]
    stamps += [item.fetched_at for day in plan.days for item in day.items]
    assert plan.tickets and plan.hotels
    assert all(stamp.tzinfo is not None and before <= stamp <= after for stamp in stamps)


def test_db13_outdated_forecast_is_flagged_and_only_the_weather_tool_is_called_again(settings: Settings) -> None:
    """Proposal DB-13 (p3 l.125-128): an outdated forecast is refreshed by its own agent only."""
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session_id = _session(ledger, "guest-db13")
        plan = asyncio.run(accepted_plan(settings, ledger.store.get_context(session_id)))
        aged = utcnow() - timedelta(seconds=settings.weather_fresh_seconds + 600)
        for day in plan.days:
            day.weather.fetched_at = aged
        ledger.store.save_plan(plan, "Accepted plan")
        rig.llm.route = {"intent": "ask", "confidence": 0.95}
        trace = {}
        for event in asyncio.run(_events(rig, session_id, "What will the weather be like on day 1?")):
            if event.event == "trace":
                trace = event.data
        called = ledger.logged_tools(trace["request_id"])
        stored = ledger.store.get_plan(session_id)
    finally:
        ledger.close()
    assert called == {"weather.forecast": 1}
    assert [(mark.section, mark.status, mark.fetched_at) for mark in stored.data_quality.freshness] == [
        ("weather", "refreshed", aged)
    ]
    assert all(day.weather.fetched_at > aged for day in stored.days)


ASK_WEATHER = "What will the weather be like on day 1?"


def _ask_on_saved_plan(settings: Settings, age, *, clock=None):
    """One ask turn on an accepted Kyoto plan that ``age`` edited before it was saved."""
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        if clock is not None:
            rig.turns.freshness.clock = clock
        session_id = _session(ledger, "guest-db13")
        plan = asyncio.run(accepted_plan(settings, ledger.store.get_context(session_id)))
        age(plan)
        ledger.store.save_plan(plan, "Accepted plan")
        rig.llm.route = {"intent": "ask", "confidence": 0.95}
        events = asyncio.run(_events(rig, session_id, ASK_WEATHER))
        trace = next(event.data for event in events if event.event == "trace")
        called = ledger.logged_tools(trace["request_id"])
        stored = ledger.store.get_plan(session_id)
    finally:
        ledger.close()
    return plan, stored, called, events


def _dump(rows) -> list:
    return [row.model_dump(mode="json") for row in rows]


def test_db13_outdated_fares_are_refreshed_by_the_flight_tool_only(settings: Settings) -> None:
    """Proposal DB-13 (p3 l.125-128): an outdated fare is re-fetched; forecast and stay are untouched."""
    aged = utcnow() - timedelta(seconds=settings.ticket_fresh_seconds + 600)

    def age(plan) -> None:
        for ticket in plan.tickets:
            ticket.fetched_at = aged

    before, after, called, events = _ask_on_saved_plan(settings, age)
    assert called == {"flights.search": 1}
    assert after.version == before.version + 1
    assert [(mark.section, mark.status) for mark in after.data_quality.freshness] == [("tickets", "refreshed")]
    assert after.tickets and all(ticket.fetched_at > aged for ticket in after.tickets)
    assert [ticket.price for ticket in after.tickets] == [ticket.price for ticket in before.tickets]
    assert _dump(day.weather for day in after.days) == _dump(day.weather for day in before.days)
    assert _dump(after.hotels) == _dump(before.hotels)
    assert _dump(item for _, item in after.iter_items()) == _dump(item for _, item in before.iter_items())
    shown = next(event.data["plan"] for event in events if event.event == "plan")
    assert shown["version"] == after.version and shown["data_quality"]["freshness"][0]["section"] == "tickets"


def test_db13_outdated_hotel_prices_are_refreshed_by_the_hotel_tool_only(settings: Settings) -> None:
    """Proposal DB-13 (p3 l.125-128): an outdated nightly price is replaced by the source's current one."""
    aged = utcnow() - timedelta(seconds=settings.hotel_fresh_seconds + 600)
    quoted: list = []

    def age(plan) -> None:
        for hotel in plan.hotels:
            quoted.append(hotel.price_per_night)
            hotel.price_per_night = Money(amount=1.0, currency=hotel.price_per_night.currency)
            hotel.fetched_at = aged

    before, after, called, _ = _ask_on_saved_plan(settings, age)
    assert called == {"hotels.search": 1}
    assert [(mark.section, mark.status) for mark in after.data_quality.freshness] == [("hotels", "refreshed")]
    assert [hotel.price_per_night for hotel in after.hotels] == quoted
    assert all(hotel.fetched_at > aged for hotel in after.hotels)
    assert [hotel.id for hotel in after.hotels] == [hotel.id for hotel in before.hotels]
    # The cost follows the re-fetched price (the accepted plan's cost was built from it).
    assert after.cost.model_dump() == before.cost.model_dump()
    assert _dump(after.tickets) == _dump(before.tickets)
    assert _dump(day.weather for day in after.days) == _dump(day.weather for day in before.days)


def test_db13_fresh_data_is_not_fetched_again(settings: Settings) -> None:
    """Proposal DB-13 (p3 l.125-128): inside every window nothing is re-fetched or re-versioned."""
    assert settings.weather_fresh_seconds < settings.ticket_fresh_seconds < settings.hotel_fresh_seconds
    almost = timedelta(seconds=settings.weather_fresh_seconds - 60)
    before, after, called, events = _ask_on_saved_plan(settings, lambda plan: None, clock=lambda: utcnow() + almost)
    assert called == {}
    assert after.model_dump(mode="json") == before.model_dump(mode="json")
    assert not any(event.event == "plan" for event in events)


def test_db13_the_injected_clock_decides_which_section_is_outdated(settings: Settings) -> None:
    """Proposal DB-13 (p3 l.125-128): just past the forecast window only the forecast is re-fetched."""
    later = timedelta(seconds=settings.weather_fresh_seconds + 60)
    before, after, called, _ = _ask_on_saved_plan(settings, lambda plan: None, clock=lambda: utcnow() + later)
    assert called == {"weather.forecast": 1}
    assert [(mark.section, mark.status) for mark in after.data_quality.freshness] == [("weather", "refreshed")]
    assert _dump(after.tickets) == _dump(before.tickets) and _dump(after.hotels) == _dump(before.hotels)


class _DownWeather:
    name = "down-weather"

    async def forecast(self, geo, start, end):
        raise ProviderError("forecast source unavailable", kind=FailureKind.SERVER_ERROR, source=self.name)


def test_db13_a_section_its_source_cannot_refresh_stays_flagged_outdated(settings: Settings) -> None:
    """Proposal DB-13 (p3 l.125-128): a failed refresh keeps the earlier forecast and flags it."""
    plan = asyncio.run(accepted_plan(settings, _kyoto_context()))
    aged = utcnow() - timedelta(seconds=settings.weather_fresh_seconds + 600)
    for day in plan.days:
        day.weather.fetched_at = aged
    registry = cost_sandbox(settings, "Kyoto")
    registry.weather = [_DownWeather()]
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    service = FreshnessService(registry=registry, planner=Planner(gateway, settings, registry.fx), settings=settings)
    outcome = asyncio.run(service.check(plan, context=None, tracer=NoopTracer(), request_id="req_db13"))
    assert outcome.outdated == ["weather"] and outcome.refreshed == []
    [mark] = outcome.plan.data_quality.freshness
    assert (mark.section, mark.status, mark.fetched_at) == ("weather", "outdated", aged)
    assert "earlier forecast is kept" in mark.note
    assert outcome.plan.version == plan.version
    assert _dump(day.weather for day in outcome.plan.days) == _dump(day.weather for day in plan.days)


def test_db13_a_reopened_plan_has_its_outdated_hotel_prices_refreshed(settings: Settings) -> None:
    """Proposal DB-13 (p3 l.125-128): opening a saved plan re-fetches its outdated section, once."""
    aged = utcnow() - timedelta(seconds=settings.hotel_fresh_seconds + 600)
    with TestClient(create_app(settings)) as client:
        container = client.app.state.container
        container.turn_service.freshness.registry = cost_sandbox(settings, "Kyoto")
        session_id = client.post("/api/journeys", json={"client_token": "guest-db13-reopen"}).json()["session"]["id"]
        context = _kyoto_context(session_id)
        container.store.save_context(context)
        plan = asyncio.run(accepted_plan(settings, context))
        for hotel in plan.hotels:
            hotel.fetched_at = aged
        container.store.save_plan(plan, "Accepted plan")
        opened = client.get(f"/api/journeys/{session_id}").json()
        again = client.get(f"/api/journeys/{session_id}/itinerary").json()["plan"]
        tools = [row["tool"] for row in container.store.tool_calls()]
    assert tools == ["hotels.search"]
    assert opened["plan"]["version"] == again["version"] == plan.version + 1
    assert [(mark["section"], mark["status"]) for mark in again["data_quality"]["freshness"]] == [("hotels", "refreshed")]
    assert sorted(version["version"] for version in opened["versions"]) == [1, 2]


# ---------------------------------------------------------------------------
# DB-15: session preferences reach long-term memory through a background write
# ---------------------------------------------------------------------------


def test_db15_preferences_are_kept_in_session_and_written_to_long_term_memory(settings: Settings) -> None:
    """Proposal DB-15 (p3 l.118-119): preferences stay in the session and reach the long-term store."""
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session_id = _session(ledger, "guest-db15")
        rig.llm.route = {"intent": "unclear", "confidence": 0.95}

        async def scenario() -> list:
            events = await _events(rig, session_id, PREFERENCE_MESSAGE)
            await rig.turns.drain_background()
            return events

        events = asyncio.run(scenario())
        in_session = ledger.store.get_context(session_id).preferences
        profile = ledger.store.get_preferences("guest-db15")
    finally:
        ledger.close()
    assert "error" not in {event.event for event in events}
    assert {"vegetarian", "museums"} <= set(in_session)
    assert profile is not None and {"vegetarian", "museums"} <= set(profile.preferences)


def test_db15_long_term_preference_write_runs_in_the_background(settings: Settings) -> None:
    """Proposal DB-15 (p3 l.118-119): the turn finishes before the long-term write does."""
    ledger = RunLedger()
    release = threading.Event()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session_id = _session(ledger, "guest-db15-bg")
        rig.llm.route = {"intent": "unclear", "confidence": 0.95}
        write = rig.turns.memory.remember_preferences

        def slow_write(client_token, context):
            release.wait(timeout=5)
            return write(client_token, context)

        rig.turns.memory.remember_preferences = slow_write

        async def scenario() -> tuple[list, object]:
            events = await _events(rig, session_id, PREFERENCE_MESSAGE)
            stored_when_turn_ended = ledger.store.get_preferences("guest-db15-bg")
            release.set()
            await rig.turns.drain_background()
            return events, stored_when_turn_ended

        events, stored_when_turn_ended = asyncio.run(scenario())
        profile = ledger.store.get_preferences("guest-db15-bg")
    finally:
        release.set()
        ledger.close()
    assert [event.event for event in events][-1] == "done"
    assert stored_when_turn_ended is None
    assert profile is not None and "vegetarian" in profile.preferences


def test_db15_a_failed_long_term_write_does_not_break_the_turn(settings: Settings) -> None:
    """Proposal DB-15 (p3 l.118-119): the background write is isolated from the traveller's turn."""
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session_id = _session(ledger, "guest-db15-fail")
        rig.llm.route = {"intent": "unclear", "confidence": 0.95}

        def broken_write(client_token, context):
            raise RuntimeError("long-term store unavailable")

        rig.turns.memory.remember_preferences = broken_write

        async def scenario() -> list:
            events = await _events(rig, session_id, PREFERENCE_MESSAGE)
            await rig.turns.drain_background()
            return events

        names = [event.event for event in asyncio.run(scenario())]
        in_session = ledger.store.get_context(session_id).preferences
    finally:
        ledger.close()
    assert "error" not in names and "clarify" in names
    assert "vegetarian" in in_session


# ---------------------------------------------------------------------------
# DB-16: the hotel agent ranks candidates by rating and distance
# ---------------------------------------------------------------------------


class _RankedHotels:
    """Kyoto candidates that a rating-only or a distance-only sort would order differently."""

    name = "ranked-hotels"

    async def search(self, **_: object) -> list[HotelCandidate]:
        lat, lng = BASE["Kyoto"]

        def hotel(name: str, rating: float | None, north_deg: float, price: float) -> HotelCandidate:
            return HotelCandidate(
                name=name,
                geo=GeoPoint(lat=lat + north_deg, lng=lng),
                nightly_price=Money(amount=price, currency="USD"),
                price_kind=PriceKind.REFERENCE,
                rating=rating,
                review_count=100,
                source_refs=[_ref(name)],
            )

        return [
            hotel("Far Grand", 4.9, 0.045, 200),  # best rating, ~5 km out
            hotel("No Rating Lodge", None, 0.001, 60),
            hotel("Central Inn", 4.5, 0.004, 150),  # ~0.45 km
            hotel("Central Budget", 4.5, 0.004, 90),  # same score, cheaper
        ]


def _hotel_plan_path(settings: Settings):
    registry = cost_sandbox(settings, "Kyoto")
    registry.hotels = [_RankedHotels()]
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    return registry, PlanPath(planner=Planner(gateway, settings, registry.fx), registry=registry, settings=settings)


def test_db16_hotel_candidates_are_ranked_by_rating_and_distance(settings: Settings) -> None:
    """Proposal DB-16 (p3 l.93): rating minus a distance penalty, then the lower price on a tie."""
    registry, plan_path = _hotel_plan_path(settings)
    context = case_context(EvalCase(id="t", dataset="design", input="", destination="Kyoto"), "s")

    async def scenario():
        destination = await registry.geocoder.geocode("Kyoto")
        return await plan_path.run_agents(
            context=context,
            destination=destination,
            date_range=context.date_range(),
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t"),
            emit=lambda *_: None,
            names={AgentName.HOTEL},
        )

    hotels = asyncio.run(scenario())["hotel"].payload["hotels"]
    assert [row["name"] for row in hotels] == ["Central Budget", "Central Inn", "Far Grand", "No Rating Lodge"]
    distances = {row["name"]: row["distance_to_centre_km"] for row in hotels}
    assert distances["Central Inn"] < 1 < 4 < distances["Far Grand"] < 6


def test_db16_the_plan_stays_at_the_top_ranked_hotel(settings: Settings) -> None:
    """Proposal DB-16 (p3 l.93, l.96): the itinerary uses the hotel agent's first-ranked candidate."""
    _, plan_path = _hotel_plan_path(settings)
    context = case_context(EvalCase(id="t", dataset="design", input="", destination="Kyoto"), "s")
    plan = asyncio.run(
        plan_path.run(
            context=context,
            session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    assert plan.hotels and plan.hotels[0].name == "Central Budget"
