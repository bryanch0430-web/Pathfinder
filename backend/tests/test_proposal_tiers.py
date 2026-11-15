"""What an edit asks of the traveller: nothing for one stop, a tick per change for several stops, one whole-edit for the rest."""

from __future__ import annotations

from plan_samples import make_plan, make_route_plan, make_two_city_plan, stop
from test_route_edit_turns import Trip, _run

from app.application.proposals import (
    apply_change,
    is_stops_only,
    proposal_changes,
    review_tier,
)
from app.config import Settings
from app.domain.models import Money, TripPlan

TWO = [("Kyoto", 2), ("Osaka", 2)]


def _copy(plan: TripPlan) -> TripPlan:
    return plan.model_copy(deep=True)


# -- is_stops_only -----------------------------------------------------------------------------------------------------


def test_two_plans_that_differ_only_in_stops_are_stops_only() -> None:
    old = make_two_city_plan()
    new = _copy(old)
    new.days[0].items = []
    new.days[2].items.append(stop("item_new", "Gion walk", "14:00", "15:00"))

    assert is_stops_only(old, new) and is_stops_only(old, old)


def test_anything_that_is_not_a_stop_makes_it_more_than_stops() -> None:
    old = make_two_city_plan()

    def changed(edit) -> TripPlan:
        new = _copy(old)
        edit(new)
        return new

    edits = {
        "a city": lambda p: setattr(p.segments[1].destination, "name", "Nara"),
        "a city's dates": lambda p: setattr(
            p.segments[1],
            "dates",
            p.segments[1].dates.model_copy(
                update={"end": p.segments[1].dates.end.replace(day=p.segments[1].dates.end.day - 1)}
            ),
        ),
        "a leg": lambda p: setattr(p.legs[1], "to_place", "Nara"),
        "a chosen transport option": lambda p: [
            setattr(t, "selected", t.id == "t_ab_bus")
            for t in p.tickets
            if t.leg_id == "leg_ab"
        ],
        "a stay's name": lambda p: setattr(p.hotels[1], "name", "Other Inn"),
        "a stay's dates": lambda p: setattr(
            p.hotels[0],
            "check_out",
            p.hotels[0].check_out.replace(day=p.hotels[0].check_out.day - 1),
        ),
        "a second stay": lambda p: p.hotels.append(
            p.hotels[0].model_copy(update={"id": "hotel_c", "name": "Third Inn"})
        ),
        "the party size": lambda p: setattr(p, "party_size", 3),
        "the budget": lambda p: setattr(p, "budget", Money(amount=999, currency="HKD")),
        "the origin": lambda p: setattr(p, "origin", "Tokyo"),
    }
    stops_only = {
        name
        for name, edit in edits.items()
        if is_stops_only(old, changed(edit))
    }

    assert stops_only == set(), f"treated as stops only: {stops_only}"


# -- proposal_changes and the tier -----------------------------------------------------------------------------------


def test_a_second_hotel_and_a_new_party_size_make_one_whole_edit_and_accepting_it_stores_both() -> (
    None
):
    old = make_plan(days=2, with_tickets=False)
    new = _copy(old)
    new.hotels.append(
        old.hotels[0].model_copy(update={"id": "hotel_second", "name": "Second Inn"})
    )
    new.party_size = 4
    new.days[0].items = new.days[0].items[:-1]

    changes = proposal_changes(
        old, new, summary="Added a second hotel and made it four travellers."
    )

    assert [c["id"] for c in changes] == ["whole-edit"]
    assert changes[0]["label"] == "Added a second hotel and made it four travellers."
    assert (
        changes[0]["detail"]
        == "Accepting applies every part of this edit; rejecting keeps your plan as it is."
    )
    assert (
        changes[0]["status"] == "pending" and review_tier(old, new, changes) == "whole"
    )
    accepted = apply_change(old, changes[0])
    assert [h.name for h in accepted.hotels] == [
        old.hotels[0].name,
        "Second Inn",
    ] and accepted.party_size == 4
    assert accepted.version == old.version + 1 and len(accepted.days[0].items) == len(
        new.days[0].items
    )


def test_a_route_change_on_the_two_city_plan_is_one_whole_edit() -> None:
    old = make_route_plan(["Kyoto", "Osaka"])
    new = make_route_plan(["Kyoto", "Nara"])

    changes = proposal_changes(old, new)

    assert [c["id"] for c in changes] == ["whole-edit"] and review_tier(
        old, new, changes
    ) == "whole"


def test_choosing_another_option_on_a_leg_is_one_whole_edit() -> None:
    old = make_two_city_plan()
    new = _copy(old)
    for ticket in new.tickets:
        if ticket.leg_id == "leg_ab":
            ticket.selected = ticket.id == "t_ab_bus"

    changes = proposal_changes(old, new)

    assert [c["id"] for c in changes] == ["whole-edit"]
    assert [
        t.id
        for t in apply_change(old, changes[0]).tickets
        if t.selected and t.leg_id == "leg_ab"
    ] == ["t_ab_bus"]


def test_the_label_falls_back_to_the_edits_own_summary() -> None:
    old = make_plan(days=2, with_tickets=False)
    new = _copy(old)
    new.party_size = 5
    new.change_summary = "Made it five travellers."

    assert proposal_changes(old, new)[0]["label"] == "Made it five travellers."
    assert proposal_changes(old, new.model_copy(update={"change_summary": ""}))[0][
        "label"
    ]


def test_one_stop_removed_is_one_change_that_needs_no_click() -> None:
    old = make_plan(days=2, with_tickets=False)
    new = _copy(old)
    removed = new.days[1].items.pop(0)

    changes = proposal_changes(old, new)

    assert [c["id"] for c in changes] == [f"drop-{removed.id}"]
    assert review_tier(old, new, changes) == "direct"


def test_one_stop_added_replaced_moved_or_retimed_needs_no_click() -> None:
    old = make_plan(days=3, with_tickets=False)

    added = _copy(old)
    added.days[2].items.append(
        stop("item_extra", "Philosopher's Path", "17:00", "18:00")
    )
    replaced = _copy(old)
    replaced.days[1].items[0] = stop(
        "item_swap",
        "Nijo Castle",
        replaced.days[1].items[0].start,
        replaced.days[1].items[0].end,
    )
    moved = _copy(old)
    mover = moved.days[0].items.pop(0)
    moved.days[2].items.append(mover)
    retimed = _copy(old)
    retimed.days[1].items[0].start, retimed.days[1].items[0].end = "07:00", "07:30"

    for new in (added, replaced, moved, retimed):
        changes = proposal_changes(old, new)
        assert len(changes) == 1, [c["id"] for c in changes]
        assert review_tier(old, new, changes) == "direct", changes[0]["label"]


def test_several_stops_and_nothing_else_keep_the_separate_ticks() -> None:
    old = make_plan(days=3, with_tickets=False)
    new = _copy(old)
    first, second = new.days[0].items.pop(0), new.days[1].items.pop(0)

    changes = proposal_changes(old, new)

    assert sorted(c["id"] for c in changes) == sorted(
        [f"drop-{first.id}", f"drop-{second.id}"]
    )
    assert review_tier(old, new, changes) == "stops" and all(
        c["id"] != "whole-edit" for c in changes
    )


def test_a_move_that_also_retimes_another_stop_is_a_tick_not_a_direct_edit() -> None:
    old = make_plan(days=3, with_tickets=False)
    new = _copy(old)
    mover = new.days[0].items.pop(0)
    mover.start, mover.end = new.days[2].items[0].start, new.days[2].items[0].end
    new.days[2].items.append(mover)
    new.days[2].items[0].start, new.days[2].items[0].end = "20:00", "21:00"

    changes = proposal_changes(old, new)

    assert len(changes) == 1 and review_tier(old, new, changes) == "stops"


def test_nothing_changed_is_no_change_at_all() -> None:
    old = make_plan(days=2, with_tickets=False)

    assert proposal_changes(old, _copy(old)) == []
    assert review_tier(old, _copy(old), []) == "direct"


# -- through a turn on the web (review on) ---------------------------------------------------------------------------------


def _review_turn(trip: Trip, message: str) -> list:
    async def go():
        trip.rig.llm.route = {
            "intent": "modify",
            "confidence": 0.95,
            "parts": ["attraction"],
        }
        return [
            e
            async for e in trip.rig.turns.handle_turn(
                session_id=trip.session.id, message=message, review=True
            )
        ]

    return go()


def test_a_turn_that_removes_one_stop_applies_it_with_no_proposal(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        before = trip.plan
        events = await _review_turn(trip, "remove Sumiyoshi Taisha")
        return before, trip.plan, events

    before, after, events = _run(settings, TWO, body)

    held = [
        e for e in events if e.event == "proposal" and e.data["proposal"]["changes"]
    ]
    assert held == []
    assert after.version == before.version + 1
    assert "Sumiyoshi Taisha" not in [i.name for _, i in after.iter_items()]


def test_a_turn_that_changes_a_stay_holds_one_whole_edit_and_writes_nothing(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        before = trip.plan
        events = await _review_turn(trip, "a cheaper hotel in Osaka")
        return before, trip.plan, events

    before, after, events = _run(settings, TWO, body)

    held = [e for e in events if e.event == "proposal"]
    assert [c["id"] for c in held[-1].data["proposal"]["changes"]] == ["whole-edit"]
    assert after.version == before.version and [s.name for s in after.hotels] == [
        s.name for s in before.hotels
    ]


def test_a_turn_that_thins_several_days_holds_separate_ticks_and_no_whole_edit(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        plan = trip.plan.model_copy(deep=True)
        for day in plan.days[1:3]:
            if not day.items:
                continue
            extra = day.items[0].model_copy(update={"id": f"{day.items[0].id}_more", "name": f"Second sight {day.date}", "start": "16:00", "end": "17:00"})
            day.items.append(extra)
        plan.version += 1
        trip.ledger.store.save_plan(plan, "Two sights a day")
        before = trip.plan
        events = await _review_turn(trip, "Only one place a day, please.")
        return before, trip.plan, events

    before, after, events = _run(settings, TWO, body)

    held = [e for e in events if e.event == "proposal"]
    ids = [c["id"] for c in held[-1].data["proposal"]["changes"]]
    assert len(ids) >= 2 and "whole-edit" not in ids and all(i.startswith("drop-") for i in ids), ids
    assert after.version == before.version
