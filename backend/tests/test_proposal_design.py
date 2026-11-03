"""Proposal design behaviours DB-08, DB-10, DB-13, DB-15 and DB-16 (Section 4, p3).

Offline: sandbox providers, and the metered writer where a model reply matters.
"""

from __future__ import annotations

import asyncio
import threading

from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import AgentName, EvalCase, GeoPoint, HotelCandidate, Money, PriceKind
from app.eval_efficiency import build_rig, case_context, cost_sandbox
from app.eval_metering import RunLedger
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
