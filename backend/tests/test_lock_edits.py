"""Chat edits respect locked stays and legs, and say so when a route change releases a lock."""

from __future__ import annotations

from test_route_edit_turns import Trip, _run

from app.config import Settings

HOTEL_ROUTE = {"intent": "modify", "confidence": 0.95, "parts": ["hotel"]}
THREE = [("Kyoto", 2), ("Osaka", 2), ("Tokyo", 2)]


def _city_of(plan, segment_id: str) -> str:
    return next(s.destination.name for s in plan.segments if s.id == segment_id)


def _lock_stay(trip: Trip, city: str) -> None:
    plan = trip.plan
    for stay in plan.hotels:
        if _city_of(plan, stay.segment_id) == city:
            stay.locked = True
    trip.ledger.store.save_plan(plan, None, replace=True)


def _lock_leg(trip: Trip, a: str, b: str) -> None:
    plan = trip.plan
    leg = next(l for l in plan.legs if (l.from_place, l.to_place) == (a, b))
    leg.locked = True
    trip.ledger.store.save_plan(plan, None, replace=True)


def _reply(events) -> str:
    return "".join(e.data["text"] for e in events if e.event == "token")


def _stay_in(plan, city: str):
    return next(s for s in plan.hotels if _city_of(plan, s.segment_id) == city)


def _leg(plan, a: str, b: str):
    return next((l for l in plan.legs if (l.from_place, l.to_place) == (a, b)), None)


RELEASED = (
    "Your locked choice for {what} no longer fits the new route, so it is open again."
)


def test_a_cheaper_hotel_edit_in_the_second_city_leaves_its_locked_stay_alone(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        _lock_stay(trip, "Osaka")
        before = _stay_in(trip.plan, "Osaka")
        events = await trip.turn("Make the hotel in Osaka cheaper", HOTEL_ROUTE)
        return before, trip.plan, _reply(events)

    before, after, reply = _run(settings, THREE, body)

    kept = _stay_in(after, "Osaka")
    assert (kept.id, kept.name, kept.locked) == (before.id, before.name, True)
    assert f"{before.name} in Osaka is locked in and was left as it is." in reply


def test_make_the_hotels_cheaper_everywhere_skips_a_locked_stay_and_says_so(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        _lock_stay(trip, "Kyoto")
        before = _stay_in(trip.plan, "Kyoto")
        events = await trip.turn("Make the hotels cheaper everywhere", HOTEL_ROUTE)
        return before, trip.plan, _reply(events)

    before, after, reply = _run(settings, THREE, body)

    assert _stay_in(after, "Kyoto").id == before.id and _stay_in(after, "Kyoto").locked
    assert f"{before.name} in Kyoto is locked in and was left as it is." in reply


def test_a_route_change_keeps_a_locked_leg_between_the_same_places_on_the_same_day(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        _lock_leg(trip, "Kyoto", "Osaka")
        before = trip.plan
        await trip.turn("remove Tokyo")
        return before, trip.plan

    before, after = _run(settings, THREE, body)

    kept = _leg(after, "Kyoto", "Osaka")
    assert kept is not None and kept.locked is True
    chosen_before = next(
        t for t in before.options_for(_leg(before, "Kyoto", "Osaka").id) if t.selected
    )
    chosen_after = next(t for t in after.options_for(kept.id) if t.selected)
    assert chosen_after.name == chosen_before.name
    assert "no longer fits" not in after.change_summary


def test_a_route_change_that_removes_a_locked_leg_releases_it_and_says_so(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        _lock_leg(trip, "Osaka", "Tokyo")
        await trip.turn("remove Tokyo")
        return trip.plan

    after = _run(settings, THREE, body)

    assert RELEASED.format(what="Osaka → Tokyo") in after.change_summary
    assert all(not leg.locked for leg in after.legs)


def test_a_route_change_that_moves_the_dates_of_a_locked_stay_releases_it_and_says_so(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        _lock_stay(trip, "Osaka")
        await trip.turn("3 nights in Osaka")
        return trip.plan

    after = _run(settings, THREE, body)

    assert RELEASED.format(what="the stay in Osaka") in after.change_summary
    assert _stay_in(after, "Osaka").locked is False


def test_a_route_change_that_drops_the_city_of_a_locked_stay_says_so(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        _lock_stay(trip, "Osaka")
        await trip.turn("remove Osaka")
        return trip.plan

    after = _run(settings, THREE, body)

    assert RELEASED.format(what="the stay in Osaka") in after.change_summary


def test_a_locked_stay_in_a_city_the_route_change_does_not_touch_stays_locked(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        _lock_stay(trip, "Tokyo")
        before = _stay_in(trip.plan, "Tokyo")
        await trip.turn("remove Osaka")
        return before, trip.plan

    before, after = _run(settings, THREE, body)

    kept = _stay_in(after, "Tokyo")
    assert kept.name == before.name and kept.locked is True
    assert "stay in Tokyo" not in after.change_summary


def test_an_explicit_swap_of_the_locked_stay_still_swaps_it_and_the_reply_says_so(settings: Settings) -> None:
    """Locking protects against a general request ("cheaper everywhere"); naming the stay to swap is the traveller's say."""

    async def body(trip: Trip):
        _lock_stay(trip, "Osaka")
        before = _stay_in(trip.plan, "Osaka")
        events = await trip.turn("Swap the hotel in Osaka for another one", HOTEL_ROUTE)
        return before, trip.plan, _reply(events)

    before, after, reply = _run(settings, THREE, body)

    assert "locked in and was left as it is" not in reply
    assert _stay_in(after, "Osaka").name != before.name or "hotel" in reply.lower()
