import asyncio
from datetime import date, timedelta

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.router import RuleRouter, System1Router
from app.config import Settings
from app.domain.models import Intent, TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer


def context(**overrides) -> TripContext:
    values = dict(
        session_id="s",
        destination="Kyoto",
        start_date=date.today() + timedelta(days=30),
        end_date=date.today() + timedelta(days=32),
        party_size=2,
        budget=400000,
        currency="JPY",
    )
    values.update(overrides)
    return TripContext(**values)


def test_plan_question_modify_classification() -> None:
    router = RuleRouter()
    plan = router.route(message="Plan my trip", context=context(), plan_summary=None, history=[])
    assert plan.intent in {Intent.PLAN, Intent.UNCLEAR}
    ask = router.route(message="What is the weather on day 2?", context=context(), plan_summary={"trip_id": "t"}, history=[])
    assert ask.intent == Intent.ASK
    modify = router.route(message="Swap the hotel for a boutique one", context=context(), plan_summary={"trip_id": "t"}, history=[])
    assert modify.intent == Intent.MODIFY
    assert "hotel" in modify.affected_parts


def test_plan_requires_origin() -> None:
    decision = RuleRouter().route(
        message="Plan my trip",
        context=context(origin=""),
        plan_summary=None,
        history=[],
    )
    assert decision.need_clarification is True
    assert "origin" in decision.missing_slots


def test_modify_without_plan_is_unclear() -> None:
    decision = RuleRouter().route(message="Change the hotel", context=context(), plan_summary=None, history=[])
    assert decision.intent == Intent.UNCLEAR


def test_gate_forces_clarification_below_threshold(settings: Settings) -> None:
    router = System1Router(
        gateway=LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings),
        settings=settings,
        use_model=False,
    )
    decision = asyncio.run(
        router.route(message="Hmm, maybe something", context=context(), plan_summary=None, history=[])
    )
    assert decision.need_clarification is True
    assert decision.confidence < settings.router_threshold
