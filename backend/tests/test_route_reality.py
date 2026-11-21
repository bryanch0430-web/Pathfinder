"""V5 travel feasibility uses known coordinates and never needs live map calls in tests."""

from unittest.mock import AsyncMock, patch

from app.agents.trip_planner.copilot import engine
from app.services import route_reality


def _planner():
    state = engine.new_copilot(destination="Kyoto", duration_days=2, currency="JPY",
                               preferences={"pace": "balanced", "travel_mode": "transit"},
                               places=[], budget_target=None, overrides={})
    state["route_points"]["base:kyoto"] = {"lat": 35.0116, "lon": 135.7681}
    return state


def test_a_routed_time_is_used_for_transit_as_well_as_driving():
    from app.services.route_metrics import route_mode
    assert route_mode(1.2) == "WALK"
    assert route_mode(8) == "TRANSIT"
    assert route_mode(40) == "DRIVE"
    a, b = (35.01, 135.76), (35.02, 135.77)
    state = {"route_times": {route_reality.pair_key(a, b): 18}}
    found = route_reality.leg(state, a, b, "transit")
    assert found["basis"] == "routed" and found["hours"] == 0.3
    state = _planner()
    far = engine.make_item({"name": "Sapporo", "lat": 43.0621, "lon": 141.3544,
                            "duration_hours": 1, "time_of_day": "afternoon"})
    engine.merge_pool(state, [far])
    events, blocked, _ = engine.apply_ops(state, [{"op": "add", "name": "Sapporo", "force": True}])
    assert not events
    assert state["days"][0]["items"] == []
    assert "unreachable" in {reason["code"] for reason in blocked[0]["reasons"]}
    from app.agents.trip_planner.copilot.graph import turn_changes
    receipt = turn_changes({"blocked": [{"name": "Sapporo", "day": 1,
                                          "reasons": blocked[0]["reasons"]}]})
    assert "too far from Kyoto" in receipt[0]["text"]
    assert "retry" not in receipt[0]


def test_near_place_fits_and_day_hours_include_real_travel():
    state = _planner()
    near = engine.make_item({"name": "Nishiki Market", "lat": 35.0045, "lon": 135.7645,
                             "duration_hours": 2, "time_of_day": "afternoon"})
    engine.merge_pool(state, [near])
    events, blocked, _ = engine.apply_ops(state, [{"op": "add", "name": "Nishiki Market"}])
    assert events and blocked == []
    total = engine.budget_status(state)["by_day"][0]
    assert 2 < total["hours"] < 4
    assert total["travel_hours"] > 0
    assert total["unverified_legs"] == 0


def test_draft_and_revision_flag_impossible_days_without_live_lookups():
    days = [{"day": 1, "base": "Kyoto", "items": [
        {"name": "Sapporo", "lat": 43.0621, "lon": 141.3544,
         "duration_hours": 1, "time_of_day": "afternoon"}]}]
    initial = {"route_points": {"base:kyoto": {"lat": 35.0116, "lon": 135.7681}}}
    with patch.object(route_reality, "resolve_points", new_callable=AsyncMock), \
         patch.object(route_reality, "resolve_roads", new_callable=AsyncMock):
        import asyncio
        result = asyncio.run(route_reality.assess_itinerary(days, "Japan", "transit", "balanced", initial))
        revised = asyncio.run(route_reality.assess_itinerary(days, "Japan", "transit", "balanced", result))
    assert result["warnings"][0]["severity"] == "impossible"
    assert revised["warnings"][0]["severity"] == "impossible"
    assert result["warnings"][0]["travel_hours"] > 8


def test_a_revision_looks_up_only_the_day_that_changed():
    import asyncio
    before = [
        {"day": 1, "base": "Tokyo", "items": [{"name": "Senso-ji"}]},
        {"day": 2, "base": "Osaka", "items": [{"name": "Dotonbori"}]},
    ]
    after = [
        {"day": 1, "base": "Tokyo", "items": [{"name": "Senso-ji"}, {"name": "teamLab"}]},
        {"day": 2, "base": "Osaka", "items": [{"name": "Dotonbori"}]},
    ]
    assert route_reality.changed_days(before, after) == {1}
    seen: list[str] = []

    async def capture(_state, requests, per_turn=3):
        seen.extend(key for key, _query in requests)
        assert per_turn == 3

    with patch.object(route_reality, "resolve_points", new=capture), \
         patch.object(route_reality, "resolve_roads", new_callable=AsyncMock):
        asyncio.run(route_reality.assess_itinerary(after, "Japan", "transit", "balanced", {}, {1}))
    assert any("teamlab" in key for key in seen)
    assert not any("dotonbori" in key for key in seen)


def test_revision_chat_keeps_the_change_and_drops_the_day_list():
    from app.agents.trip_planner.nodes.planning_trip_node import revision_visible
    opening, started = revision_visible(
        "Done: teamLab is now on day 1. The other days are unchanged.\n\n"
        "Day 1 — Tokyo\n- teamLab\nDay 2 — Osaka\n- Dotonbori\n"
    )
    assert started
    assert opening == "Done: teamLab is now on day 1. The other days are unchanged."
    assert "Dotonbori" not in opening


def test_unlocated_place_stays_provisional_not_impossible():
    state = _planner()
    unknown = [{"name": f"Unknown local place {n}", "duration_hours": 0.1} for n in range(14)]
    warnings = route_reality.check_plan_days(state, [{"day": 1, "base": "Kyoto", "items": unknown}],
                                              "transit", 8)
    assert route_reality.day_travel(state, "Kyoto", unknown, "transit")["hours"] >= 8
    assert route_reality.day_travel(state, "Kyoto", unknown, "transit")["known_hours"] == 0
    assert not any(reason["code"] == "unreachable" for reason in
                   engine.check_add(state, 1, engine.make_item(unknown[0]))["reasons"])
    assert warnings[0]["severity"] in ("tight", "unverified")
