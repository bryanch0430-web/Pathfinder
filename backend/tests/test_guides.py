"""A chosen guide changes the prompt and the day-by-day ranking. The traveler's own likes still outrank it."""

from app.agents.trip_planner.copilot import engine
from app.agents.trip_planner.guides import GUIDES, guide_leaning
from app.agents.trip_planner.nodes.planning_trip_node import build_plan_generation_prompt


def test_each_guide_has_a_distinct_leaning():
    texts = [guide_leaning({"guide": guide_id}) for guide_id in GUIDES]
    assert all("GUIDE LEANING" in text for text in texts)
    assert len(set(texts)) == 6
    assert guide_leaning({}) == ""
    assert guide_leaning({"guide": "aoi"}) == ""
    assert "traveler's stated pace" in guide_leaning({"guide": "tetra"})
    assert guide_leaning({"guide": "tetra", "guide_shapes_plan": False}) == ""


def test_the_draft_prompt_includes_the_chosen_leaning():
    prompt = build_plan_generation_prompt(
        planning_result={}, destination="Kyoto", duration_days=3, origin="Hong Kong",
        planning_preferences={"guide": "sphere", "pace": "balanced"},
    )
    assert "Sphere-san" in prompt
    assert "after 19:00" in prompt
    assert "pace" in prompt


def test_food_guide_ranks_a_meal_ahead_of_a_museum():
    copilot = engine.new_copilot(
        destination="Tokyo", duration_days=2, currency="JPY",
        preferences={"guide": "octa"}, places=[], budget_target=None, overrides={},
    )
    day = copilot["days"][0]
    meal = {"name": "Tsukiji outer market", "category": "food", "area": "Tsukiji", "tags": ["market", "street food"],
            "why": "breakfast", "basis": "community", "est_cost": 10, "duration_hours": 1}
    museum = {"name": "Nezu Museum", "category": "attraction", "area": "Minami-Aoyama", "tags": ["museum", "art"],
              "why": "garden", "basis": "community", "est_cost": 15, "duration_hours": 2}
    assert engine.score(copilot, meal, day) > engine.score(copilot, museum, day)


def test_turning_the_guide_off_stops_the_food_bias():
    copilot = engine.new_copilot(
        destination="Tokyo", duration_days=2, currency="JPY",
        preferences={"guide": "octa", "guide_shapes_plan": False}, places=[], budget_target=None, overrides={},
    )
    assert copilot["profile"]["guide"] is None
    day = copilot["days"][0]
    meal = {"name": "Tsukiji outer market", "category": "food", "area": "Tsukiji", "tags": ["market"],
            "why": "breakfast", "basis": "community", "est_cost": 10, "duration_hours": 1}
    museum = {"name": "Nezu Museum", "category": "attraction", "area": "Minami-Aoyama", "tags": ["museum"],
              "why": "garden", "basis": "community", "est_cost": 15, "duration_hours": 2}
    assert engine.score(copilot, meal, day) == engine.score(copilot, museum, day)


def test_a_stated_like_still_outranks_the_guide():
    copilot = engine.new_copilot(
        destination="Tokyo", duration_days=2, currency="JPY",
        preferences={"guide": "octa", "interests": ["museums"]}, places=[], budget_target=None, overrides={},
    )
    day = copilot["days"][0]
    meal = {"name": "A market lunch", "category": "food", "area": "Tsukiji", "tags": ["market"],
            "why": "food", "basis": "model_estimate", "est_cost": 10, "duration_hours": 1}
    museum = {"name": "A quiet museum", "category": "attraction", "area": "Ueno", "tags": ["museum"],
              "why": "museums", "basis": "model_estimate", "est_cost": 12, "duration_hours": 2}
    assert engine.score(copilot, museum, day) > engine.score(copilot, meal, day)
