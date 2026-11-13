"""The traveller profile: pace, children, business and step-free, read from the message and applied by code."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from plan_samples import make_plan
from single_city_plan import START

from app.application.assessment import assess_plan
from app.application.llm_gateway import LLMGateway
from app.application.planner import _pace, _schedule_day
from app.application.profile_rules import (
    FAMILY_BOOST,
    family_suited,
    profile_order,
    stops_cap,
)
from app.application.prompts import PromptLibrary
from app.application.ranking import BUSINESS_STAY_WEIGHTS, rank_stays
from app.application.trip_rules import DayWindow
from app.application.understanding import understand
from app.config import Settings
from app.domain.models import (
    EvalCase,
    GeoPoint,
    PlaceCandidate,
    PlanItem,
    TravellerProfile,
    TripContext,
)
from app.eval_efficiency import build_rig, case_context
from app.eval_metering import RunLedger
from app.infrastructure.http import use_transport
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.places import GOOGLE_FIELD_MASK, GooglePlacesSource
from app.infrastructure.providers.routes import TransportModelRoutes, model_travel

# --- the model -------------------------------------------------------------------------------------------------------


def test_a_context_has_the_default_profile_and_reads_pace_from_it() -> None:
    context = TripContext(session_id="s")

    assert context.profile == TravellerProfile()
    assert (
        context.profile.pace,
        context.profile.with_children,
        context.profile.purpose,
        context.profile.step_free,
    ) == (
        "balanced",
        False,
        "leisure",
        False,
    )
    assert context.pace == "balanced"


@pytest.mark.parametrize(
    ("old", "pace"),
    [
        ("slow", "relaxed"),
        ("relaxed", "relaxed"),
        ("fast", "packed"),
        ("packed", "packed"),
        ("balanced", "balanced"),
        ("turbo", "balanced"),
        ("", "balanced"),
    ],
)
def test_an_old_stored_pace_string_is_mapped_into_the_profile(
    old: str, pace: str
) -> None:
    stored = TripContext.model_validate({"session_id": "s", "pace": old})
    built = TripContext(session_id="s", pace=old)

    assert stored.profile.pace == built.profile.pace == pace
    assert stored.pace == pace


def test_a_stored_profile_wins_over_an_old_pace_next_to_it() -> None:
    context = TripContext.model_validate(
        {
            "session_id": "s",
            "pace": "fast",
            "profile": {"pace": "relaxed", "step_free": True},
        }
    )

    assert context.profile == TravellerProfile(pace="relaxed", step_free=True)


def test_the_pace_can_still_be_set_on_a_context() -> None:
    context = TripContext(session_id="s")

    context.pace = "slow"

    assert context.profile.pace == "relaxed" and context.pace == "relaxed"


def test_a_default_profile_dumps_as_the_form_always_did_and_another_one_round_trips() -> (
    None
):
    plain = TripContext(session_id="s").model_dump(mode="json")
    assert plain["pace"] == "balanced" and "profile" not in plain

    context = TripContext(
        session_id="s", profile=TravellerProfile(with_children=True, purpose="business")
    )
    dumped = context.model_dump(mode="json")

    assert dumped["profile"]["with_children"] is True and dumped["pace"] == "balanced"
    assert TripContext.model_validate(dumped).profile == context.profile


# --- the message -----------------------------------------------------------------------------------------------------


def _read(message: str, settings: Settings) -> TripContext:
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    return asyncio.run(understand(message, TripContext(session_id="s"), gateway))


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Plan a 3-day Kyoto trip from Hong Kong with two kids.",
            {"with_children": True},
        ),
        (
            "Kyoto for 4 days, we are travelling with a toddler.",
            {"with_children": True},
        ),
        ("Plan Kyoto with my 5 year old daughter in April.", {"with_children": True}),
        (
            "A business trip to Tokyo for 3 days from Hong Kong.",
            {"purpose": "business"},
        ),
        ("A work trip to Tokyo, 2 days, from Hong Kong.", {"purpose": "business"}),
        ("Kyoto trip, I use a wheelchair.", {"step_free": True}),
        ("Kyoto trip, we need step-free access.", {"step_free": True}),
        ("Kyoto for 3 days, wheelchair accessible places only.", {"step_free": True}),
        ("Plan Kyoto for 3 days and take it slow.", {"pace": "relaxed"}),
        ("Plan Kyoto for 3 days with a relaxed pace.", {"pace": "relaxed"}),
        (
            "Plan Kyoto for 3 days, I want to see as much as possible.",
            {"pace": "packed"},
        ),
        ("Plan Kyoto for 3 days with a packed schedule.", {"pace": "packed"}),
    ],
)
def test_the_profile_is_read_from_what_the_traveller_says(
    message: str, expected: dict, settings: Settings
) -> None:
    profile = _read(message, settings).profile
    base = TravellerProfile()

    for key, value in expected.items():
        assert getattr(profile, key) == value, message
    for key in {"pace", "with_children", "purpose", "step_free"} - set(expected):
        assert getattr(profile, key) == getattr(base, key), f"{message}: {key} changed"


def test_a_plain_request_leaves_the_profile_alone(settings: Settings) -> None:
    context = _read("Plan a 3-day trip to Kyoto from Hong Kong in April.", settings)

    assert context.profile == TravellerProfile()


def test_a_slow_pace_is_still_a_preference_and_now_also_the_profile(
    settings: Settings,
) -> None:
    context = _read(
        "Plan a 4-day Kyoto trip from Hong Kong for 2 in April. I want a slow pace.",
        settings,
    )

    assert "slow pace" in context.preferences and context.profile.pace == "relaxed"


def test_a_later_message_does_not_undo_what_an_earlier_one_said(
    settings: Settings,
) -> None:
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    first = asyncio.run(
        understand(
            "Kyoto trip with two kids, step-free please.",
            TripContext(session_id="s"),
            gateway,
        )
    )

    second = asyncio.run(understand("Make it three days.", first, gateway))

    assert second.profile.with_children and second.profile.step_free


# --- stops a day --------------------------------------------------------------------------------------------------------


def test_the_stops_a_day_follow_the_pace_and_business_keeps_two(
    settings: Settings,
) -> None:
    assert settings.max_attractions_per_day == 4
    assert stops_cap(settings, "relaxed") == 2
    assert stops_cap(settings, "balanced") == 4
    assert stops_cap(settings, "packed") == 5
    # The older internal paces keep what they always gave.
    assert (stops_cap(settings, "slow"), stops_cap(settings, "fast")) == (3, 4)
    # Business: two a day, unless the traveller asked for a packed pace.
    assert stops_cap(settings, "balanced", business=True) == 2
    assert stops_cap(settings, "relaxed", business=True) == 2
    assert stops_cap(settings, "packed", business=True) == 5


def test_the_planners_pace_comes_from_the_profile_then_from_the_old_preference_tags() -> (
    None
):
    def context(pace: str = "balanced", preferences: tuple[str, ...] = ()):
        return SimpleNamespace(
            profile=TravellerProfile(pace=pace), preferences=list(preferences)
        )

    assert _pace(context("relaxed")) == "relaxed"
    assert _pace(context("packed")) == "packed"
    assert _pace(context()) == "balanced"
    assert _pace(context(preferences=("slow pace",))) == "slow"
    assert _pace(context(preferences=("fast pace",))) == "fast"
    assert _pace(SimpleNamespace(preferences=["slow pace"])) == "slow"


def _row(name: str, lat: float, lng: float, **extra) -> dict:
    row = {
        "id": name.lower().replace(" ", "_"),
        "name": name,
        "category": "Attraction",
        "description": "",
        "geo": {"lat": lat, "lng": lng},
        "address": "Kyoto",
        "rating": 4.5,
        "source_refs": [
            {
                "title": name,
                "url": "https://example.com/" + name.lower().replace(" ", "-"),
                "source": "t",
            }
        ],
        "tags": [],
    }
    row.update(extra)
    return row


def _two_close_stops(pace: str) -> list[PlanItem]:
    rows = [_row("Alpha Hall", 35.000, 135.000), _row("Beta Hall", 35.002, 135.002)]
    items = _schedule_day(date(2027, 4, 2), rows, None, None, pace=pace, must_see=[])
    return [item for item in items if item.type.value == "attraction"]


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def test_the_gap_between_stops_is_longer_for_a_relaxed_trip_and_never_short_of_15_for_a_packed_one() -> (
    None
):
    relaxed = _two_close_stops("relaxed")
    packed = _two_close_stops("packed")
    balanced = _two_close_stops("balanced")

    assert _minutes(relaxed[1].start) - _minutes(relaxed[0].end) >= 45
    assert _minutes(packed[1].start) - _minutes(packed[0].end) >= 15
    # The balanced day is scheduled exactly as before (transfer plus a small buffer).
    assert _minutes(balanced[1].start) - _minutes(balanced[0].end) < 30


def test_a_trip_with_children_ends_no_stop_after_19() -> None:
    rows = [
        _row("Alpha Hall", 35.000, 135.000),
        _row("Beta Hall", 35.002, 135.002),
        _row("Gamma Hall", 35.004, 135.004),
    ]
    window = DayWindow(start=16 * 60)

    free = _schedule_day(
        date(2027, 4, 2), rows, None, None, pace="balanced", must_see=[], window=window
    )
    kept = _schedule_day(
        date(2027, 4, 2),
        rows,
        None,
        None,
        pace="balanced",
        must_see=[],
        window=window,
        stops_end_by=19 * 60,
    )

    assert max(_minutes(item.end) for item in free) > 19 * 60, (
        "the control day does run past 19:00"
    )
    assert kept and max(_minutes(item.end) for item in kept) <= 19 * 60


# --- which places are chosen --------------------------------------------------------------------------------------------


def test_family_suited_places_are_the_source_types_zoo_aquarium_park_playground_amusement_park_and_child_museums() -> (
    None
):
    assert family_suited({"name": "City Zoo", "tags": ["zoo", "tourist_attraction"]})
    assert family_suited({"name": "Bay Aquarium", "tags": ["aquarium"]})
    assert family_suited({"name": "Maruyama", "tags": ["park"]})
    assert family_suited({"name": "Fun Land", "tags": ["amusement_park"]})
    assert family_suited({"name": "Corner Play", "tags": ["playground"]})
    assert family_suited({"name": "Science Museum", "tags": ["museum"]})
    assert family_suited(
        {
            "name": "City Museum",
            "tags": ["museum"],
            "description": "Hands-on rooms for children.",
        }
    )
    assert not family_suited({"name": "City Museum", "tags": ["museum"]})
    assert not family_suited({"name": "Old Temple", "tags": ["place_of_worship"]})
    assert not family_suited({"name": "Playhouse Bar", "tags": ["bar"]})


def test_children_boost_family_places_by_0_15_before_selection_and_others_keep_their_order() -> (
    None
):
    museum = _row("Grand Museum", 35.0, 135.0, rating=4.5, tags=["museum"])
    zoo = _row("City Zoo", 35.1, 135.1, rating=4.4, tags=["zoo"])
    temple = _row("Old Temple", 35.2, 135.2, rating=4.3, tags=["place_of_worship"])
    rows = [museum, temple, zoo]

    assert FAMILY_BOOST == 0.15
    assert [r["name"] for r in profile_order(rows, TravellerProfile())] == [
        "Grand Museum",
        "Old Temple",
        "City Zoo",
    ]
    assert [
        r["name"] for r in profile_order(rows, TravellerProfile(with_children=True))
    ] == [
        "City Zoo",
        "Grand Museum",
        "Old Temple",
    ]


def test_step_free_prefers_places_the_source_marks_accessible_and_puts_the_unsure_before_the_refused() -> (
    None
):
    yes = _row("Yes Hall", 35.0, 135.0, step_free=True)
    unknown = _row("Unknown Hall", 35.1, 135.1)
    no = _row("No Hall", 35.2, 135.2, step_free=False)

    ordered = profile_order([no, unknown, yes], TravellerProfile(step_free=True))

    assert [r["name"] for r in ordered] == ["Yes Hall", "Unknown Hall", "No Hall"]
    assert [
        r["name"] for r in profile_order([no, unknown, yes], TravellerProfile())
    ] == ["No Hall", "Unknown Hall", "Yes Hall"]


# --- step-free: the source and the transfers -------------------------------------------------------------------------


def test_the_google_search_asks_for_accessibility_and_reads_the_entrance() -> None:
    assert "places.accessibilityOptions" in GOOGLE_FIELD_MASK
    settings = Settings(_env_file=None, google_places_api_key="FAKE-KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        def place(pid: str, name: str, access: dict | None) -> dict:
            row = {
                "id": pid,
                "displayName": {"text": name, "languageCode": "en"},
                "location": {"latitude": 34.99, "longitude": 135.78},
            }
            if access is not None:
                row["accessibilityOptions"] = access
            return row

        return httpx.Response(
            200,
            json={
                "places": [
                    place("a", "Ramp Hall", {"wheelchairAccessibleEntrance": True}),
                    place("b", "Stairs Hall", {"wheelchairAccessibleEntrance": False}),
                    place("c", "Silent Hall", {"wheelchairAccessibleParking": True}),
                    place("d", "Unknown Hall", None),
                ]
            },
        )

    with use_transport(httpx.MockTransport(handler)):
        found = asyncio.run(
            GooglePlacesSource(settings).search(
                destination="Kyoto",
                geo=GeoPoint(lat=35.0, lng=135.77),
                category="attraction",
                limit=5,
            )
        )

    assert [p.step_free for p in found] == [True, False, None, None]
    assert PlaceCandidate(name="x").step_free is None


def test_a_walk_over_800_m_becomes_transit_for_a_step_free_trip() -> None:
    assert model_travel(0.7, step_free=True)[0] == "walk"
    assert model_travel(0.9, step_free=True)[0] == "metro"
    assert model_travel(1.8, step_free=True)[0] == "metro"
    assert model_travel(40.0, step_free=True)[0] == "drive"
    # Nobody else's transfer changes.
    assert model_travel(0.9)[0] == "walk" and model_travel(1.8)[0] == "walk"


def test_the_routes_tool_gives_step_free_legs_when_asked() -> None:
    points = [
        GeoPoint(lat=35.0, lng=135.0),
        GeoPoint(lat=35.0, lng=135.011),
    ]  # about 1 km apart
    tool = TransportModelRoutes()

    plain = asyncio.run(tool.segments(points=points, ids=["a", "b"]))
    kept = asyncio.run(tool.segments(points=points, ids=["a", "b"], step_free=True))

    assert {leg.mode for leg in plain} == {"walk"}
    assert {leg.mode for leg in kept} == {"metro"}


# --- a planned trip -----------------------------------------------------------------------------------------------------


class _Places:
    """The sandbox places with a hook to change each candidate."""

    name = "profile-places"

    def __init__(self, hook=None):
        self.hook = hook

    async def search(self, **kwargs):
        from app.evaluation import SandboxPlaces

        found = await SandboxPlaces(kwargs["destination"]).search(**kwargs)
        if self.hook is not None:
            found = self.hook(found, kwargs["category"])
        return found


def _plan_with(
    settings: Settings,
    profile: TravellerProfile,
    *,
    days: int = 3,
    preferences=(),
    hook=None,
):
    settings = settings.model_copy(
        update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8}
    )

    async def main():
        ledger = RunLedger()
        try:
            rig = build_rig(settings, "Kyoto", ledger)
            if hook is not None:
                rig.plan_path.registry.places = [_Places(hook)]
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-profile")
            case = EvalCase(
                id="p",
                dataset="d",
                input="",
                destination="Kyoto",
                days=days,
                party_size=2,
                budget=90000,
                currency="HKD",
            )
            context = case_context(case, session.id)
            context.start_date, context.end_date = (
                START,
                START + timedelta(days=days - 1),
            )
            context.profile = profile
            context.preferences = list(preferences)
            ledger.store.save_context(context)
            _ = [
                e
                async for e in rig.turns.handle_turn(
                    session_id=session.id, message="Plan this trip"
                )
            ]
            return ledger.store.get_plan(session.id), context
        finally:
            ledger.close()

    return asyncio.run(main())


def _open_late(found, category):
    """Every place open until 21:00 on every weekday, so an evening visit is possible."""
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for place in found:
        place.opening_hours = [f"{day}: 9:00 AM – 9:00 PM" for day in days]
    return found


def _sights(plan) -> list[PlanItem]:
    return [item for _, item in plan.iter_items() if item.type.value == "attraction"]


def test_the_stops_chosen_a_day_follow_the_pace_and_business(settings: Settings, offline_gateway: LLMGateway) -> None:
    from app.application.planner import Planner
    from app.infrastructure.providers.fx import FxService

    planner = Planner(offline_gateway, settings, FxService())
    places = [_row(f"Hall {n}", 35.0 + n * 0.02, 135.0 + n * 0.02) for n in range(18)]

    def per_day(**kwargs) -> list[int]:
        found = planner._assign_places(places, 3, None, **kwargs)
        return [len(rows) for rows in found.values()]

    assert per_day(pace="balanced") == [4, 4, 4]
    assert per_day(pace="relaxed") == [2, 2, 2]
    assert per_day(pace="packed") == [5, 5, 5]
    assert per_day(pace="balanced", business=True) == [2, 2, 2]
    assert per_day(pace="packed", business=True) == [5, 5, 5]


def test_a_business_trip_has_stops_only_after_17_and_two_at_most_a_day(settings: Settings) -> None:
    plan, _ = _plan_with(settings, TravellerProfile(purpose="business"), days=3, hook=_open_late)

    sights = _sights(plan)
    assert sights, "the free middle day has stops"
    assert all(_minutes(item.start) >= 17 * 60 for item in sights)
    assert all(sum(1 for i in day.items if i.type.value == "attraction") <= 2 for day in plan.days)


def test_a_business_traveller_who_asks_for_daytime_stops_keeps_the_day(settings: Settings) -> None:
    plan, _ = _plan_with(
        settings, TravellerProfile(purpose="business"), days=3, preferences=["daytime stops"], hook=_open_late
    )

    sights = _sights(plan)
    assert any(_minutes(item.start) < 17 * 60 for item in sights)
    assert all(sum(1 for i in day.items if i.type.value == "attraction") <= 2 for day in plan.days)


def test_a_trip_with_children_ends_every_stop_by_19_and_takes_family_places_first(settings: Settings) -> None:
    def hook(found, category):
        for place in found:
            place.tags = ["zoo"] if place.name.startswith("Gion") else ["place_of_worship"]
            place.rating = 4.0
        return found

    plan, _ = _plan_with(settings, TravellerProfile(with_children=True, pace="relaxed"), days=3, hook=hook)

    sights = _sights(plan)
    assert any(item.name.startswith("Gion") for item in sights)
    assert all(_minutes(item.end) <= 19 * 60 for item in sights)


def test_the_profile_is_kept_on_the_plan_only_when_it_is_not_the_default(
    settings: Settings,
) -> None:
    plain, _ = _plan_with(settings, TravellerProfile())
    business, _ = _plan_with(settings, TravellerProfile(purpose="business"))

    assert plain.profile is None
    assert business.profile == TravellerProfile(purpose="business")


# --- step-free on a planned trip -----------------------------------------------------------------------------------------


def _marked(found, category):
    for index, place in enumerate(found):
        place.step_free = [True, None, False, None][index % 4]
    return found


def test_a_step_free_trip_prefers_accessible_places_and_lists_what_is_not_confirmed(settings: Settings) -> None:
    plan, context = _plan_with(settings, TravellerProfile(step_free=True, pace="relaxed"), days=3, hook=_marked)

    stops = [item for _, item in plan.iter_items() if item.type.value in {"attraction", "meal"}]
    assert any(item.step_free is True for item in stops)
    listed = [line for line in assess_plan(plan, context).unverified if line.startswith("step-free access not confirmed: ")]
    assert listed, "a place with no step-free information is listed"
    unconfirmed = {item.name for item in stops if item.step_free is None}
    confirmed = {item.name for item in stops if item.step_free is True}
    for line in listed:
        name = line.removeprefix("step-free access not confirmed: ")
        assert name in unconfirmed and name not in confirmed


def test_without_the_step_free_wish_nothing_is_listed_about_access(
    settings: Settings,
) -> None:
    plan, context = _plan_with(settings, TravellerProfile(), hook=_marked)

    assert not [
        line for line in assess_plan(plan, context).unverified if "step-free" in line
    ]


def test_a_stop_that_the_source_says_has_no_step_free_entrance_is_listed_as_that(
    settings: Settings,
) -> None:
    plan = make_plan()
    stop = plan.days[0].items[0]
    stop.step_free = False
    context = TripContext(session_id="s", profile=TravellerProfile(step_free=True))

    lines = assess_plan(plan, context).unverified

    assert f"no step-free entrance at {stop.name} (the place source says so)" in lines


# --- children: long transfers --------------------------------------------------------------------------------------------


def test_a_single_transfer_over_45_minutes_is_flagged_for_a_trip_with_children() -> (
    None
):
    from app.domain.models import TransferLeg

    plan = make_plan()
    stop = plan.days[0].items[0]
    stop.transfer = TransferLeg(
        from_item_id="x",
        mode="drive",
        minutes=52,
        distance_km=40.0,
        source="transport-model",
        tool="routes.segments",
    )
    children = TripContext(session_id="s", profile=TravellerProfile(with_children=True))

    flagged = [
        line for line in assess_plan(plan, children).unverified if "52 minutes" in line
    ]
    none = [
        line
        for line in assess_plan(plan, TripContext(session_id="s")).unverified
        if "52 minutes" in line
    ]

    assert len(flagged) == 1 and stop.name in flagged[0]
    assert none == []


# --- stays for business -----------------------------------------------------------------------------------------------------


def test_business_ranks_stays_by_distance_to_the_centre_at_0_35_and_price_at_0_20() -> (
    None
):
    assert BUSINESS_STAY_WEIGHTS == {
        "price": 0.20,
        "rating": 0.25,
        "distance": 0.35,
        "match": 0.20,
    }
    base = make_plan().hotels[0]

    def stay(sid: str, price: float, centre_km: float, stops_km: float):
        return base.model_copy(
            update={
                "id": sid,
                "name": sid,
                "price_per_night": base.price_per_night.model_copy(
                    update={"amount": price}
                ),
                "rating": 4.0,
                "distance_to_centre_km": centre_km,
                "nearest_stop_km": stops_km,
                "match": None,
            }
        )

    central_dear = stay("central_dear", 2000, 0.3, 9.0)
    far_cheap = stay("far_cheap", 800, 8.0, 0.3)

    leisure = [central_dear.model_copy(), far_cheap.model_copy()]
    rank_stays(leisure, per_night_budget=None)
    business = [central_dear.model_copy(), far_cheap.model_copy()]
    rank_stays(
        business,
        per_night_budget=None,
        weights=BUSINESS_STAY_WEIGHTS,
        distance_to="centre",
    )

    assert [s.id for s in sorted(leisure, key=lambda s: s.rank)] == [
        "far_cheap",
        "central_dear",
    ]
    assert [s.id for s in sorted(business, key=lambda s: s.rank)] == [
        "central_dear",
        "far_cheap",
    ]


def test_a_plan_made_for_business_ranks_its_stays_that_way() -> None:
    plan = make_plan()
    base = plan.hotels[0]
    near = base.model_copy(
        update={
            "id": "near",
            "name": "Near",
            "distance_to_centre_km": 0.2,
            "nearest_stop_km": 9.0,
            "match": None,
        }
    )
    far = base.model_copy(
        update={
            "id": "far",
            "name": "Far",
            "distance_to_centre_km": 7.0,
            "nearest_stop_km": 0.2,
            "match": None,
        }
    )
    plan.hotel_options = [far, near]

    plan.profile = TravellerProfile(purpose="business")
    plan.refresh_derived()

    assert next(o for o in plan.hotel_options if o.id == "near").rank == 1


# --- the form and the model --------------------------------------------------------------------------------------------------


def test_the_form_sets_the_profile_through_the_state_patch(settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from app.interface.api import create_app

    with TestClient(create_app(settings)) as client:
        session_id = client.post("/api/journeys", json={"client_token": "t"}).json()["session"]["id"]
        headers = {"X-Client-Token": "t"}

        saved = client.patch(
            f"/api/journeys/{session_id}/state",
            json={"pace": "slow", "with_children": True, "purpose": "business", "step_free": True},
            headers=headers,
        )
        bad = client.patch(f"/api/journeys/{session_id}/state", json={"purpose": "holiday"}, headers=headers)
        later = client.patch(f"/api/journeys/{session_id}/state", json={"destination": "Kyoto"}, headers=headers)

    context = saved.json()["context"]
    assert saved.status_code == 200
    assert context["pace"] == "relaxed"
    assert context["profile"] == {"pace": "relaxed", "with_children": True, "purpose": "business", "step_free": True}
    assert bad.status_code == 422
    assert later.json()["context"]["profile"]["with_children"] is True


def test_what_the_model_reads_of_the_profile_is_taken_only_when_stated_and_valid() -> None:
    from app.application.understanding import _apply_model_profile

    context = TripContext(session_id="s", profile=TravellerProfile(step_free=True))

    _apply_model_profile(context, {"pace": "relaxed", "with_children": True, "purpose": "business", "step_free": False})
    assert context.profile == TravellerProfile(pace="relaxed", with_children=True, purpose="business", step_free=True)

    # An invalid word, a non-default-looking default, or something that is not an object changes nothing.
    _apply_model_profile(context, {"pace": "turbo", "purpose": "holiday", "with_children": "yes"})
    _apply_model_profile(context, "relaxed")
    assert context.profile == TravellerProfile(pace="relaxed", with_children=True, purpose="business", step_free=True)
