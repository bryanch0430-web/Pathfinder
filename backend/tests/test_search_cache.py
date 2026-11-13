"""Hotel and flight results kept for a session, so an edit or a second plan pays only for what changed."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from multi_city_rig import multi_city_rig, route_context

from app.application.search_cache import SearchCache, search_key
from app.config import Settings
from app.eval_metering import RunLedger

NOW = datetime(2027, 1, 1, 12, 0, tzinfo=UTC)
HOTEL = {"destination": "Kyoto", "check_in": NOW.date(), "check_out": NOW.date() + timedelta(days=2), "party_size": 2, "currency": "hkd"}


def test_results_kept_a_moment_ago_are_read_back_and_other_questions_miss() -> None:
    cache = SearchCache(None, "s", clock=lambda: NOW)
    key = search_key("hotels.search", HOTEL)
    cache.put("hotels", key, [{"name": "Inn"}])

    assert cache.get("hotels", key) == [{"name": "Inn"}]
    assert cache.get("hotels", search_key("hotels.search", {**HOTEL, "check_out": NOW.date() + timedelta(days=3)})) is None
    assert cache.get("hotels", search_key("hotels.search", {**HOTEL, "destination": "Osaka"})) is None
    assert cache.get("flights", key) is None


def test_the_place_and_the_currency_are_not_case_sensitive() -> None:
    assert search_key("hotels.search", HOTEL) == search_key("hotels.search", {**HOTEL, "destination": " KYOTO ", "currency": "HKD"})


def test_results_older_than_six_hours_are_ignored() -> None:
    clock = [NOW]
    cache = SearchCache(None, "s", clock=lambda: clock[0])
    key = search_key("hotels.search", HOTEL)
    cache.put("hotels", key, [{"name": "Inn"}])

    clock[0] = NOW + timedelta(hours=5, minutes=59)
    assert cache.get("hotels", key) is not None
    clock[0] = NOW + timedelta(hours=7)
    assert cache.get("hotels", key) is None


def test_a_round_trip_flight_search_is_not_kept() -> None:
    flight = {"origin": "HKG", "destination": "KIX", "depart_date": NOW.date(), "party_size": 1, "currency": "HKD"}

    assert search_key("flights.search", {**flight, "return_date": None}) is not None
    assert search_key("flights.search", {**flight, "return_date": NOW.date()}) is None
    assert search_key("places.search", flight) is None


def test_the_store_keeps_them_per_session_and_forgets_them_with_the_session() -> None:
    ledger = RunLedger()
    try:
        first, second = SearchCache(ledger.store, "s1"), SearchCache(ledger.store, "s2")
        key = search_key("hotels.search", HOTEL)
        first.put("hotels", key, [{"name": "Inn"}])

        assert first.get("hotels", key) == [{"name": "Inn"}] and second.get("hotels", key) is None
        ledger.store.delete_session("s1")
        assert first.get("hotels", key) is None
    finally:
        ledger.close()


def _searches(ledger: RunLedger) -> int:
    return sum(1 for call in ledger.store.tool_calls() if call["tool"] in ("hotels.search", "flights.search"))


def test_the_second_plan_of_the_same_route_in_a_session_searches_nothing(settings: Settings) -> None:
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8, "enable_flights": True})

    async def main():
        ledger = RunLedger()
        try:
            rig = multi_city_rig(settings, ledger)
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-cache")
            ledger.store.save_context(route_context(session.id, [("Kyoto", 2), ("Osaka", 2)]))
            _ = [e async for e in rig.turns.handle_turn(session_id=session.id, message="Plan this trip")]
            first = _searches(ledger)
            first_plan = ledger.store.get_plan(session.id)
            _ = [e async for e in rig.turns.handle_turn(session_id=session.id, message="Plan this trip")]
            second_plan = ledger.store.get_plan(session.id)
            return first, _searches(ledger) - first, first_plan, second_plan
        finally:
            ledger.close()

    first, second, first_plan, second_plan = asyncio.run(main())

    assert first == 4  # two hotels and the two flights to and from home (Kyoto to Osaka is ground)
    assert second == 0
    assert [s.name for s in second_plan.hotels] == [s.name for s in first_plan.hotels]
    assert [t.id for t in second_plan.tickets] == [t.id for t in first_plan.tickets]
