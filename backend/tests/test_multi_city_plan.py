"""A trip of several cities, planned end to end with the offline sources."""

from __future__ import annotations

import asyncio

import pytest
from multi_city_rig import START, multi_city_rig, route_context, run_turn
from plan_samples import make_two_city_plan

from app.application.asking import _plan_facts
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import PlanInvalidError
from app.eval_metering import RunLedger
from app.evaluation import BASE
from app.infrastructure.http import haversine_km
from app.infrastructure.observability import NoopTracer


def _plan(settings: Settings, stops, **more):
    events, plan, _ = run_turn(settings, route_context("s", stops, **more))
    return events, plan


def test_two_cities_make_one_plan_with_a_stay_in_each_and_the_legs_between(
    settings: Settings,
) -> None:
    _, plan = _plan(settings, [("Kyoto", 2), ("Osaka", 2)])

    assert plan is not None
    assert [(s.id, s.destination.name) for s in plan.segments] == [
        ("seg_1", "Kyoto"),
        ("seg_2", "Osaka"),
    ]
    assert len(plan.days) == 5 and plan.dates.start == START
    assert [stay.segment_id for stay in plan.hotels] == ["seg_1", "seg_2"]
    assert [(stay.check_in, stay.check_out) for stay in plan.hotels] == [
        (plan.segments[0].dates.start, plan.segments[0].dates.end),
        (plan.segments[1].dates.start, plan.segments[1].dates.end),
    ]
    assert [leg.id for leg in plan.legs] == ["leg_out", "leg_1", "leg_home"]
    assert all(plan.options_for(leg.id) for leg in plan.legs)
    assert sum(t.selected for t in plan.tickets) == 3
    assert plan.summary.startswith("Kyoto → Osaka: 5 days.")
    ids = [item.id for _, item in plan.iter_items()] + [stay.id for stay in plan.hotels]
    assert len(ids) == len(set(ids))


def test_every_stop_is_near_its_own_city(settings: Settings) -> None:
    _, plan = _plan(settings, [("Kyoto", 2), ("Tokyo", 2)])

    for day in plan.days:
        city = plan.segment_of(day.date).destination.name
        for item in day.items:
            if item.geo is not None and item.type.value != "transit":
                lat, lng = BASE[city]
                assert haversine_km(lat, lng, item.geo.lat, item.geo.lng) < 80, (
                    day.date,
                    city,
                    item.name,
                )


def test_the_stay_of_each_night_is_in_the_city_of_that_night(
    settings: Settings,
) -> None:
    _, plan = _plan(settings, [("Kyoto", 2), ("Tokyo", 2)])

    for day in plan.days[:-1]:
        stay = plan.stay_on(day.date)
        assert stay is not None and stay.segment_id == day.segment_id


def test_a_travel_day_starts_with_the_leg(settings: Settings) -> None:
    _, plan = _plan(settings, [("Kyoto", 2), ("Osaka", 2)])

    for leg_id, date_ in (
        ("leg_out", plan.days[0].date),
        ("leg_1", plan.segments[1].dates.start),
    ):
        day = next(d for d in plan.days if d.date == date_)
        assert (
            day.items[0].id == f"transit_{leg_id}"
            and day.items[0].type.value == "transit"
        )
    assert plan.cost.total > 0 and plan.cost.lines
    assert any("estimate" in note for note in plan.cost.notes)


def test_a_one_way_trip_has_no_leg_home(settings: Settings) -> None:
    _, plan = _plan(settings, [("Kyoto", 2), ("Osaka", 2)], one_way=True)

    assert [leg.id for leg in plan.legs] == ["leg_out", "leg_1"]


def test_nights_that_add_up_to_more_than_the_dates_are_asked_about_not_planned(
    settings: Settings,
) -> None:
    events, plan = _plan(settings, [("Kyoto", 5), ("Osaka", 5)], nights=7)

    asked = [e for e in events if e.event == "clarify"]
    assert plan is None and asked
    assert (
        asked[0].data["question"]
        == "The nights you gave add up to 10 but the dates cover 7. Which should I follow?"
    )


def test_more_cities_than_the_setting_allows_are_refused(settings: Settings) -> None:
    settings = settings.model_copy(
        update={
            "max_cities": 2,
            "preplan_budget_seconds": 15,
            "agent_timeout_seconds": 8,
        }
    )
    context = route_context("s", [("Kyoto", 1), ("Osaka", 1), ("Tokyo", 1)])

    async def main():
        ledger = RunLedger()
        try:
            rig = multi_city_rig(settings, ledger)
            await rig.plan_path.run(
                context=context,
                session_id="s",
                tracer=NoopTracer(),
                emit=lambda *_: None,
                toolbox=Toolbox(
                    path="plan", tracer=NoopTracer(), store=None, request_id="t"
                ),
            )
        finally:
            ledger.close()

    with pytest.raises(PlanInvalidError, match="up to 2 cities"):
        asyncio.run(main())


def test_a_city_that_cannot_be_located_names_itself(settings: Settings) -> None:
    class Nowhere:
        name = "nowhere"

        async def geocode(self, name, *, country_hint=""):
            from app.domain.errors import ProviderNoData

            raise ProviderNoData("none", source="nowhere")

    settings = settings.model_copy(
        update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8}
    )
    context = route_context("s", [("Kyoto", 1), ("Atlantis", 1)])

    async def main():
        ledger = RunLedger()
        try:
            rig = multi_city_rig(settings, ledger)
            rig.plan_path.registry.geocoder = Nowhere()
            await rig.plan_path.run(
                context=context,
                session_id="s",
                tracer=NoopTracer(),
                emit=lambda *_: None,
                toolbox=Toolbox(
                    path="plan", tracer=NoopTracer(), store=None, request_id="t"
                ),
            )
        finally:
            ledger.close()

    with pytest.raises(PlanInvalidError, match="I could not locate 'Kyoto'"):
        asyncio.run(main())


def test_a_city_visited_twice_is_planned_once_per_visit(settings: Settings) -> None:
    _, plan = _plan(settings, [("Kyoto", 1), ("Osaka", 1), ("Kyoto", 1)])

    assert [s.destination.name for s in plan.segments] == ["Kyoto", "Osaka", "Kyoto"]
    assert len(plan.hotels) == 3 and len({s.id for s in plan.segments}) == 3
    assert [leg.id for leg in plan.legs] == ["leg_out", "leg_1", "leg_2", "leg_home"]


def test_an_edit_on_a_multi_city_plan_is_not_made_yet(settings: Settings) -> None:
    settings = settings.model_copy(
        update={
            "preplan_budget_seconds": 15,
            "agent_timeout_seconds": 8,
            "enable_flights": True,
        }
    )

    async def main():
        ledger = RunLedger()
        try:
            rig = multi_city_rig(settings, ledger)
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-edit")
            context = route_context(session.id, [("Kyoto", 2), ("Osaka", 2)])
            ledger.store.save_context(context)
            _ = [
                e
                async for e in rig.turns.handle_turn(
                    session_id=session.id, message="Plan this trip"
                )
            ]
            before = ledger.store.get_plan(session.id)
            rig.llm.route = {
                "intent": "modify",
                "confidence": 0.95,
                "parts": ["attractions"],
            }
            events = [
                e
                async for e in rig.turns.handle_turn(
                    session_id=session.id, message="make day 2 lighter"
                )
            ]
            return before, ledger.store.get_plan(session.id), events
        finally:
            ledger.close()

    before, after, events = asyncio.run(main())

    text = " ".join(str(e.data) for e in events)
    assert "can't change a multi-city plan from chat yet" in text
    assert [(d.date, [i.name for i in d.items]) for d in after.days] == [
        (d.date, [i.name for i in d.items]) for d in before.days
    ]


def test_a_question_about_a_two_city_plan_is_answered_from_its_facts() -> None:
    plan = make_two_city_plan()

    facts = _plan_facts(
        "which hotel do we stay in and how far is it from the first sight?", plan
    )

    assert "hotel" in facts


def test_a_single_city_trip_is_planned_exactly_as_before_multi_city_planning(settings: Settings) -> None:
    import json
    from pathlib import Path

    from single_city_plan import single_city_plan

    golden = json.loads((Path(__file__).parent / "golden" / "single_city_plan.json").read_text(encoding="utf-8"))

    assert single_city_plan(settings) == golden


def test_a_question_asked_of_a_stored_two_city_plan_gets_an_answer(settings: Settings) -> None:
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8, "enable_flights": True})

    async def main():
        ledger = RunLedger()
        try:
            rig = multi_city_rig(settings, ledger)
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-ask")
            ledger.store.save_context(route_context(session.id, [("Kyoto", 2), ("Osaka", 2)]))
            _ = [e async for e in rig.turns.handle_turn(session_id=session.id, message="Plan this trip")]
            rig.llm.route = {"intent": "ask", "confidence": 0.95}
            return [e async for e in rig.turns.handle_turn(session_id=session.id, message="Which hotel are we in at Osaka?")]
        finally:
            ledger.close()

    events = asyncio.run(main())

    assert not [e for e in events if e.event == "error"]
    assert any(e.event in ("token", "answer") for e in events)
