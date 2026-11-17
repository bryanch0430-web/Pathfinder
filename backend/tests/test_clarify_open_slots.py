"""A plan whose choice slots are all open, with a complete trip form, is normal. It must not draw a clarifying question.

The clarify skill says "a stay that was not chosen is one such gap". With choice slots an unlocked stay is the usual
state, so this checks that a clear message on such a plan never reaches the clarify prompt, and that the plan outline
the clarify prompt reads says nothing about stays that could be read as a gap. No network and no real writer: the
router's decision is scripted.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.domain.models import EvalCase
from app.eval_efficiency import accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger
from tests.test_conversation_context import RecordingLLM


def _run(
    settings: Settings, message: str, route: dict
) -> tuple[RecordingLLM, list, list[tuple[str, str]]]:
    """One real turn on a stored one-city trip whose stay and flights have two options each, every slot open."""
    settings = settings.model_copy(
        update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8}
    )

    async def main() -> tuple[RecordingLLM, list, list[tuple[str, str]]]:
        ledger = RunLedger()
        try:
            rig = build_rig(settings, "Kyoto", ledger)
            llm = RecordingLLM()
            llm.route = route
            rig.gateway.llm = llm
            session = ledger.store.create_session("guest-open-slots")
            context = case_context(
                EvalCase(id="c", dataset="design", input="", destination="Kyoto"),
                session.id,
            )
            ledger.store.save_context(context)
            plan = await accepted_plan(settings, context)
            for stay in plan.hotels:
                stay.locked = False
            if plan.hotels:
                plan.hotel_options = [
                    plan.hotels[0].model_copy(),
                    plan.hotels[0].model_copy(
                        update={"id": "hotel_other", "name": "Another Inn"}
                    ),
                ]
            ledger.store.save_plan(plan, "Accepted plan")
            events = [
                event
                async for event in rig.turns.handle_turn(
                    session_id=session.id, message=message
                )
            ]
            return llm, events, plan.open_choices()
        finally:
            ledger.close()

    return asyncio.run(main())


@pytest.mark.parametrize(
    ("message", "route"),
    [
        ("what is the weather like on day 2?", {"intent": "ask", "confidence": 0.95}),
        ("add a ramen dinner on day 2", {"intent": "modify", "confidence": 0.95}),
    ],
)
def test_a_clear_message_on_a_plan_with_every_slot_open_is_not_clarified(
    settings: Settings, message: str, route: dict
) -> None:
    llm, events, open_choices = _run(settings, message, route)

    assert open_choices
    assert [
        purpose for purpose, _ in llm.sent if purpose.startswith(("ask", "modify"))
    ], llm.sent
    assert not llm.prompts("clarify"), llm.prompts("clarify")
    assert not [event for event in events if event.event == "clarify"]


def test_the_outline_the_clarify_prompt_reads_does_not_mention_an_open_stay(
    settings: Settings,
) -> None:
    llm, _events, open_choices = _run(
        settings, "hmm, day 2", {"intent": "unclear", "confidence": 0.3}
    )

    assert open_choices, (
        "the plan must have an open stay for this check to mean anything"
    )
    prompts = llm.prompts("clarify")
    assert prompts, (
        "an unclear message on a plan is the one case that reaches the clarify prompt"
    )
    lowered = prompts[0].lower()
    assert (
        "not chosen" not in lowered
        and "hotel_other" not in lowered
        and "another inn" not in lowered
    ), prompts[0]


def test_the_edit_skill_says_its_new_fields_are_for_one_city_plans() -> None:
    from app.application.skills import skill_for_modify

    skill = skill_for_modify()
    assert skill is not None
    assert "one-city plans" in skill.body
