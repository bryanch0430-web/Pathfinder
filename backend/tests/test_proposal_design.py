"""Proposal design behaviours DB-08, DB-10, DB-13, DB-15 and DB-16 (Section 4, p3).

Offline: sandbox providers, and the metered writer where a model reply matters.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from pydantic import BaseModel

from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.application.validation import repair_plan
from app.config import Settings
from app.domain.errors import PlanInvalidError, ProviderError
from app.domain.models import AgentName, EvalCase, FailureKind, GeoPoint, HotelCandidate, Money, PriceKind
from app.eval_efficiency import accepted_plan, build_rig, case_context, cost_sandbox
from app.eval_metering import MeteredLLM, RunLedger
from app.evaluation import BASE, _ref
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer

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
