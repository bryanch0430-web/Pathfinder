"""What the edit step reads of the plan: all of it when the message is about no one day, else that day."""

from __future__ import annotations

import asyncio

from plan_samples import make_plan
from test_conversation_context import RecordingLLM, _gateway

from app.application.modification import ModifyPath
from app.config import Settings
from app.domain.models import EvalCase
from app.eval_efficiency import case_context


def _edit_prompt(
    settings: Settings, message: str, scope_item_id: str | None = None
) -> str:
    llm = RecordingLLM()
    path = ModifyPath(
        planner=None, plan_path=None, settings=settings, gateway=_gateway(settings, llm)
    )  # type: ignore[arg-type]
    context = case_context(
        EvalCase(id="c", dataset="design", input="", destination="Kyoto"), "s"
    )

    asyncio.run(
        path._extract(
            message, make_plan(alternatives=1), context, [], scope_item_id=scope_item_id
        )
    )

    return llm.prompts("modify.extract")[0]


def test_edit_with_nothing_selected_sends_the_full_plan(settings: Settings) -> None:
    prompt = _edit_prompt(settings, "make the whole trip cheaper")

    assert (
        "Stay: hotel_1 Hotel Granvia" in prompt
        and "Transport: ticket_1" in prompt
        and "Cost: total 8200 HKD" in prompt
    )
    assert (
        'Day 1 2026-12-03 "Day 1 walk":' in prompt
        and 'Day 2 2026-12-04 "Day 2 walk":' in prompt
    )
    assert (
        "item_d2_lunch 13:00-14:00 Soba house 2 (meal lunch) $$, soba, rating 4.4 (812)"
        in prompt
    )
    assert "in detail:" not in prompt and "Selected by the traveller" not in prompt


def test_edit_naming_a_day_sends_outline_plus_that_day(settings: Settings) -> None:
    prompt = _edit_prompt(settings, "day 2 is too busy")

    assert "Day 2 in detail:" in prompt
    assert "other choices: Other table 0 near Soba house 2" in prompt
    assert "Other table 0 near Soba house 1" not in prompt
    assert "Day 1 2026-12-03: item_d1_s1 Temple 1.1" in prompt


def test_edit_with_a_selected_stop_names_it(settings: Settings) -> None:
    prompt = _edit_prompt(settings, "swap this for something indoors", "item_d1_s1")

    assert "Day 1 in detail:" in prompt
    assert "Selected by the traveller: item_d1_s1 Temple 1.1 (Day 1)" in prompt


def test_edit_prompt_has_no_urls_and_the_message_once_in_its_fence(
    settings: Settings,
) -> None:
    prompt = _edit_prompt(settings, "make the whole trip cheaper")

    assert "http" not in prompt
    assert prompt.count("make the whole trip cheaper") == 1
    assert (
        prompt.index("Current plan:")
        < prompt.index("Trip:")
        < prompt.index("<user_travel_request>")
    )
