"""A multi-city plan is made of single-city plans: the context each is planned with, how they are put together, and
how a stitched plan is taken apart again."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from plan_samples import make_plan

from app.application.route_plan import build_segments
from app.application.stitch import city_context, split, stitch
from app.domain.models import (
    DateRange,
    Destination,
    TicketOption,
    TransportLeg,
    TripContext,
    TripPlan,
    TripSegment,
)

START = date(2026, 12, 3)


def _context(**more) -> TripContext:
    return TripContext(
        session_id="s1",
        destination="Kyoto",
        origin="Hong Kong",
        start_date=START,
        end_date=date(2026, 12, 10),
        days=8,
        party_size=2,
        budget=9000,
        currency="HKD",
        preferences=["museums"],
        **more,
    )


def _segments(nights: list[int]) -> list[TripSegment]:
    names = ["Kyoto", "Osaka", "Tokyo"]
    return build_segments(
        [Destination(name=n) for n in names[: len(nights)]], nights, START
    )


def _part(index: int, segment: TripSegment, city: str) -> TripPlan:
    """A single-city plan for the segment's dates (arrival to departure day) with ids that collide across cities."""
    base = make_plan(
        days=(segment.dates.end - segment.dates.start).days + 1, with_tickets=False
    )
    data = base.model_dump(mode="json")
    shift = (segment.dates.start - START).days
    for day in data["days"]:
        day["date"] = (
            date.fromisoformat(day["date"]) + timedelta(days=shift)
        ).isoformat()
    data["dates"] = {
        "start": segment.dates.start.isoformat(),
        "end": segment.dates.end.isoformat(),
    }
    data["destination"] = {"name": city}
    data["segments"] = []
    for stay in data["hotels"]:
        stay["check_in"], stay["check_out"] = (
            segment.dates.start.isoformat(),
            segment.dates.end.isoformat(),
        )
    for day in data["days"]:
        day["segment_id"] = ""
    return TripPlan.model_validate(data)


def _parts(nights: list[int]) -> tuple[list[TripSegment], list[TripPlan]]:
    segments = _segments(nights)
    return segments, [_part(n, s, s.destination.name) for n, s in enumerate(segments)]


def _stitch(nights=(2, 2), legs=None, tickets=None, context=None):
    segments, parts = _parts(list(nights))
    plan = stitch(
        parts,
        segments,
        legs or [],
        tickets or [],
        context or _context(),
        session_id="s1",
        version=1,
        trip_id="trip_1",
    )
    return segments, parts, plan


def _train(arrive_hour: int, depart_hour: int = 9) -> TicketOption:
    return TicketOption(
        id="t_leg_1",
        mode="train",
        leg_id="leg_1",
        name="Kyoto to Osaka by train",
        depart_at=datetime(2026, 12, 5, depart_hour, 0),
        arrive_at=datetime(2026, 12, 5, arrive_hour, 0),
        estimated=True,
        selected=True,
    )


def test_a_middle_city_is_planned_alone_from_its_arrival_to_the_day_before_it_is_left() -> (
    None
):
    segments = _segments([2, 3, 2])

    middle = city_context(_context(), segments[1], last=False, budget=1500.0)

    assert (middle.destination, middle.stops, middle.origin) == ("Osaka", [], "")
    assert (middle.start_date, middle.end_date, middle.days) == (
        date(2026, 12, 5),
        date(2026, 12, 7),
        3,
    )
    assert (middle.budget, middle.party_size, middle.currency, middle.preferences) == (
        1500.0,
        2,
        "HKD",
        ["museums"],
    )


def test_the_last_city_keeps_its_departure_day_and_no_budget_stays_none() -> None:
    segments = _segments([2, 3, 2])

    last = city_context(_context(), segments[2], last=True, budget=None)

    assert (last.start_date, last.end_date, last.days, last.budget) == (
        date(2026, 12, 8),
        date(2026, 12, 10),
        3,
        None,
    )


def test_the_parts_become_one_valid_plan_with_unique_ids() -> None:
    _, _, plan = _stitch()

    assert TripPlan.model_validate(plan.model_dump(mode="json")).dates == DateRange(
        start=START, end=date(2026, 12, 7)
    )
    assert [(d.date, d.segment_id) for d in plan.days] == [
        (date(2026, 12, 3), "seg_1"),
        (date(2026, 12, 4), "seg_1"),
        (date(2026, 12, 5), "seg_2"),
        (date(2026, 12, 6), "seg_2"),
        (date(2026, 12, 7), "seg_2"),
    ]
    ids = [item.id for _, item in plan.iter_items()] + [stay.id for stay in plan.hotels]
    assert len(ids) == len(set(ids))
    assert [(s.id, s.check_in, s.check_out, s.segment_id) for s in plan.hotels] == [
        ("hotel_1", date(2026, 12, 3), date(2026, 12, 5), "seg_1"),
        ("s2_hotel_1", date(2026, 12, 5), date(2026, 12, 7), "seg_2"),
    ]
    assert (
        plan.destination.name == "Kyoto"
        and plan.origin == "Hong Kong"
        and plan.party_size == 2
    )
    assert plan.summary.startswith("Kyoto → Osaka: 5 days.")


def test_the_day_a_city_is_left_is_the_next_citys_arrival_day() -> None:
    _, parts, plan = _stitch()

    arrival = next(d for d in plan.days if d.date == date(2026, 12, 5))
    second_part_first_day = next(
        d for d in parts[1].days if d.date == date(2026, 12, 5)
    )

    assert [i.name for i in arrival.items] == [
        i.name for i in second_part_first_day.items
    ]


def test_a_travel_day_starts_with_the_chosen_leg_and_drops_what_you_arrive_too_late_for() -> (
    None
):
    leg = TransportLeg(
        id="leg_1",
        from_place="Kyoto",
        to_place="Osaka",
        depart_date=date(2026, 12, 5),
        kind="between",
    )
    _, _, plan = _stitch(legs=[leg], tickets=[_train(11)])

    day = next(d for d in plan.days if d.date == date(2026, 12, 5))

    first = day.items[0]
    assert (first.id, first.type.value, first.name) == (
        "transit_leg_1",
        "transit",
        "Kyoto → Osaka (train)",
    )
    assert (first.start, first.end, first.notes) == ("09:00", "11:00", "Estimated time")
    assert [item.name for item in day.items[1:]] == ["Soba house 1", "Izakaya 1"]
    assert (
        "Temple 1.1 was left out of 2026-12-05: you arrive at 11:00."
        in plan.data_quality.notes
    )
    assert day.items[1].transfer is None


def test_a_flight_home_removes_what_would_run_into_it() -> None:
    leg = TransportLeg(
        id="leg_home",
        from_place="Osaka",
        to_place="Hong Kong",
        depart_date=date(2026, 12, 7),
        kind="return",
    )
    flight = TicketOption(
        id="t_home",
        mode="flight",
        leg_id="leg_home",
        name="Cathay KIX-HKG",
        depart_at=datetime(2026, 12, 7, 12, 0),
        arrive_at=datetime(2026, 12, 7, 15, 0),
        selected=True,
    )

    _, _, plan = _stitch(legs=[leg], tickets=[flight])

    last = plan.days[-1]
    assert all(item.end <= "10:00" for item in last.items)
    assert any("was left out of 2026-12-07" in note for note in plan.data_quality.notes)


def test_a_locked_stop_is_kept_on_a_travel_day() -> None:
    leg = TransportLeg(
        id="leg_1",
        from_place="Kyoto",
        to_place="Osaka",
        depart_date=date(2026, 12, 5),
        kind="between",
    )
    segments, parts = _parts([2, 2])
    for day in parts[1].days:
        if day.date == date(2026, 12, 5):
            day.items[0].locked = True
    plan = stitch(
        parts,
        segments,
        [leg],
        [_train(11)],
        _context(),
        session_id="s1",
        version=1,
        trip_id=None,
    )

    day = next(d for d in plan.days if d.date == date(2026, 12, 5))
    assert any(item.locked for item in day.items)


def test_taking_a_stitched_plan_apart_gives_the_cities_back() -> None:
    leg = TransportLeg(
        id="leg_1",
        from_place="Kyoto",
        to_place="Osaka",
        depart_date=date(2026, 12, 5),
        kind="between",
    )
    segments, parts, plan = _stitch(legs=[leg], tickets=[_train(8, 6)])

    again = split(plan)

    assert len(again) == 2
    for original, back, segment in zip(parts, again, segments):
        kept = {d.date: [i.name for i in d.items] for d in back.days}
        wanted = {
            d.date: [i.name for i in d.items]
            for d in original.days
            if segment.dates.start <= d.date
            and (d.date < segment.dates.end or segment is segments[-1])
        }
        assert {day: names for day, names in kept.items() if names} == {
            day: names for day, names in wanted.items() if names
        }
        assert back.destination.name == segment.destination.name
        assert not any(i.type.value == "transit" for d in back.days for i in d.items)
    assert [stay.id for stay in again[1].hotels] == ["hotel_1"]
    again_plan = stitch(
        again,
        segments,
        plan.legs,
        plan.tickets,
        _context(),
        session_id="s1",
        version=2,
        trip_id="trip_1",
    )
    assert [i.name for _, i in again_plan.iter_items()] == [
        i.name for _, i in plan.iter_items()
    ]
