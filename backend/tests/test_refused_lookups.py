"""A lookup the per-turn cap refused is reported as what it is: a limit, in plain words, once.

CONC-G5: a refused call showed the traveller "toolbox: hotels.search was not called: this turn already
made 2 tool calls (the limit is 2)" next to a second gap that claimed the search found nothing; a
refused geocode read as "I could not locate 'Tokyo'" (and invited a retry that hits the same cap); a
refused transfer lookup said nothing at all. LEAK-G9: the error shown for a turn that ends on a refused
call or a blocked tool named the tool or blamed a data source.

Offline: sandbox providers and the metered writer, like the other turn tests.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.application.modification import ModifyPath
from app.application.orchestrator import user_facing_error
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import SecurityBlocked, ToolBudgetExceeded, operator_text
from app.domain.models import EvalCase, FailureKind, TripPlan
from app.eval_efficiency import accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger
from app.infrastructure.observability import NoopTracer
from tests.test_modify_targets import _build

#: Words that belong in a log, not in what a traveller reads.
INTERNAL = ("toolbox", "tool calls", "tool call", "the limit is", "was not called", "hotels.search", "places.search")


def _plan_turn(settings: Settings, cap: int | None) -> tuple[list, TripPlan | None, list[dict]]:
    """One plan turn on a fresh trip; returns the events, the saved plan and the turn's tool log."""
    settings = settings.model_copy(
        update={
            "preplan_budget_seconds": 15,
            "agent_timeout_seconds": 8,
            **({"max_tool_calls_per_turn": cap} if cap is not None else {}),
        }
    )
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Tokyo", ledger)
        session = ledger.store.create_session("guest-refused")
        context = case_context(EvalCase(id="r", dataset="design", input="", destination="Tokyo"), session.id)
        ledger.store.save_context(context)
        rig.llm.route = {"intent": "plan", "confidence": 0.95}

        async def turn() -> list:
            return [event async for event in rig.turns.handle_turn(session_id=session.id, message="Plan my trip.")]

        events = asyncio.run(turn())
        request_id = next(event.data["request_id"] for event in events if event.event == "done")
        return events, ledger.store.get_plan(session.id), ledger.store.tool_calls(request_id)
    finally:
        ledger.close()


SWAP_HOTEL = "Swap the hotel for the other option."


def _edit_turn(settings: Settings, cap: int) -> tuple[list, TripPlan | None, list[dict]]:
    """One hotel swap on a trip that has a plan; the cap is on the swap, not on making the plan."""
    open_settings = settings.model_copy(
        update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8, "max_tool_calls_per_turn": 0}
    )
    ledger = RunLedger()
    try:
        rig = build_rig(open_settings.model_copy(update={"max_tool_calls_per_turn": cap}), "Tokyo", ledger)
        session = ledger.store.create_session("guest-refused")
        context = case_context(EvalCase(id="r", dataset="design", input="", destination="Tokyo"), session.id)
        ledger.store.save_context(context)
        ledger.store.save_plan(asyncio.run(accepted_plan(open_settings, context)), "Accepted plan")
        rig.llm.route = {"intent": "modify", "confidence": 0.95, "affected_parts": ["hotel"]}

        async def turn() -> list:
            return [event async for event in rig.turns.handle_turn(session_id=session.id, message=SWAP_HOTEL)]

        events = asyncio.run(turn())
        request_id = next(event.data["request_id"] for event in events if event.event == "done")
        return events, ledger.store.get_plan(session.id), ledger.store.tool_calls(request_id)
    finally:
        ledger.close()


class _Log:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def log_tool_call(self, **row) -> None:
        self.rows.append(row)


def _refusal() -> ToolBudgetExceeded:
    async def lookup(**_: object) -> list:
        return []

    toolbox = Toolbox(path="plan", tracer=NoopTracer(), store=_Log(), request_id="req_cap", max_calls=1)

    async def scenario() -> None:
        await toolbox.call("geocode", lookup, name="Tokyo")
        await toolbox.call("geocode", lookup, name="Kyoto")

    with pytest.raises(ToolBudgetExceeded) as caught:
        asyncio.run(scenario())
    return caught.value


def test_a_refused_call_carries_words_for_the_traveller_and_the_counters_for_the_trace() -> None:
    refusal = _refusal()

    assert not any(word in str(refusal).lower() for word in INTERNAL)
    assert "limit" in str(refusal)
    assert refusal.kind is FailureKind.BLOCKED and not refusal.retryable
    assert "geocode was not called" in refusal.detail and "(the limit is 1)" in refusal.detail
    assert "geocode was not called" in operator_text(refusal)


def test_the_gaps_of_a_plan_whose_lookups_were_refused_are_plain_and_not_repeated(settings: Settings) -> None:
    events, plan, calls = _plan_turn(settings, cap=2)

    assert plan is not None and not [event for event in events if event.event == "error"]
    assert any(call["status"] == "blocked" for call in calls)
    blocked = [gap for gap in plan.data_quality.gaps if gap.kind is FailureKind.BLOCKED]
    assert blocked, "the refusals must show on the plan"
    for gap in plan.data_quality.gaps:
        assert not any(word in gap.message.lower() for word in INTERNAL), gap
    sections = {gap.section for gap in blocked}
    for section in sections:
        in_section = [gap for gap in plan.data_quality.gaps if gap.section == section]
        # One statement per section: the limit, not also "no relevant page found".
        assert [gap.kind for gap in in_section] == [FailureKind.BLOCKED], in_section


def test_a_refused_geocode_is_reported_as_the_limit_and_not_as_an_unknown_place(settings: Settings) -> None:
    events, plan, calls = _plan_turn(settings, cap=1)  # the exchange rates take the one call

    errors = [event.data for event in events if event.event == "error"]
    assert plan is None and len(errors) == 1
    assert "could not locate" not in errors[0]["message"].lower()
    assert "limit" in errors[0]["message"]
    assert errors[0]["retryable"] is False
    assert [call["status"] for call in calls if call["tool"] == "geocode"] == ["blocked"]


def test_transfer_times_that_fell_back_to_estimates_because_of_the_cap_show_as_a_gap(settings: Settings) -> None:
    _, whole, calls = _plan_turn(settings, cap=None)
    assert whole is not None and not [gap for gap in whole.data_quality.gaps if gap.section == "transfers"]
    # Let every lookup through except the transfer times, which come last.
    cap = next(index for index, call in enumerate(calls) if call["tool"] == "routes.segments")

    events, plan, calls = _plan_turn(settings, cap=cap)

    assert plan is not None and not [event for event in events if event.event == "error"]
    assert all(call["status"] == "blocked" for call in calls if call["tool"] == "routes.segments")
    [gap] = [gap for gap in plan.data_quality.gaps if gap.section == "transfers"]
    assert gap.kind is FailureKind.BLOCKED and "estimate" in gap.message
    assert not any(word in gap.message.lower() for word in INTERNAL)


def test_a_turn_that_ends_on_a_refused_call_does_not_blame_a_data_source() -> None:
    shown = user_facing_error(_refusal())

    assert "data source failed" not in shown and "plan was not changed" not in shown
    assert "limit" in shown
    assert not any(word in shown.lower() for word in INTERNAL)


def test_a_blocked_tool_is_not_named_to_the_traveller() -> None:
    blocked = SecurityBlocked("tool web.search is not allowed on the ask path", category="tool_allowlist")

    shown = user_facing_error(blocked)

    assert "web.search" not in shown and "allowed" not in shown and "path" not in shown


def test_a_hotel_swap_whose_lookup_the_cap_refused_says_so_and_not_that_none_came_back(settings: Settings) -> None:
    """RB-B-6: the reply and the plan said the source returned no cheaper hotel, and the search was never made."""
    events, plan, calls = _edit_turn(settings, cap=1)  # the exchange rates take the one call

    assert plan is not None and not [event for event in events if event.event == "error"]
    assert [call["status"] for call in calls if call["tool"] == "hotels.search"] == ["blocked"]
    [reply] = [event.data["text"] for event in events if event.event == "token"]
    assert "limit" in reply and "returned" not in reply
    hotel_gaps = [gap for gap in plan.data_quality.gaps if gap.section == "hotels"]
    # One statement, as on the plan path: the limit, not also "none was returned".
    assert [gap.kind for gap in hotel_gaps] == [FailureKind.BLOCKED], hotel_gaps
    for text in [reply, *(gap.message for gap in plan.data_quality.gaps)]:
        assert not any(word in text.lower() for word in INTERNAL), text


def test_a_hotel_swap_that_ran_its_lookup_and_found_nothing_still_says_none_was_returned(settings: Settings) -> None:
    gateway, planner, plan_path, context, plan = _build(settings)
    plan_path.registry.hotels = []  # the source has no other hotel to offer
    toolbox = Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t")

    updated, _ = asyncio.run(
        ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway).run(
            plan=plan,
            context=context,
            message=SWAP_HOTEL,
            route_parts=["hotel"],
            toolbox=toolbox,
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )

    assert not toolbox.refused
    assert "No cheaper verified hotel was returned" in updated.change_summary
    assert [gap.message for gap in updated.data_quality.gaps if gap.section == "hotels"] == [
        "No cheaper verified hotel was returned; the current hotel stays."
    ]


def test_a_trip_without_a_hotel_whose_lookup_the_cap_refused_is_not_told_that_none_was_returned(
    settings: Settings,
) -> None:
    gateway, planner, plan_path, context, plan = _build(settings)
    plan.hotels = []
    context.start_date += timedelta(days=7)
    context.end_date += timedelta(days=7)
    toolbox = Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t", max_calls=1)

    updated, _ = asyncio.run(
        ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway).run(
            plan=plan,
            context=context,
            message="move the trip a week later",
            route_parts=["dates"],
            toolbox=toolbox,
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )

    assert "hotels.search" in toolbox.refused
    hotel_gaps = [gap for gap in updated.data_quality.gaps if gap.section == "hotels"]
    assert [gap.kind for gap in hotel_gaps] == [FailureKind.BLOCKED], hotel_gaps
