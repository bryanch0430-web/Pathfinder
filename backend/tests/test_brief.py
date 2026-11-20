from app.agents.trip_planner.brief import read_brief
from app.agents.trip_planner.nodes.onboarding_form_node import show_onboarding_form


SAMPLE = (
    "Plan a 10-day cultural journey to Japan focusing on traditional temples, tea ceremonies, "
    "vibrant street food in Osaka, and modern art in Tokyo with a budget of $3,500."
)


def test_a_chat_sentence_fills_the_brief_without_turning_days_into_adults():
    found = read_brief(SAMPLE)
    assert found["destination"] == "Japan"
    assert found["duration_days"] == 10
    assert found["budget_amount"] == 3500
    assert found["currency"] == "USD"
    assert found["places_to_visit"] == ["Osaka", "Tokyo"]
    assert found["interests"] == ["traditional temples", "tea ceremonies", "vibrant street food", "modern art"]
    assert "adults" not in found


def test_the_form_opens_with_those_facts():
    shown = show_onboarding_form({
        "user_message": SAMPLE,
        "destination": "Japan",
        "duration_days": 10,
        "planning_preferences": {"adults": 10},
        "places_to_visit": [],
    })
    values = shown["ui_action"]["values"]
    assert values["destination"] == "Japan"
    assert values["duration_days"] == 10
    assert values["budget_amount"] == 3500
    assert values["currency"] == "USD"
    assert values["places_to_visit"] == ["Osaka", "Tokyo"]
    assert values["planning_preferences"]["interests"][0] == "traditional temples"
    assert "adults" not in values["planning_preferences"]
