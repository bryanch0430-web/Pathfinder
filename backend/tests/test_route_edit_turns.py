"""Chat edits that change the route of a trip, run as real turns on a stored trip with the offline sources."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Self

from multi_city_rig import multi_city_rig, route_context

from app.application.validation import _route_errors
from app.config import Settings
from app.eval_metering import RunLedger

PLAN_ROUTE = {"intent": "plan", "confidence": 0.95}
MODIFY_ROUTE = {"intent": "modify", "confidence": 0.95, "parts": ["attraction"]}


class Trip:
    """A stored trip and the turns run on it; ``searches`` counts the hotel and flight searches made so far."""

    def __init__(self, settings: Settings, stops, **more: Any) -> None:
        self.settings = settings.model_copy(
            update={
                "preplan_budget_seconds": 15,
                "agent_timeout_seconds": 8,
                "enable_flights": True,
            }
        )
        self.stops, self.more = stops, more

    async def __aenter__(self) -> Self:
        self.ledger = RunLedger()
        self.rig = multi_city_rig(self.settings, self.ledger)
        self.session = self.ledger.store.create_session("guest-edit-route")
        self.ledger.store.save_context(
            route_context(self.session.id, self.stops, **self.more)
        )
        await self.turn("Plan this trip", PLAN_ROUTE)
        return self

    async def __aexit__(self, *_: object) -> None:
        self.ledger.close()

    async def turn(self, message: str, route: dict[str, Any] = MODIFY_ROUTE, *, scope_item_id: str | None = None) -> list:
        self.rig.llm.route = route
        return [
            e
            async for e in self.rig.turns.handle_turn(
                session_id=self.session.id, message=message, scope_item_id=scope_item_id
            )
        ]

    @property
    def plan(self):
        return self.ledger.store.get_plan(self.session.id)

    @property
    def context(self):
        return self.ledger.store.get_context(self.session.id)

    def searches(self, tool: str) -> int:
        return sum(1 for call in self.ledger.store.tool_calls() if call["tool"] == tool)


def _run(settings: Settings, stops, body, **more: Any):
    async def main():
        async with Trip(settings, stops, **more) as trip:
            return await body(trip)

    return asyncio.run(main())


def _names(plan, segment_id: str) -> list[list[str]]:
    return [[i.name for i in d.items if i.type.value != "transit"] for d in plan.days if d.segment_id == segment_id]


def test_dropping_the_middle_city_gives_its_nights_to_the_one_before_and_keeps_the_last_as_it_was(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        before = trip.plan
        events = await trip.turn("remove Osaka")
        return before, trip.plan, events, trip.context

    before, after, events, context = _run(
        settings, [("Kyoto", 2), ("Osaka", 2), ("Tokyo", 2)], body
    )

    assert [(s.destination.name, s.dates.night_count) for s in after.segments] == [
        ("Kyoto", 4),
        ("Tokyo", 2),
    ]
    assert after.version == before.version + 1 and after.dates == before.dates
    tokyo_before, tokyo_after = before.segments[2], after.segments[1]
    assert tokyo_before.dates == tokyo_after.dates
    assert _names(after, tokyo_after.id) == _names(before, tokyo_before.id)
    assert [s.name for s in after.stays_in(tokyo_after.id)] == [
        s.name for s in before.stays_in(tokyo_before.id)
    ]
    assert "Removed Osaka; its 2 nights went to Kyoto." in after.change_summary
    assert [(s.city, s.nights) for s in context.stops] == [("Kyoto", 4), ("Tokyo", 2)]
    assert not _route_errors(after)
    assert any(e.event == "plan" for e in events)


def test_adding_a_city_plans_only_the_city_that_is_new_and_the_ones_whose_dates_moved(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        hotels, flights, before = (
            trip.searches("hotels.search"),
            trip.searches("flights.search"),
            trip.plan,
        )
        await trip.turn("add Nara after Kyoto")
        return (
            before,
            trip.plan,
            trip.searches("hotels.search") - hotels,
            trip.searches("flights.search") - flights,
        )

    before, after, hotel_searches, flight_searches = _run(
        settings, [("Kyoto", 4), ("Osaka", 2)], body
    )

    assert [(s.destination.name, s.dates.night_count) for s in after.segments] == [
        ("Kyoto", 2),
        ("Nara", 2),
        ("Osaka", 2),
    ]
    # Osaka keeps its dates, so it is not planned or searched again; Kyoto's dates moved and Nara is new.
    assert hotel_searches == 2 and flight_searches == 0
    osaka_before, osaka_after = before.segments[1], after.segments[2]
    # (Its arrival day depends on the leg that brings the traveller there, which changed.)
    assert _names(after, osaka_after.id)[1:] == _names(before, osaka_before.id)[1:]
    assert [leg.id for leg in after.legs] == ["leg_out", "leg_1", "leg_2", "leg_home"]
    assert (
        after.options_for("leg_out")[0].selected
        and sum(t.selected for t in after.tickets) == 4
    )


def test_the_traveller_keeps_the_flight_they_chose_on_a_leg_that_did_not_move(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        plan = trip.plan
        for ticket in plan.options_for("leg_home"):
            ticket.selected = ticket.id == plan.options_for("leg_home")[-1].id
        trip.ledger.store.save_plan(plan, None, replace=True)
        chosen = [t.id for t in trip.plan.options_for("leg_home") if t.selected]
        await trip.turn("3 nights in Osaka")
        return chosen, trip.plan

    chosen, after = _run(settings, [("Kyoto", 2), ("Osaka", 2)], body)

    assert [t.id for t in after.options_for("leg_home") if t.selected] == chosen


def test_more_nights_for_a_city_come_from_the_longest_other_stay(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        await trip.turn("3 nights in Osaka")
        return trip.plan

    after = _run(settings, [("Kyoto", 3), ("Osaka", 1)], body)

    assert [(s.destination.name, s.dates.night_count) for s in after.segments] == [("Kyoto", 1), ("Osaka", 3)]


def test_going_from_two_cities_to_one_looks_the_flights_up_again_as_a_round_trip(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        await trip.turn("remove Osaka")
        return trip.plan, trip.context

    after, context = _run(settings, [("Kyoto", 2), ("Osaka", 2)], body)

    assert len(after.segments) == 1 and [leg.id for leg in after.legs] == ["leg_out"]
    assert after.tickets and sum(ticket.selected for ticket in after.tickets) == 1
    assert (
        "Your earlier flight choice no longer fits the route, so the flights were looked up again."
        in after.change_summary
    )
    assert context.stops == [] and context.destination == "Kyoto"


def test_adding_a_city_to_a_one_city_trip_makes_it_a_trip_of_two(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        await trip.turn("add Osaka for 2 nights")
        return trip.plan, trip.context

    after, context = _run(settings, [("Kyoto", 4)], body)

    assert [(s.destination.name, s.dates.night_count) for s in after.segments] == [
        ("Kyoto", 2),
        ("Osaka", 2),
    ]
    assert [leg.id for leg in after.legs] == ["leg_out", "leg_1", "leg_home"]
    assert all(t.return_depart_at is None for t in after.tickets)
    assert "looked up again" in after.change_summary
    assert [(s.city, s.nights) for s in context.stops] == [("Kyoto", 2), ("Osaka", 2)]


def test_a_stop_the_traveller_locked_in_a_city_that_stays_is_kept(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        plan = trip.plan
        locked = next(
            i
            for d in plan.days
            if d.segment_id == "seg_1"
            for i in d.items
            if i.type.value == "attraction"
        )
        locked.locked = True
        trip.ledger.store.save_plan(plan, None, replace=True)
        await trip.turn("remove Osaka")
        return locked.name, trip.plan

    name, after = _run(settings, [("Kyoto", 2), ("Osaka", 2)], body)

    assert any(i.name == name and i.locked for _, i in after.iter_items())


def test_a_city_visited_twice_asks_which_visit_and_changes_nothing(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        before = trip.plan
        events = await trip.turn("remove Kyoto")
        return before, trip.plan, events

    before, after, events = _run(
        settings, [("Kyoto", 1), ("Osaka", 1), ("Kyoto", 1)], body
    )

    asked = [e for e in events if e.event == "clarify"]
    assert asked and "You visit Kyoto twice" in asked[0].data["question"]
    assert after.version == before.version


def test_a_route_change_that_cannot_be_made_is_a_question(settings: Settings) -> None:
    async def body(trip: Trip):
        events = await trip.turn("add Nara")
        return trip.plan, events

    after, events = _run(settings, [("Kyoto", 1), ("Osaka", 1)], body)

    assert [e for e in events if e.event == "clarify"] and len(after.segments) == 2


def test_a_different_hotel_for_the_last_nights_splits_the_stay(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        before = trip.plan
        await trip.turn("a different hotel for the last 2 nights in Osaka")
        return before, trip.plan

    before, after = _run(settings, [("Kyoto", 2), ("Osaka", 4)], body)

    osaka = after.segments[1]
    first, second = after.stays_in(osaka.id)
    assert (first.check_in, first.check_out) == (
        osaka.dates.start,
        osaka.dates.start + timedelta(days=2),
    )
    assert (second.check_in, second.check_out) == (first.check_out, osaka.dates.end)
    assert first.name != second.name
    assert [s.name for s in after.stays_in(after.segments[0].id)] == [
        s.name for s in before.stays_in(before.segments[0].id)
    ]
    assert not _route_errors(after) and after.version == before.version + 1
    assert (
        any("Osaka" in line.label for line in after.cost.lines)
        and len([ln for ln in after.cost.lines if ln.label.startswith("Hotel")]) == 3
    )


def test_a_split_that_leaves_no_night_for_the_first_hotel_is_asked_about(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        return await trip.turn(
            "another hotel for the last 3 nights in Osaka"
        ), trip.plan

    events, after = _run(settings, [("Kyoto", 2), ("Osaka", 2)], body)

    assert [e for e in events if e.event == "clarify"] and len(after.hotels) == 2
