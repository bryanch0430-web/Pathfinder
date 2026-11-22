"""Robust reads of poor or thin traveller messages.

One home for the fallbacks that catch what a vague or mistyped message means:
unknown destination names, cuisine typos, "near day 2" search anchoring, shared
material adds, repeated-question loops, and the wish cue that asks the writer to
read a food wish on an otherwise complete trip.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

from app.application.country_check import whole_place_question
from app.application.escalation import is_stuck
from app.application.language import cuisine_slot
from app.application.modification import ChangeDraft, EditQuestion, _add_attractions, _day_anchor, _is_a_kind
from app.application.orchestrator import _answers_pending_question
from app.application.trip_rules import food_wishes, place_wishes
from app.application.understanding import _apply_rules, understand
from app.domain.models import (
    DateRange,
    DayPlan,
    Destination,
    GeoLocation,
    GeoPoint,
    HotelStay,
    ItemType,
    PlanItem,
    SourceRef,
    TripContext,
    TripPlan,
)

TODAY = date(2026, 11, 21)


def _rules(text: str) -> TripContext:
    context = TripContext(session_id="t")
    _apply_rules(text, context, TODAY)
    return context


# --- unknown destination names -----------------------------------------------


def test_a_cued_unknown_place_is_the_destination() -> None:
    assert _rules("I want to go Phu Quoc for 5 days").destination == "Phu Quoc"
    assert _rules("4 days in Jeju with my family").destination == "Jeju"
    assert _rules("take me to Reykjavik").destination == "Reykjavik"


def test_an_uncued_unknown_place_is_the_destination() -> None:
    assert _rules("Phu Quoc trip for 5 days").destination == "Phu Quoc"
    assert _rules("Porto itinerary please").destination == "Porto"
    assert _rules("plan Sapa for me").destination == "Sapa"
    assert _rules("thinking about Jeju").destination == "Jeju"
    assert _rules("Phu Quoc, 5 days, 2 people").destination == "Phu Quoc"


def test_words_that_are_no_place_stay_empty() -> None:
    for text in (
        "plan anyway",
        "plan a road trip for 5 days",
        "plan my honeymoon trip",
        "what about breakfast?",
        "how about money exchange",
        "thinking about dinner",
        "tell me about the hotel",
        "please call the hotel to confirm the late check-in",
        "I want to go somewhere warm",
        "flights from Tokyo, 5 days",
    ):
        assert _rules(text).destination == "", text


def test_a_place_after_the_verb_is_not_an_activity() -> None:
    assert _rules("I want to go to eat sushi in tokyo").destination == "Tokyo"


# --- cuisine typos --------------------------------------------------------------


def test_a_cuisine_with_one_wrong_letter_is_still_that_cuisine() -> None:
    assert cuisine_slot("add a sushii restaurant") == "sushi"
    assert cuisine_slot("i want raman for dinner") == "ramen"
    assert cuisine_slot("any koren bbq?") == "korean"
    assert cuisine_slot("japnese food") == "japanese"


def test_common_words_are_no_cuisine() -> None:
    assert cuisine_slot("what about breakfast") is None
    assert cuisine_slot("indeed let's go") is None
    assert cuisine_slot("in Traditional Chinese") is None


# --- "near day N" anchors the search ----------------------------------------------


def _item(name: str, start: str, end: str, lat: float | None = None, lng: float | None = None) -> PlanItem:
    return PlanItem(
        id=name.lower().replace(" ", "_"),
        type=ItemType.ATTRACTION,
        name=name,
        start=start,
        end=end,
        geo=GeoPoint(lat=lat, lng=lng) if lat is not None else None,
        source_refs=[SourceRef(title=name, source="check")],
    )


def _plan() -> TripPlan:
    start = date(2026, 11, 1)
    days = [
        DayPlan(date=start, items=[_item("D1 stop", "09:00", "10:00", 35.0, 135.0)]),
        DayPlan(
            date=date(2026, 11, 2),
            items=[
                _item("Mother Farm", "09:00", "11:00", 35.2, 140.0),
                _item("Fun Farm", "13:00", "15:00", 35.4, 140.2),
            ],
        ),
        DayPlan(date=date(2026, 11, 3), items=[]),
    ]
    return TripPlan(
        session_id="t",
        destination=Destination(name="Chiba"),
        dates=DateRange(start=start, end=date(2026, 11, 3)),
        days=days,
        hotels=[HotelStay(id="h1", name="Hotel", check_in=start, check_out=date(2026, 11, 4))],
    )


def test_near_a_day_anchors_the_search_on_that_days_stops() -> None:
    anchor = _day_anchor(_plan(), ChangeDraft(target_day=2), "suggest sushi near day 2 attractions")
    assert anchor is not None and abs(anchor.lat - 35.3) < 0.01 and abs(anchor.lng - 140.1) < 0.01


def test_no_near_word_or_no_day_means_no_anchor() -> None:
    plan = _plan()
    assert _day_anchor(plan, ChangeDraft(target_day=2), "suggest sushi") is None
    assert _day_anchor(plan, ChangeDraft(), "sushi near the temple") is None


# --- shared material adds ------------------------------------------------------------


def _rows() -> list[dict]:
    return [
        {"id": "p1", "name": "Sushi Dai", "geo": {"lat": 35.3, "lng": 140.1},
         "source_refs": [{"title": "g", "source": "check"}], "tags": ["requested"]},
        {"id": "p2", "name": "Ramen House", "geo": {"lat": 35.3, "lng": 140.15},
         "source_refs": [{"title": "g", "source": "check"}], "tags": ["requested"]},
        {"id": "p3", "name": "City Museum", "geo": {"lat": 35.1, "lng": 140.0},
         "source_refs": [{"title": "g", "source": "check"}], "tags": []},
    ]


def test_add_these_adds_every_shared_place_the_search_located() -> None:
    assert _add_attractions(_plan(), _rows(), "add these to day 3", day=3) == ["Sushi Dai", "Ramen House"]


def test_an_add_without_a_shared_reference_still_adds_one() -> None:
    assert len(_add_attractions(_plan(), _rows(), "add a stop to day 3", day=3)) == 1


# --- repeated questions are stuck --------------------------------------------------


@dataclass
class _Msg:
    role: str
    content: str
    meta: dict | None = None


def _chat(*turns: str) -> list[_Msg]:
    return [_Msg(("user", "assistant")[index % 2], text) for index, text in enumerate(turns)]


def test_the_same_question_asked_again_is_a_stuck_loop() -> None:
    loop = _chat(
        "please suggest any sushi restaurant near day2 attractions",
        "Which sushi restaurant would you like to add?",
        "any sushi restaurant near day 2 is fine",
        "Which sushi restaurant would you like to add to the plan?",
        "just pick a sushi restaurant near day 2",
        "Which sushi restaurant would you like to add?",
    )
    assert is_stuck(loop, 3)


def test_a_url_after_the_dates_form_is_not_stuck() -> None:
    filled = _chat(
        "can you use this url to plan trip: https://hk.trip.com/moments/detail/tokyo-294-149523579",
        "Where are you travelling from, what are your travel dates, and what is your budget?",
        "Here are the missing details — dates: 2026-11-21 to 2026-11-22; budget: 111; party size: 2.",
        "Where are you travelling from?",
        "Here are the missing details — origin: Hong Kong.",
        "Raise the budget, shorten the trip, or plan anyway with an over-budget hotel?",
        "Here are the missing details — budget: Keep HKD 111 and plan anyway.",
        "Should I relax it, or change the plan around it?",
    )
    assert not is_stuck(filled, 3)


def test_different_questions_are_a_normal_clarify_chat() -> None:
    normal = _chat(
        "plan a trip",
        "Where would you like to go?",
        "Tokyo",
        "When do you travel?",
        "in June",
        "How many people are going?",
    )
    assert not is_stuck(normal, 3)


# --- the wish cue asks the writer on a complete new trip --------------------------


class _FakeGateway:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def json(self, lane, *, user, purpose=None):
        self.calls += 1
        return {"preferences": ["must-eat: sushi"]}


def _understood(text: str, *, has_plan: bool = False) -> tuple[_FakeGateway, TripContext]:
    gateway = _FakeGateway()
    out = asyncio.run(understand(text, TripContext(session_id="t"), gateway, today=TODAY, has_plan=has_plan))
    return gateway, out


def test_a_food_wish_on_a_complete_new_trip_still_reaches_the_writer() -> None:
    gateway, out = _understood("5 day tokyo trip from hong kong in june 2027, 2 people, budget HKD 20000, for the sushi")
    assert gateway.calls == 1 and "must-eat: sushi" in out.preferences


def test_a_complete_new_trip_without_a_wish_word_skips_the_writer() -> None:
    gateway, _ = _understood("5 day tokyo trip from hong kong in june 2027, 2 people, budget HKD 20000")
    assert gateway.calls == 0


def test_an_existing_plan_keeps_the_writer_skip() -> None:
    gateway, _ = _understood(
        "5 day tokyo trip from hong kong in june 2027, 2 people, budget HKD 20000, for the sushi",
        has_plan=True,
    )
    assert gateway.calls == 0


def test_a_food_wish_is_never_searched_as_a_place() -> None:
    context = TripContext(session_id="t", preferences=["must-see: Gundam", "must-eat: sushi"])
    assert place_wishes(context) == ["Gundam"]
    assert food_wishes(context) == ["sushi"]


# --- a known city is never "a whole country/region" ------------------------------


class _Registry:
    def __init__(self, place: GeoLocation | None = None) -> None:
        self.place = place
        self.geocoder = self

    async def geocode(self, name: str) -> GeoLocation | None:
        assert self.place is not None, "a known city never reaches the geocoder"
        return self.place


def test_a_known_city_is_never_asked_as_a_whole_region() -> None:
    # Google geocodes "Tokyo" as the Metropolis, a first-level region; it is still a city to plan for.
    context = TripContext(session_id="t", destination="Tokyo", days=5)
    assert asyncio.run(whole_place_question(_Registry(), context)) is None


def test_an_unknown_region_still_asks_which_cities() -> None:
    place = GeoLocation(name="Hokkaido", geo=GeoPoint(lat=43.0, lng=142.0), kind="region")
    context = TripContext(session_id="t", destination="Hokkaido", days=6)
    asked = asyncio.run(whole_place_question(_Registry(place), context))
    assert asked is not None and asked.slots == ["destination"]


# --- an option answer completes the edit that asked --------------------------------

_PENDING = EditQuestion(
    "Would you like a sushi restaurant near Sunworld Hon Thom Phu Quoc, Sunset Town, or Kiss Bridge?",
    fact="which day 3 stop to search near",
    request="suggest any sushi restaurant near day 3 attractions",
)


def test_picking_an_option_answers_the_pending_edit_question() -> None:
    assert _answers_pending_question(_PENDING, "Kiss Bridge", None)
    assert _answers_pending_question(_PENDING, "sunset town", None)


def test_naming_a_plan_stop_answers_the_pending_edit_question() -> None:
    assert _answers_pending_question(_PENDING, "Mother Farm", _plan())


def test_a_new_request_does_not_answer_the_pending_edit_question() -> None:
    assert not _answers_pending_question(_PENDING, "add a cafe on day 2", _plan())
    assert not _answers_pending_question(_PENDING, "ok", _plan())
    assert not _answers_pending_question(_PENDING, "x" * 80, _plan())


def test_a_named_stop_anchors_the_search_on_that_stop() -> None:
    anchor = _day_anchor(
        _plan(), ChangeDraft(target_day=2), "suggest sushi near day 2 attractions (Mother Farm)"
    )
    assert anchor is not None and anchor.lat == 35.2 and anchor.lng == 140.0


# --- a mistyped meal kind is still a kind, and aims the meal search -------------------


def test_dim_sum_is_a_cuisine() -> None:
    assert cuisine_slot("ok other suggestion for dim sum restaruant?") == "dim sum"


def test_a_mistyped_meal_word_with_a_cuisine_is_still_a_kind() -> None:
    assert _is_a_kind("dim sum restaruant")
    assert _is_a_kind("a sushi restauramt")


def test_a_name_that_looks_like_a_short_kind_stays_a_name() -> None:
    # One wrong letter on a five-letter word is too often a name ("Sushi Palace", not "place").
    assert not _is_a_kind("Sushi Palace")
