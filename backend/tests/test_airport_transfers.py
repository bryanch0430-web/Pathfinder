"""Airport transfers: how to get between the airport and a city's stay when the traveller flies, chosen and locked like any
other leg. Offline: a fake geocoder (airports) and a fake routes source."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

from plan_samples import make_plan, make_two_city_plan

from app.application.airport_transfers import sync_transfers
from app.application.search_cache import SearchCache
from app.application.toolbox import Toolbox
from app.application.validation import validate_plan
from app.config import Settings
from app.domain.models import (
    GeoLocation,
    GeoPoint,
    RouteLeg,
    RouteStep,
    TicketOption,
    TripPlan,
)
from app.infrastructure.http import haversine_km
from app.infrastructure.observability import NoopTracer

AIRPORTS = {
    "KIX": GeoPoint(lat=34.43, lng=135.24),
    "ITM": GeoPoint(lat=34.79, lng=135.44),
    "HKG": GeoPoint(lat=22.31, lng=113.91),
}
KYOTO_STAY = GeoPoint(lat=35.01, lng=135.76)
OSAKA_STAY = GeoPoint(lat=34.70, lng=135.50)


class AirportGeocoder:
    """Finds an airport from "<IATA> airport"; anything else is not found."""

    name = "fake-geocoder"

    def __init__(self, known: dict[str, GeoPoint] | None = None):
        self.known = AIRPORTS if known is None else known
        self.asked: list[str] = []

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation | None:
        self.asked.append(name)
        code = name.removesuffix(" airport").strip().upper()
        point = self.known.get(code)
        if point is None or not name.endswith(" airport"):
            return None
        return GeoLocation(
            name=f"{code} Airport",
            country_code="JP",
            timezone="Asia/Tokyo",
            geo=point,
            source=self.name,
        )


class FakeRoutes:
    """A routes source that answers: transit by rail in 40 minutes, driving in 25."""

    name = "google-routes"
    available = True

    def __init__(
        self,
        transit_minutes: int = 40,
        drive_minutes: int = 25,
        vehicle: str = "HEAVY_RAIL",
    ):
        self.transit_minutes, self.drive_minutes, self.vehicle = (
            transit_minutes,
            drive_minutes,
            vehicle,
        )

    async def leg(self, origin, destination, *, mode="walk"):
        km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
        steps = (
            [
                RouteStep(
                    instruction="Rail",
                    mode="transit",
                    minutes=self.transit_minutes,
                    vehicle=self.vehicle,
                )
            ]
            if mode == "transit"
            else []
        )
        return RouteLeg(
            from_geo=origin,
            to_geo=destination,
            duration_minutes=self.transit_minutes
            if mode == "transit"
            else self.drive_minutes,
            distance_km=round(km, 2),
            mode=mode,
            source="google-routes",
            steps=steps,
        )


class Registry:
    def __init__(self, geocoder: AirportGeocoder | None = None, routes=None):
        self.geocoder = geocoder or AirportGeocoder()
        self.routes = [routes or FakeRoutes()]


def _flights(plan: TripPlan) -> TripPlan:
    """The two-city sample with real-looking flights: HKG-KIX in, KIX-HKG out, and geocodable stays."""
    plan.hotels[0].geo, plan.hotels[1].geo = KYOTO_STAY, OSAKA_STAY
    for ticket in plan.tickets:
        if ticket.leg_id == "leg_out":
            ticket.origin, ticket.destination = "HKG", "KIX"
        if ticket.leg_id == "leg_home":
            ticket.origin, ticket.destination = "KIX", "HKG"
            ticket.depart_at, ticket.arrive_at = (
                datetime(2026, 12, 7, 16, 0),
                datetime(2026, 12, 7, 19, 30),
            )
    plan.hotel_options = []
    return plan


def _sync(
    plan: TripPlan, registry: Registry | None = None, cache: SearchCache | None = None
) -> list[str]:
    toolbox = Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t")
    return asyncio.run(
        sync_transfers(
            plan,
            registry=registry or Registry(),
            toolbox=toolbox,
            cache=cache or SearchCache(None, plan.session_id),
        )
    )


def _transfer(plan: TripPlan, leg_id: str):
    leg = next((l for l in plan.legs if l.id == leg_id), None)
    return leg, plan.options_for(leg_id) if leg else []


def _select(plan: TripPlan, leg_id: str, option_id: str) -> None:
    for ticket in plan.options_for(leg_id):
        ticket.selected = ticket.id == option_id


# --- the model split ---------------------------------------------------------------------------------------------------


def test_route_legs_and_transfer_legs_split_the_kinds() -> None:
    plan = _flights(make_two_city_plan())
    _sync(plan)

    assert [l.kind for l in plan.route_legs()] == ["outbound", "between", "return"]
    assert sorted(l.kind for l in plan.transfer_legs()) == ["from_stay", "to_stay"]
    assert {l.id for l in plan.legs} == {l.id for l in plan.route_legs()} | {
        l.id for l in plan.transfer_legs()
    }


def test_the_check_that_every_pair_of_cities_has_a_leg_still_passes_with_transfers() -> (
    None
):
    plan = _flights(make_two_city_plan())
    _sync(plan)

    assert plan.transfer_legs()
    assert not [
        e
        for e in validate_plan(plan, Settings(_env_file=None))
        if "no transport leg" in e
    ]


# --- what a flight gives -------------------------------------------------------------------------------------------------


def test_a_flight_into_the_first_city_gives_a_to_stay_transfer_with_estimated_options() -> (
    None
):
    plan = _flights(make_two_city_plan())

    _sync(plan)

    leg, options = _transfer(plan, "to_stay_seg_a")
    assert leg is not None and (leg.kind, leg.segment_id, leg.airport) == (
        "to_stay",
        "seg_a",
        "KIX",
    )
    assert leg.from_place == "KIX airport" and leg.to_place == plan.hotels[0].name
    assert {o.mode for o in options} == {"train", "bus", "taxi"}
    assert all(o.estimated and o.price is None and o.leg_id == leg.id for o in options)
    by_mode = {o.mode: o for o in options}
    assert by_mode["bus"].name == "Airport bus or shuttle (estimate)"
    assert by_mode["bus"].duration_minutes == round(25 * 1.3)
    assert (
        by_mode["taxi"].name == "Taxi or ride (estimate)"
        and by_mode["taxi"].duration_minutes == 25
    )
    fastest = min(options, key=lambda o: o.duration_minutes)
    assert [o.id for o in options if o.selected] == [fastest.id] and options[0].selected
    assert [o.duration_minutes for o in options] == sorted(
        o.duration_minutes for o in options
    )


def test_a_transit_bus_makes_the_second_option_unnecessary() -> None:
    plan = _flights(make_two_city_plan())

    _sync(plan, Registry(routes=FakeRoutes(vehicle="BUS")))

    _, options = _transfer(plan, "to_stay_seg_a")
    assert sorted(o.mode for o in options) == ["bus", "taxi"]


def test_a_flight_out_of_the_last_city_gives_a_from_stay_transfer_to_the_airport() -> (
    None
):
    plan = _flights(make_two_city_plan())

    _sync(plan)

    leg, options = _transfer(plan, "from_stay_seg_b")
    assert leg is not None and (leg.kind, leg.segment_id, leg.airport) == (
        "from_stay",
        "seg_b",
        "KIX",
    )
    assert leg.from_place == plan.hotels[1].name and leg.to_place == "KIX airport"
    assert options and all(o.estimated for o in options)


def test_a_flight_arrives_45_minutes_before_the_transfer_and_the_flight_home_leaves_120_after_it() -> (
    None
):
    plan = _flights(make_two_city_plan())

    _sync(plan)

    _, into = _transfer(plan, "to_stay_seg_a")
    for option in into:
        assert option.depart_at == datetime(2026, 12, 3, 11, 45)  # lands 11:00
    _, out = _transfer(plan, "from_stay_seg_b")
    for option in out:
        assert option.arrive_at == datetime(
            2026, 12, 7, 14, 0
        )  # the flight leaves 16:00


def test_a_leg_whose_chosen_option_is_a_train_gives_no_transfers_on_either_side() -> (
    None
):
    plan = _flights(make_two_city_plan())
    train = TicketOption(
        id="t_out_train",
        mode="train",
        leg_id="leg_out",
        name="Airport rail link",
        estimated=True,
        selected=True,
        depart_at=datetime(2026, 12, 3, 9, 0),
        arrive_at=datetime(2026, 12, 3, 12, 0),
    )
    plan.tickets.append(train)
    _select(plan, "leg_out", "t_out_train")

    _sync(plan)

    assert _transfer(plan, "to_stay_seg_a")[0] is None
    # The flight home is still a flight.
    assert _transfer(plan, "from_stay_seg_b")[0] is not None


def test_switching_a_leg_from_train_to_flight_adds_its_two_transfers_and_back_removes_them() -> (
    None
):
    plan = _flights(make_two_city_plan())
    plan.tickets.append(
        TicketOption(
            id="t_ab_flight",
            mode="flight",
            leg_id="leg_ab",
            name="Short hop",
            origin="KIX",
            destination="ITM",
            depart_at=datetime(2026, 12, 5, 10, 0),
            arrive_at=datetime(2026, 12, 5, 11, 0),
        )
    )
    _sync(plan)
    assert (
        _transfer(plan, "from_stay_seg_a")[0] is None
        and _transfer(plan, "to_stay_seg_b")[0] is None
    )

    _select(plan, "leg_ab", "t_ab_flight")
    _sync(plan)

    out_of_a, into_b = (
        _transfer(plan, "from_stay_seg_a")[0],
        _transfer(plan, "to_stay_seg_b")[0],
    )
    assert out_of_a is not None and out_of_a.airport == "KIX"
    assert into_b is not None and into_b.airport == "ITM"

    _select(plan, "leg_ab", "t_ab_train")
    _sync(plan)

    assert (
        _transfer(plan, "from_stay_seg_a")[0] is None
        and _transfer(plan, "to_stay_seg_b")[0] is None
    )
    assert not [
        t for t in plan.tickets if t.leg_id in {"from_stay_seg_a", "to_stay_seg_b"}
    ]


def test_no_transfer_is_made_at_the_travellers_home_end() -> None:
    plan = _flights(make_two_city_plan())

    _sync(plan)

    assert sorted(l.id for l in plan.transfer_legs()) == [
        "from_stay_seg_b",
        "to_stay_seg_a",
    ]
    assert all("HKG" not in l.airport for l in plan.transfer_legs())


def test_a_one_city_trip_with_a_round_trip_flight_gets_both_transfers() -> None:
    plan = make_plan()
    plan.hotels[0].geo = KYOTO_STAY

    _sync(plan, Registry(AirportGeocoder()))

    segment = plan.segments[0].id
    assert [l.kind for l in plan.transfer_legs()] == ["to_stay", "from_stay"]
    assert {l.airport for l in plan.transfer_legs()} == {"KIX"}
    assert [l.id for l in plan.transfer_legs()] == [
        f"to_stay_{segment}",
        f"from_stay_{segment}",
    ]


# --- what is not found ---------------------------------------------------------------------------------------------------


def test_an_airport_the_geocoder_cannot_find_gives_no_transfer_and_no_error() -> None:
    plan = _flights(make_two_city_plan())

    _sync(plan, Registry(AirportGeocoder({})))

    assert plan.transfer_legs() == []
    assert not [t for t in plan.tickets if t.mode == "taxi"]


def test_a_stay_with_no_coordinates_gets_no_transfer() -> None:
    plan = _flights(make_two_city_plan())
    plan.hotels[0].geo = None

    _sync(plan)

    assert _transfer(plan, "to_stay_seg_a")[0] is None
    assert _transfer(plan, "from_stay_seg_b")[0] is not None


def test_a_code_that_is_not_an_airport_code_is_not_searched() -> None:
    plan = _flights(make_two_city_plan())
    for ticket in plan.tickets:
        ticket.origin, ticket.destination = "Hong Kong", "Kyoto"
    geocoder = AirportGeocoder()

    _sync(plan, Registry(geocoder))

    assert plan.transfer_legs() == [] and geocoder.asked == []


def test_an_airport_is_looked_up_once_for_the_session() -> None:
    plan = _flights(make_two_city_plan())
    geocoder = AirportGeocoder()
    cache = SearchCache(None, "s2")

    _sync(plan, Registry(geocoder), cache)
    first = len(geocoder.asked)
    _sync(plan, Registry(geocoder), cache)

    assert (
        first == 1 and len(geocoder.asked) == 1
    )  # KIX in and out: one lookup, then none


# --- rebuilding ----------------------------------------------------------------------------------------------------------


def test_an_unchanged_transfer_keeps_its_choice_and_its_lock() -> None:
    plan = _flights(make_two_city_plan())
    _sync(plan)
    chosen = next(o for o in plan.options_for("to_stay_seg_a") if o.mode == "bus")
    _select(plan, "to_stay_seg_a", chosen.id)
    next(l for l in plan.legs if l.id == "to_stay_seg_a").locked = True

    said = _sync(plan)

    leg, options = _transfer(plan, "to_stay_seg_a")
    assert said == [] and leg.locked is True
    assert [o.id for o in options if o.selected] == [chosen.id]


def test_replacing_the_stay_keeps_the_transfers_id_recomputes_its_options_and_releases_its_lock() -> (
    None
):
    plan = _flights(make_two_city_plan())
    _sync(plan)
    before = {o.id: o.duration_minutes for o in plan.options_for("to_stay_seg_a")}
    next(l for l in plan.legs if l.id == "to_stay_seg_a").locked = True
    plan.hotels[0] = plan.hotels[0].model_copy(
        update={"name": "Far Out Inn", "geo": GeoPoint(lat=35.20, lng=135.90)}
    )
    registry = Registry(routes=FakeRoutes(transit_minutes=70, drive_minutes=50))

    said = _sync(plan, registry)

    leg, options = _transfer(plan, "to_stay_seg_a")
    assert leg is not None and leg.id == "to_stay_seg_a" and leg.locked is False
    assert leg.to_place == "Far Out Inn"
    assert {o.id: o.duration_minutes for o in options} != before
    assert (
        "The airport transfer in Kyoto was worked out again for your new stay." in said
    )


def test_a_new_flight_into_another_airport_works_the_transfer_out_again_and_says_flight() -> (
    None
):
    plan = _flights(make_two_city_plan())
    _sync(plan)
    next(l for l in plan.legs if l.id == "to_stay_seg_a").locked = True
    for ticket in plan.options_for("leg_out"):
        ticket.destination = "ITM"

    said = _sync(plan)

    leg, _ = _transfer(plan, "to_stay_seg_a")
    assert leg.airport == "ITM" and leg.locked is False
    assert (
        "The airport transfer in Kyoto was worked out again for your new flight."
        in said
    )


# --- the day --------------------------------------------------------------------------------------------------------------


def test_the_transfer_is_a_transit_item_on_the_day_and_stops_it_runs_into_are_removed_with_a_note() -> (
    None
):
    from plan_samples import stop

    plan = _flights(make_two_city_plan())
    plan.days[0].items = [
        stop("item_in_the_way", "Gion walk", "12:00", "13:00"),
        stop("item_after", "Philosopher's Path", "17:00", "18:00"),
    ]

    _sync(plan)

    day = plan.days[0]
    transit = next(i for i in day.items if i.id == "transit_to_stay_seg_a")
    assert transit.type.value == "transit" and transit.start == "11:45"
    assert "Gion walk" not in [i.name for i in day.items]
    assert "Philosopher's Path" in [i.name for i in day.items]
    assert any(
        "Gion walk" in note and "2026-12-03" in note for note in plan.data_quality.notes
    )


def test_the_transfer_to_the_airport_comes_before_the_flight_home_and_drops_a_stop_that_runs_into_it() -> (
    None
):
    from plan_samples import stop

    plan = _flights(make_two_city_plan())
    last = plan.days[-1]
    last.items = [
        stop("item_late", "Dotonbori", "13:00", "14:00"),
        stop("item_early", "Osaka Castle", "09:00", "10:00"),
    ]

    _sync(plan)

    leaving = next(i for i in plan.days[-1].items if i.id == "transit_from_stay_seg_b")
    assert leaving.end <= "14:00"
    assert "Dotonbori" not in [i.name for i in plan.days[-1].items]
    assert "Osaka Castle" in [i.name for i in plan.days[-1].items]


def test_a_rebuilt_transfer_leaves_one_transit_item_not_two() -> None:
    plan = _flights(make_two_city_plan())

    _sync(plan)
    _sync(plan)

    ids = [
        i.id
        for day in plan.days
        for i in day.items
        if i.id.startswith("transit_to_stay")
    ]
    assert ids == ["transit_to_stay_seg_a"]


# --- the link ------------------------------------------------------------------------------------------------------------


def test_a_booking_link_is_a_directions_link_with_no_key_in_it() -> None:
    plan = _flights(make_two_city_plan())

    _sync(plan)

    _, options = _transfer(plan, "to_stay_seg_a")
    for option in options:
        url = urlparse(option.booking_url)
        query = parse_qs(url.query)
        assert (url.scheme, url.netloc, url.path) == (
            "https",
            "www.google.com",
            "/maps/dir/",
        )
        assert query["origin"] == ["KIX airport"] and query["destination"] == [
            plan.hotels[0].name
        ]
        assert "key" not in query and "api_key" not in query
        assert option.mode in {"train", "bus", "taxi"}
        assert query["travelmode"] == (
            ["driving"] if option.mode == "taxi" else ["transit"]
        )
    assert date(2026, 12, 3) == plan.days[0].date


# --- plans stored before transfers existed -------------------------------------------------------------------------------


def test_a_new_plan_gets_its_transfers_and_the_mark_and_an_old_plan_is_left_alone() -> None:
    from app.application.airport_transfers import sync_session_transfers

    def run(plan: TripPlan, before: TripPlan | None):
        return asyncio.run(
            sync_session_transfers(plan, registry=Registry(), store=None, session_id=plan.session_id, before=before)
        )

    fresh = _flights(make_two_city_plan())
    run(fresh, None)
    assert fresh.transfers_synced is True and fresh.transfer_legs()

    old = _flights(make_two_city_plan())
    assert old.transfers_synced is False
    edited = old.model_copy(deep=True)
    assert run(edited, old) == []
    assert edited.transfer_legs() == [] and edited.transfers_synced is False

    later = fresh.model_copy(deep=True)
    run(later, fresh)
    assert later.transfers_synced is True and later.transfer_legs()
