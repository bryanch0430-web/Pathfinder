"""What an answer reads of the plan: all of it when the question is about no one day, else that day."""

from __future__ import annotations

from plan_samples import make_plan

from app.application.asking import ask_prompt


def test_question_with_nothing_selected_gets_the_full_plan() -> None:
    text = ask_prompt("is this trip too rushed?", make_plan(), {}, "plan")

    assert text.startswith("Plan: Kyoto, 2026-12-03..2026-12-04")
    assert (
        "Stay: Hotel Granvia" in text
        and "Transport: flight HKG-KIX Cathay" in text
        and "Cost: total 8200 HKD" in text
    )
    assert (
        'Day 1 2026-12-03 "Day 1 walk":' in text
        and 'Day 2 2026-12-04 "Day 2 walk":' in text
    )
    assert "item_d1_s1" not in text and "in detail:" not in text


def test_question_about_a_day_gets_that_day_in_detail() -> None:
    text = ask_prompt("what do we eat on day 2?", make_plan(alternatives=1), {}, "plan")

    assert "Day 2 in detail:" in text
    assert "other choices: Other table 0 near Soba house 2" in text
    assert "Other table 0 near Soba house 1" not in text


def test_question_with_a_selected_stop_names_it() -> None:
    text = ask_prompt(
        "is this open on Mondays?", make_plan(), {}, "plan", scope_item_id="item_d1_s1"
    )

    assert "Day 1 in detail:" in text
    assert "Selected by the traveller: Temple 1.1 (Day 1)" in text


def test_itinerary_rows_are_not_sent_twice() -> None:
    facts = {"itinerary": [{"x": "ROW_MARK"}], "cost": {"total": 1}}

    text = ask_prompt("what is the total?", make_plan(), facts, "plan")

    assert "ROW_MARK" not in text and '"cost"' in text
    assert "itinerary" in facts


def test_no_plan_is_unchanged() -> None:
    text = ask_prompt(
        "where should I go?", None, {"itinerary": [{"x": "ROW_MARK"}]}, "none"
    )

    assert text.startswith("Plan: none\n")
    assert "ROW_MARK" in text


def test_the_question_is_sent_once_in_its_fence_after_the_plan_and_has_no_url() -> None:
    text = ask_prompt("is this trip too rushed?", make_plan(), {}, "plan")

    assert text.count("is this trip too rushed?") == 1
    assert (
        text.index("Plan: ")
        < text.index("Facts (source: plan)")
        < text.index("<user_travel_request>")
    )
    assert "http" not in text
