"""Plan display language: set_language, unsupported, refresh and Chinese input.

Language is a display setting, not a plan edit: switching it calls no planner,
no tool and no model, saves no plan version and leaves every stop id, time and
source as it was. Each turn test runs through the turn service on the offline
rig the efficiency evaluation uses, with a metered writer and counted tools.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from app.application.language import CHINESE_INPUT_REPLY, DISCLAIMER, cuisine_slot, language_slot
from app.application.modification import _search_extra, _stop_example
from app.application.router import RuleRouter, System1Router
from app.config import Settings
from app.domain.models import EvalCase, Intent, TripContext, TripPlan, utcnow
from app.eval_efficiency import accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger

PLAN_SUMMARY = {"trip_id": "x", "destination": "Kyoto", "days": []}


def _context() -> TripContext:
    return case_context(EvalCase(id="lang", dataset="efficiency", input="", destination="Kyoto"), "s")


def _rule(message: str, *, plan: bool = True):
    return RuleRouter().route(
        message=message, context=_context(), plan_summary=PLAN_SUMMARY if plan else None, history=[]
    )


def _turn(
    settings: Settings,
    message: str,
    *,
    select_meal: bool = False,
    plan_locale: str = "en",
    stale: bool = False,
) -> dict[str, Any]:
    """One real turn on a session that already holds an accepted Kyoto plan."""
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("")
        case = EvalCase(id="lang", dataset="efficiency", input=message, destination="Kyoto")
        context = case_context(case, session.id).model_copy(update={"plan_locale": plan_locale})
        ledger.store.save_context(context)
        plan = asyncio.run(accepted_plan(settings, context))
        ledger.store.save_plan(plan, "Accepted plan")
        before = ledger.store.get_plan(session.id)
        versions_before = ledger.store.list_versions(session.id)
        scope = None
        if select_meal:
            scope = next(item.id for day in plan.days for item in day.items if item.type.value == "meal")

        paths: list[str] = []

        def forbid(name: str):
            async def run(*_: Any, **__: Any):
                paths.append(name)
                raise AssertionError(f"{name} must not run")

            return run

        rig.plan_path.run = forbid("plan")  # type: ignore[method-assign]
        rig.modify_path.run = forbid("modify")  # type: ignore[method-assign]
        rig.turns.ask_path.run = forbid("ask")  # type: ignore[method-assign]
        checks: list[str] = []
        check = rig.turns.freshness.check

        async def spy(*args: Any, **kwargs: Any):
            checks.append("check")
            return await check(*args, **kwargs)

        rig.turns.freshness.check = spy  # type: ignore[method-assign]
        if stale:
            rig.turns.freshness.clock = lambda: utcnow() + timedelta(days=30)
        made_before = ledger.made.copy()
        events: list = []

        async def run() -> None:
            async for event in rig.turns.handle_turn(
                session_id=session.id, message=message, scope_item_id=scope, scope_label="Day 1 · lunch" if scope else None
            ):
                events.append(event)

        asyncio.run(run())
        trace = next(event.data for event in events if event.event == "trace")
        reply = [m for m in ledger.store.list_messages(session.id, limit=20) if m.role == "assistant"][-1]
        return {
            "events": events,
            "route": next(event.data for event in events if event.event == "route"),
            "locale_events": [event.data for event in events if event.event == "locale"],
            "purposes": list(rig.llm.purposes),
            "made": ledger.made - made_before,
            "logged": [row["tool"] for row in ledger.store.tool_calls(trace["request_id"])],
            "paths": paths,
            "checks": checks,
            "reply": reply.content,
            "before": before,
            "after": ledger.store.get_plan(session.id),
            "versions_before": versions_before,
            "versions_after": ledger.store.list_versions(session.id),
            "context": ledger.store.get_context(session.id),
            "errors": [event.data for event in events if event.event == "error"],
        }
    finally:
        ledger.close()


def _assert_untouched(result: dict[str, Any]) -> None:
    """No model, no tool, no planner, no stop edit, no new version: plan diff = 0."""
    assert result["errors"] == []
    assert result["purposes"] == [], "no writer, router or planner model call"
    assert sum(result["made"].values()) == 0 and result["logged"] == [], "no tool call"
    assert result["paths"] == []
    assert result["versions_after"] == result["versions_before"]
    before: TripPlan = result["before"]
    after: TripPlan = result["after"]
    assert after.version == before.version
    assert after.model_dump(mode="json") == before.model_dump(mode="json")
    stops = lambda plan: [(i.id, i.start, i.end, [s.model_dump(mode="json") for s in i.source_refs]) for d in plan.days for i in d.items]  # noqa: E731
    assert stops(after) == stops(before)


# --- router table -------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "intent", "language"),
    [
        ("Make it in Traditional Chinese", Intent.SET_LANGUAGE, "zh-Hant"),
        ("Show the plan in Traditional Chinese please", Intent.SET_LANGUAGE, "zh-Hant"),
        ("繁體中文", Intent.SET_LANGUAGE, "zh-Hant"),
        ("用中文顯示", Intent.SET_LANGUAGE, "zh-Hant"),
        ("Switch back to English", Intent.SET_LANGUAGE, "en"),
        ("English please", Intent.SET_LANGUAGE, "en"),
        ("Translate to Japanese", Intent.UNSUPPORTED, "other"),
        ("in French", Intent.UNSUPPORTED, "other"),
        ("Show it in Simplified Chinese", Intent.UNSUPPORTED, "other"),
        ("简体中文", Intent.UNSUPPORTED, "other"),
    ],
)
def test_lang_router_table_language_rows(message: str, intent: Intent, language: str) -> None:
    decision = _rule(message)
    assert decision.intent == intent
    assert decision.slots["language"] == language
    assert "cuisine" not in decision.slots
    assert not decision.need_clarification


def test_lang_chinese_restaurant_is_still_a_cuisine_edit() -> None:
    decision = _rule("Swap Day 1 lunch for a Chinese restaurant")
    assert decision.intent == Intent.MODIFY
    assert decision.slots == {"cuisine": "chinese"}
    assert _search_extra("Swap Day 1 lunch for a Chinese restaurant") == {"meals_query": "chinese restaurant"}


@pytest.mark.parametrize(
    "message",
    ["Make it in Traditional Chinese", "Can I get the Chinese version?", "Translate the plan to Chinese"],
)
def test_lang_traditional_chinese_never_fills_the_cuisine_slot(message: str) -> None:
    assert language_slot(message) is not None
    assert cuisine_slot(message) is None
    assert _search_extra(message) is None


@pytest.mark.parametrize(
    "message",
    [
        "Swap lunch for a traditional Chinese restaurant",
        "Change dinner to Japanese",
        "Can we go to the Korean DMZ",
        "Add an English breakfast on day 2",
        "Cantonese dim sum for lunch on day 1",
    ],
)
def test_lang_food_and_places_are_not_language_requests(message: str) -> None:
    assert language_slot(message) is None


def test_lang_refresh_row_routes_to_refresh() -> None:
    for message in ("Refresh the stale data", "Refresh the outdated prices", "update the stale data please"):
        assert _rule(message).intent == Intent.REFRESH
    assert _rule("Update the hotel to something cheaper").intent == Intent.MODIFY


def test_lang_system1_router_asks_no_model_for_language_turns(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        router: System1Router = rig.turns.router
        for message in ("Make it in Traditional Chinese", "Translate to Japanese", "Refresh the stale data", "天星小輪最後一班幾點開？"):
            asyncio.run(router.route(message=message, context=_context(), plan_summary=PLAN_SUMMARY, history=[]))
        assert rig.llm.purposes == []
    finally:
        ledger.close()


# --- turns ----------------------------------------------------------------------


def test_lang_set_traditional_chinese_turn_changes_only_the_locale(settings: Settings) -> None:
    result = _turn(settings, "Make it in Traditional Chinese")
    assert result["route"]["intent"] == "set_language"
    assert result["route"]["slots"]["language"] == "zh-Hant"
    assert result["locale_events"] == [{"plan_locale": "zh-Hant"}]
    assert result["context"].plan_locale == "zh-Hant"
    assert DISCLAIMER["zh-Hant"] in result["reply"]
    _assert_untouched(result)
    # the locale event comes before the reply tokens
    names = [event.event for event in result["events"]]
    assert names.index("locale") < names.index("token")


def test_lang_selected_stop_does_not_turn_set_language_into_a_stop_edit(settings: Settings) -> None:
    result = _turn(settings, "Make it in Traditional Chinese", select_meal=True)
    assert result["route"]["intent"] == "set_language"
    assert result["context"].plan_locale == "zh-Hant"
    assert "places" not in result["made"] and "meals" not in result["made"], "no restaurant search"
    _assert_untouched(result)


def test_lang_switch_back_to_english(settings: Settings) -> None:
    result = _turn(settings, "Switch back to English", plan_locale="zh-Hant")
    assert result["route"]["intent"] == "set_language"
    assert result["locale_events"] == [{"plan_locale": "en"}]
    assert result["context"].plan_locale == "en"
    assert "English" in result["reply"]
    _assert_untouched(result)


def test_lang_unsupported_language_lists_the_supported_ones(settings: Settings) -> None:
    result = _turn(settings, "Translate to Japanese")
    assert result["route"]["intent"] == "unsupported"
    assert result["locale_events"] == []
    assert result["context"].plan_locale == "en"
    assert "Japanese" in result["reply"] and "English" in result["reply"] and "Traditional Chinese" in result["reply"]
    _assert_untouched(result)


@pytest.mark.parametrize("plan_locale", ["en", "zh-Hant"])
def test_lang_chinese_input_gets_the_fixed_reply(settings: Settings, plan_locale: str) -> None:
    result = _turn(settings, "把酒店換成近新宿站的", plan_locale=plan_locale)
    assert result["route"]["intent"] == "unclear"
    assert result["route"]["reason_code"] == "chinese_input"
    assert result["reply"].startswith(CHINESE_INPUT_REPLY["en"])
    assert (CHINESE_INPUT_REPLY["zh-Hant"] in result["reply"]) == (plan_locale == "zh-Hant")
    _assert_untouched(result)


def test_lang_refresh_calls_only_the_freshness_check(settings: Settings) -> None:
    result = _turn(settings, "Refresh the stale data", stale=True)
    assert result["route"]["intent"] == "refresh"
    assert result["checks"] == ["check"]
    assert result["paths"] == [], "no planner, modify or ask path"
    assert result["purposes"] == [], "no model call"
    assert result["errors"] == []
    # The outdated sections were fetched again by their own sources, and only those.
    assert sorted(result["logged"]) == ["flights.search", "hotels.search", "weather.forecast"]
    assert len(result["versions_after"]) == len(result["versions_before"]) + 1
    assert "plan" in [event.event for event in result["events"]]
    assert result["reply"] == "Refreshed the outdated weather forecast, flight fares and hotel prices."
    assert result["context"].plan_locale == "en"


def test_lang_refresh_reply_follows_the_plan_locale(settings: Settings) -> None:
    result = _turn(settings, "Refresh the stale data", plan_locale="zh-Hant")
    assert result["checks"] == ["check"]
    assert any("一" <= char <= "鿿" for char in result["reply"])


# --- state ----------------------------------------------------------------------


def test_lang_plan_locale_defaults_to_english_for_existing_sessions() -> None:
    legacy = '{"session_id": "old", "destination": "Kyoto", "language": "en"}'
    assert TripContext.model_validate_json(legacy).plan_locale == "en"


def test_lang_plan_locale_is_settable_through_patch_state(settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from app.interface.api import create_app

    with TestClient(create_app(settings)) as client:
        guest = {"X-Client-Token": "test"}
        created = client.post("/api/journeys", json={"client_token": "test"})
        session_id = created.json()["session"]["id"]
        assert created.json()["context"]["plan_locale"] == "en"
        patched = client.patch(f"/api/journeys/{session_id}/state", json={"plan_locale": "zh-Hant"}, headers=guest)
        assert patched.status_code == 200
        assert patched.json()["context"]["plan_locale"] == "zh-Hant"
        assert client.get(f"/api/journeys/{session_id}", headers=guest).json()["context"]["plan_locale"] == "zh-Hant"
        rejected = client.patch(f"/api/journeys/{session_id}/state", json={"plan_locale": "ja"}, headers=guest)
        assert rejected.status_code == 422


# --- Ho Lee Fook --------------------------------------------------------------------


def test_lang_clarification_example_uses_a_real_stop(settings: Settings) -> None:
    context = _context()
    plan = asyncio.run(accepted_plan(settings, context))
    example = _stop_example(plan)
    first_day = next(day for day in plan.days if day.items)
    assert example == f' (for example "1. {first_day.items[0].name}")'
    assert "Ho Lee Fook" not in example
    empty = plan.model_copy(update={"days": [day.model_copy(update={"items": []}) for day in plan.days]})
    assert _stop_example(empty) == ""
