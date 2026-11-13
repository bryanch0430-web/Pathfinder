"""Chat edits inside the cities of a multi-city plan, and the plan diff of a plan of several cities."""

from __future__ import annotations

from plan_samples import make_plan, make_route_plan
from test_route_edit_turns import Trip, _run

from app.application.modification import ModifyPath
from app.application.multi_edit import _localise_day, _own_id
from app.application.validation import diff_plans
from app.config import Settings

TWO = [("Kyoto", 2), ("Osaka", 2)]


def _dump(plan, segment_id: str):
    return (
        [d.model_dump() for d in plan.days if d.segment_id == segment_id],
        [s.model_dump() for s in plan.stays_in(segment_id)],
    )


def _spy(trip: Trip) -> list[str]:
    """The cities the ordinary edit is run on (it is run on a plan of one city at a time)."""
    ran: list[str] = []
    original = ModifyPath.run

    async def run(self, *, plan, **kwargs):
        if len(plan.segments) == 1:
            ran.append(plan.destination.name)
        return await original(self, plan=plan, **kwargs)

    trip._restore = lambda: setattr(ModifyPath, "run", original)
    ModifyPath.run = run
    return ran


def test_a_stop_named_in_one_city_changes_that_city_only(settings: Settings) -> None:
    async def body(trip: Trip):
        before = trip.plan
        ran = _spy(trip)
        try:
            events = await trip.turn("remove Sumiyoshi Taisha")
        finally:
            trip._restore()
        return before, trip.plan, ran, events

    before, after, ran, events = _run(settings, TWO, body)

    assert ran == ["Osaka"]
    assert "Sumiyoshi Taisha" not in [i.name for _, i in after.iter_items()]
    assert _dump(after, "seg_1") == _dump(before, "seg_1")
    assert after.version == before.version + 1
    diff = next(e for e in events if e.event == "plan").data["diff"]
    osaka = {i.id for d in before.days if d.segment_id == "seg_2" for i in d.items}
    assert {e["item_id"] for e in diff} <= osaka
    assert [e["name"] for e in diff if e["kind"] == "removed"] == ["Sumiyoshi Taisha"]


def test_a_cheaper_hotel_in_one_city_changes_that_citys_stay_and_the_diff_names_it(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        before = trip.plan
        events = await trip.turn("a cheaper hotel in Osaka")
        return before, trip.plan, events

    before, after, events = _run(settings, TWO, body)

    assert after.stays_in("seg_1") == before.stays_in("seg_1")
    assert [s.name for s in after.stays_in("seg_2")] != [
        s.name for s in before.stays_in("seg_2")
    ]
    diff = next(e for e in events if e.event == "plan").data["diff"]
    hotel = next(e for e in diff if e["kind"] == "hotel")
    assert (
        hotel["id"] == after.stays_in("seg_2")[0].id and hotel["segment_id"] == "seg_2"
    )
    assert not any(e["kind"] == "transport" for e in diff)


def test_a_selected_stop_settles_the_city_whatever_the_words_say(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        plan = trip.plan
        stop = next(
            i
            for d in plan.days
            if d.segment_id == "seg_2"
            for i in d.items
            if i.type.value == "attraction"
        )
        ran = _spy(trip)
        try:
            await trip.turn("remove this stop in Kyoto", scope_item_id=stop.id)
        finally:
            trip._restore()
        return stop.name, trip.plan, ran

    name, after, ran = _run(settings, TWO, body)

    assert ran == ["Osaka"]
    assert name not in [
        i.name for d in after.days if d.segment_id == "seg_2" for i in d.items
    ]


def test_a_message_about_no_city_is_run_on_every_city(settings: Settings) -> None:
    async def body(trip: Trip):
        ran = _spy(trip)
        try:
            await trip.turn("only one place a day")
        finally:
            trip._restore()
        return ran

    assert sorted(_run(settings, TWO, body)) == ["Kyoto", "Osaka"]


def test_a_day_the_message_names_is_counted_from_the_first_day_of_its_city() -> None:
    plan = make_route_plan(["Kyoto", "Osaka", "Tokyo"])

    assert _localise_day("make day 4 lighter", plan, 1) == "make day 2 lighter"
    assert (
        _localise_day("make the 6th day lighter", plan, 2) == "make the day 2 lighter"
    )
    assert _localise_day("make it lighter", plan, 1) == "make it lighter"


def test_a_selected_id_is_the_citys_own() -> None:
    assert (
        _own_id("s2_item_x", 2) == "item_x"
        and _own_id("item_x", 1) == "item_x"
        and _own_id(None, 2) is None
    )
    assert _own_id("s3_item_x", 2) == "s3_item_x"


# -- the diff ------------------------------------------------------------------------------------------------------------


def test_the_diff_of_a_single_city_plan_keeps_its_hotel_entry_and_gains_the_stay_id() -> (
    None
):
    old, new = make_plan(), make_plan()
    new.hotels[0] = new.hotels[0].model_copy(
        update={"id": "hotel_2", "name": "Granvia Annex"}
    )

    entry = next(e for e in diff_plans(old, new) if e["kind"] == "hotel")

    assert entry == {
        "kind": "hotel",
        "id": "hotel_2",
        "from": "Hotel Granvia",
        "to": "Granvia Annex",
    }


def test_the_diff_of_a_route_names_cities_nights_stays_and_legs() -> None:
    old = make_route_plan(["Kyoto", "Osaka", "Tokyo"])
    new = make_route_plan(["Kyoto", "Tokyo"], nights=3)
    new.hotels[1] = new.hotels[1].model_copy(update={"name": "Tokyo Annex"})
    changes = diff_plans(old, new)

    kinds = {(e["kind"], e.get("city")) for e in changes}
    assert ("city_removed", "Osaka") in kinds
    assert ("nights", "Kyoto") in kinds and ("nights", "Tokyo") in kinds
    hotel = next(e for e in changes if e["kind"] == "hotel")
    assert (
        hotel["id"] == "hotel_2"
        and hotel["segment_id"] == "seg_2"
        and hotel["to"] == "Tokyo Annex"
    )
    transport = [e for e in changes if e["kind"] == "transport"]
    assert {e["leg_id"] for e in transport} >= {"leg_2"}
    assert not any(e["kind"] == "flight" for e in changes)


def test_a_city_added_and_a_leg_chosen_differently_are_in_the_diff() -> None:
    old = make_route_plan(["Kyoto", "Osaka"])
    new = make_route_plan(["Kyoto", "Osaka", "Tokyo"])
    for ticket in new.tickets:
        if ticket.leg_id == "leg_1":
            ticket.name = "Kyoto to Osaka by bus"

    changes = diff_plans(old, new)

    assert {"kind": "city_added", "city": "Tokyo", "segment_id": "seg_3"} in changes
    assert {
        "kind": "transport",
        "leg_id": "leg_1",
        "from": "Kyoto to Osaka",
        "to": "Kyoto to Osaka by bus",
    } in changes
    assert {
        "kind": "transport",
        "leg_id": "leg_2",
        "from": "",
        "to": "Osaka to Tokyo",
    } in changes
