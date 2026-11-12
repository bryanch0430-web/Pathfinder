"""What the edit step reads of the plan: all of it when the message is about no one day, else that day."""

from __future__ import annotations

import asyncio
import json

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


def _dates_edit(settings: Settings, target_item_id: str | None):
    """Move the whole trip a week with a stop selected, the writer naming `target_item_id` as its target."""
    from test_modify_dates import _edit

    reply = {
        "description": "move the trip a week later",
        "affected_parts": ["dates"],
        "target_item_id": target_item_id,
        "confidence": 0.9,
    }
    llm = RecordingLLM(replies={"modify.extract": json.dumps(reply)})
    _, updated, diff, _ = _edit(
        settings, "move the trip a week later", writer=_gateway(settings, llm), scope_item_id="item_p1"
    )
    assert llm.prompts("modify.extract"), "the writer was not asked"
    return updated, diff


def test_a_whole_trip_edit_is_not_aimed_at_the_selected_stop_the_writer_was_told_about(settings: Settings) -> None:
    told, told_diff = _dates_edit(settings, "item_p1")
    untold, untold_diff = _dates_edit(settings, None)

    assert [entry.get("kind") for entry in told_diff] == [entry.get("kind") for entry in untold_diff]
    assert [[item.name for item in day.items] for day in told.days] == [[item.name for item in day.items] for day in untold.days]
    assert told.change_summary == untold.change_summary


def test_edit_prompt_carries_the_weather_and_tags_but_not_the_stops_notes(settings: Settings) -> None:
    from app.application.prompt_context import plan_context
    from app.domain.models import WeatherDay

    plan = make_plan()
    plan.days[0].items[0].notes = "Closed on Mondays; book ahead"
    plan.days[0].items[0].tags = ["outdoor"]
    plan.days[0].weather = WeatherDay(date=plan.days[0].date, summary="Light rain", high_c=12, low_c=4)

    for focus in (None, 1):
        with_notes = plan_context(plan, focus=focus)
        without = plan_context(plan, focus=focus, notes=False)
        assert "note: Closed on Mondays" in with_notes
        assert "Closed on Mondays" not in without
        assert "weather: Light rain" in without and "tags outdoor" in without

    llm = RecordingLLM()
    path = ModifyPath(planner=None, plan_path=None, settings=settings, gateway=_gateway(settings, llm))  # type: ignore[arg-type]
    context = case_context(EvalCase(id="c", dataset="design", input="", destination="Kyoto"), "s")
    asyncio.run(path._extract("make the whole trip cheaper", plan, context, []))
    prompt = llm.prompts("modify.extract")[0]
    assert "Closed on Mondays" not in prompt and "weather: Light rain" in prompt
