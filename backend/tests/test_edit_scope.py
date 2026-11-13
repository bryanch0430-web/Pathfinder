"""Which city a chat edit is about."""

from __future__ import annotations

from plan_samples import make_plan, make_route_plan

from app.application.edit_scope import segment_for_edit, which_visit_question


def _id(result) -> str:
    return result if isinstance(result, str) else result.id


def test_a_selected_stop_settles_it() -> None:
    plan = make_route_plan(["Tokyo", "Kyoto", "Osaka"])

    assert _id(segment_for_edit(plan, "make it later", "item_d4", None)) == "seg_2"
    assert _id(segment_for_edit(plan, "make it later in Tokyo", "item_d4", None)) == "seg_2"


def test_a_selected_stay_settles_it() -> None:
    plan = make_route_plan(["Tokyo", "Kyoto", "Osaka"])

    assert _id(segment_for_edit(plan, "cheaper", "hotel_3", None)) == "seg_3"


def test_a_day_settles_it_and_the_shared_day_is_the_city_arrived_in() -> None:
    plan = make_route_plan(["Tokyo", "Kyoto", "Osaka"])

    assert _id(segment_for_edit(plan, "slower", None, 2)) == "seg_1"
    assert _id(segment_for_edit(plan, "slower", None, 3)) == "seg_2"
    assert _id(segment_for_edit(plan, "make day 6 lighter", None, None)) == "seg_3"
    assert _id(segment_for_edit(plan, "a different lunch on 8 December", None, None)) == "seg_3"


def test_a_city_in_the_message_settles_it() -> None:
    plan = make_route_plan(["Tokyo", "Kyoto", "Osaka"])

    assert _id(segment_for_edit(plan, "a cheaper hotel in kyoto please", None, None)) == "seg_2"
    assert _id(segment_for_edit(plan, "nothing touristy near Osaka", None, None)) == "seg_3"


def test_a_city_name_inside_another_word_is_not_a_city() -> None:
    assert segment_for_edit(make_route_plan(["Rome", "Kyoto"]), "something romantic", None, None) == "all"


def test_a_message_about_no_city_is_about_all_of_them() -> None:
    plan = make_route_plan(["Tokyo", "Kyoto"])

    assert segment_for_edit(plan, "slower pace", None, None) == "all"
    assert segment_for_edit(plan, "slower pace", "gone_id", None) == "all"


def test_a_city_visited_twice_asks_which_visit_unless_a_stop_or_day_settles_it() -> None:
    plan = make_route_plan(["Tokyo", "Kyoto", "Tokyo"])

    assert segment_for_edit(plan, "a cheaper hotel in Tokyo", None, None) == "ask"
    assert _id(segment_for_edit(plan, "a cheaper hotel in Tokyo", "item_d6", None)) == "seg_3"
    assert _id(segment_for_edit(plan, "a cheaper hotel in Tokyo on day 2", None, None)) == "seg_1"
    assert which_visit_question(plan, "a cheaper hotel in Tokyo") == (
        "You visit Tokyo twice. Which visit do you mean: 03 Dec–05 Dec or 07 Dec–09 Dec?"
    )


def test_a_one_city_plan_is_always_its_one_segment() -> None:
    plan = make_plan()

    assert _id(segment_for_edit(plan, "slower pace", None, None)) == "seg_1"
