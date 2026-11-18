import asyncio
from datetime import date, timedelta

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.router import (
    PLAIN_MODEL,
    REASON_MODEL,
    RuleRouter,
    System1Router,
    gate_cloud_llm,
    gate_mode,
    looks_like_question,
    not_about_the_trip,
    question_comes_first,
)
from app.config import Settings
from app.domain.models import Intent, RouteDecision, TripContext
from app.infrastructure.llm import LLMResult, NullLLM
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


def test_a_restaurant_question_is_a_question_even_without_a_question_word() -> None:
    assert looks_like_question("Café Bohème TKO is for eating japanese food??") is True


def test_fast_reasoning_skips_jev_and_jev_still_answers_when_it_is_the_gate(settings: Settings) -> None:
    class Jev:
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, **kwargs):
            self.calls += 1
            return RouteDecision(
                intent=Intent.ASK,
                confidence=0.95,
                need_clarification=False,
                reason_code="jev_ask",
                rationale="system one decision",
            )

    class Fast:
        available = True
        name = "fast-reasoner"

        async def complete(self, **kwargs):
            return LLMResult(
                text='{"intent":"ask","confidence":0.93,"need_clarification":false,"reason_code":"fast_ask"}',
                model="fast-reasoner",
            )

    jev = Jev()
    router = System1Router(
        gateway=LLMGateway(Fast(), PromptLibrary(settings), NoopTracer(), settings),
        settings=settings,
        decision_client=jev,
        use_model=True,
    )
    message = "maybe the museum"
    plan = {"trip_id": "t"}
    jev_decision = asyncio.run(router.route(message=message, context=context(), plan_summary=plan, history=[]))
    assert jev.calls == 1
    assert jev_decision.reason_code == "jev_ask"

    settings.llm_api_key = ""
    settings.openai_api_key = ""
    settings.xai_api_key = ""
    settings.router_gate = "fast"
    fast_decision = asyncio.run(router.route(message=message, context=context(), plan_summary=plan, history=[]))
    assert jev.calls == 1
    assert fast_decision.reason_code == "fast_ask"


def test_a_saved_fast_key_is_asked_instead_of_the_writer(settings: Settings, monkeypatch) -> None:
    class Fast:
        available = True
        name = "fast-reasoner"

        async def complete(self, **kwargs):
            return LLMResult(
                text='{"intent":"ask","confidence":0.93,"need_clarification":false,"reason_code":"fast_key"}',
                model="fast-reasoner",
            )

    class Jev:
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, **kwargs):
            self.calls += 1
            return RouteDecision(intent=Intent.ASK, confidence=0.95, need_clarification=False, reason_code="jev_ask")

    monkeypatch.setattr("app.application.router.gate_cloud_llm", lambda _settings, _mode: Fast())
    jev = Jev()
    settings.router_gate = "reason"
    router = System1Router(
        gateway=LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings),
        settings=settings,
        decision_client=jev,
        use_model=True,
    )
    decision = asyncio.run(
        router.route(message="maybe the museum", context=context(), plan_summary={"trip_id": "t"}, history=[])
    )
    assert jev.calls == 0
    assert decision.reason_code == "fast_key"


def test_the_cloud_gate_uses_the_poe_model_for_each_arm(settings: Settings) -> None:
    settings.poe_api_key = "sk-test-key-1234"
    settings.poe_base_url = "https://api.poe.com/v1"
    plain = gate_cloud_llm(settings, "plain")
    reason = gate_cloud_llm(settings, "reason")
    assert plain is not None and plain.name == PLAIN_MODEL and plain._fixed_temperature == 0.0
    assert plain._fixed_max_tokens == 1024 and plain._extra == {"reasoning_effort": "none"}
    assert reason is not None and reason.name == REASON_MODEL and reason._fixed_temperature == 0.2
    assert reason._extra == {"reasoning_effort": "low"}
    assert gate_mode(settings) == "jev"
    settings.router_gate = "fast"
    assert gate_mode(settings) == "reason"


def test_an_arm_key_and_provider_replace_the_shared_poe_key(settings: Settings) -> None:
    settings.poe_api_key = "sk-poe-shared-key"
    settings.gate_plain_provider = "openai"
    settings.gate_plain_api_key = "sk-openai-arm-key"
    settings.gate_plain_model = "gpt-4o-mini"
    settings.gate_plain_temperature = 0.1
    settings.gate_plain_max_tokens = 256
    settings.gate_plain_reasoning = "none"
    plain = gate_cloud_llm(settings, "plain")
    assert plain is not None
    assert plain._api_key == "sk-openai-arm-key"
    assert plain._base_url == "https://api.openai.com/v1"
    assert plain._fixed_temperature == 0.1
    assert plain._fixed_max_tokens == 256


def test_a_question_during_intake_is_answered_before_the_missing_details(settings: Settings) -> None:
    class Jev:
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, **kwargs):
            self.calls += 1
            return RouteDecision(intent=Intent.PLAN, confidence=0.99, need_clarification=True, reason_code="jev_plan")

    jev = Jev()
    router = System1Router(
        gateway=LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings),
        settings=settings,
        decision_client=jev,
        use_model=True,
    )
    incomplete = TripContext(session_id="s", destination="Kyoto", start_date=date(2026, 11, 30), budget=8300)

    async def decide(message: str):
        return await router.route(message=message, context=incomplete, plan_summary=None, history=[])

    for message in ("what is gundam from japan", "you don't know what is gundam??"):
        assert question_comes_first(message) is True
        decision = asyncio.run(decide(message))
        assert decision.intent == Intent.ASK
        assert decision.need_clarification is False
        assert decision.reason_code == "question_before_intake"
    assert jev.calls == 0

    planned = asyncio.run(decide("Plan my trip"))
    assert planned.intent == Intent.PLAN
    assert planned.need_clarification is True
    assert jev.calls == 1


def test_an_insult_is_not_about_the_trip_and_a_short_reply_is() -> None:
    assert not_about_the_trip("....fuck you") is True
    assert not_about_the_trip("hello") is True
    assert not_about_the_trip("hello? are you hal jordon") is True
    assert not_about_the_trip("the other one") is False
    assert not_about_the_trip("what is the weather on day 2?") is False
    assert not_about_the_trip("what is gundam from japan") is False
    assert not_about_the_trip("you don't know what is gundam??") is False
    assert not_about_the_trip("what is this") is False
