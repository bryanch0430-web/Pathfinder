import asyncio
from datetime import date

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.router import _gate
from app.application.understanding import _relative_dates, _valid_place, understand
from app.config import Settings
from app.domain.models import Intent, RouteDecision, TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer

TODAY = date(2026, 10, 31)  # a Saturday


def _gateway(settings: Settings) -> LLMGateway:
    return LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)


def test_relative_date_phrases() -> None:
    assert _relative_dates("in three months", TODAY) == (date(2027, 1, 31), None)
    assert _relative_dates("next month", TODAY) == (date(2026, 11, 1), None)
    assert _relative_dates("this weekend", TODAY) == (date(2026, 10, 31), date(2026, 11, 1))
    assert _relative_dates("in two weeks", TODAY) == (date(2026, 11, 14), None)


def test_time_phrases_are_not_places() -> None:
    assert _valid_place("three months") is False
    assert _valid_place("next month") is False
    assert _valid_place("London") is True


def test_date_fragment_does_not_overwrite_destination(settings: Settings) -> None:
    context = TripContext(session_id="s", destination="London", party_size=2)

    updated = asyncio.run(understand("in three months", context, _gateway(settings), today=TODAY))

    assert updated.destination == "London"
    assert updated.start_date == date(2027, 1, 31)


def test_labeled_clarify_answer_fills_slots_without_rewriting_destination(settings: Settings) -> None:
    context = TripContext(session_id="s", destination="London", party_size=2, currency="HKD")
    message = "Here are the missing details — origin: Hong Kong; dates: In three months; budget: 5,000 HKD."

    updated = asyncio.run(understand(message, context, _gateway(settings), today=TODAY))

    assert updated.destination == "London"
    assert updated.origin == "Hong Kong"
    assert updated.start_date == date(2027, 1, 31)
    assert updated.budget == 5000
    assert updated.currency == "HKD"


def test_missing_slots_reports_dates_once() -> None:
    context = TripContext(
        session_id="s", destination="London", origin="Hong Kong", party_size=2, budget=5000
    )
    assert context.missing_slots() == ["dates"]


def test_gate_moves_to_plan_when_context_is_complete(settings: Settings) -> None:
    context = TripContext(
        session_id="s",
        destination="London",
        origin="Hong Kong",
        start_date=date(2026, 11, 8),
        end_date=date(2026, 11, 13),
        party_size=2,
        budget=5000,
    )
    decision = RouteDecision(
        intent=Intent.UNCLEAR,
        confidence=0.2,
        need_clarification=True,
        reason_code="below_threshold_jev",
        missing_slots=["dates"],
    )

    gated = _gate(decision, context, None, settings, source="jev")

    assert gated.intent == Intent.PLAN
    assert gated.need_clarification is False
    assert gated.missing_slots == []


def test_gate_still_clarifies_when_a_slot_is_really_missing(settings: Settings) -> None:
    context = TripContext(session_id="s", destination="London")
    decision = RouteDecision(
        intent=Intent.UNCLEAR,
        confidence=0.2,
        need_clarification=True,
        reason_code="below_threshold_jev",
        missing_slots=["dates"],
    )

    gated = _gate(decision, context, None, settings, source="jev")

    assert gated.need_clarification is True
    assert "dates" in gated.missing_slots
