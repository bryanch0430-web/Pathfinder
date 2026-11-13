"""Ranking only labels: a rank, a score and up to three reasons, written by code from fields the sources gave."""

from __future__ import annotations

from datetime import date, datetime

from plan_samples import make_plan, make_route_plan

from app.application.ranking import (
    ALTERNATIVE_WEIGHTS,
    STAY_WEIGHTS,
    TRANSPORT_WEIGHTS,
    rank_alternatives,
    rank_stays,
    rank_tickets,
)
from app.domain.models import Money, PlanAlternative, StayMatch, TicketOption, TripPlan


def _stay(
    stay_id: str,
    price: float | None,
    rating: float | None,
    km: float | None,
    match: int | None = None,
):
    base = make_plan().hotels[0]
    return base.model_copy(
        update={
            "id": stay_id,
            "name": stay_id,
            "price_per_night": Money(amount=price, currency="HKD")
            if price is not None
            else None,
            "rating": rating,
            "nearest_stop_km": km,
            "match": StayMatch(
                wishes=["two beds"],
                verdict="meets",
                score=match,
                reason="",
                evidence=[],
                evidence_refs=[],
                rooms_checked=True,
            )
            if match is not None
            else None,
        }
    )


def _alt(
    alt_id: str,
    rating: float | None,
    metres: int | None,
    price_level: str | None = None,
) -> PlanAlternative:
    return PlanAlternative(
        id=alt_id,
        name=alt_id,
        rating=rating,
        distance_m=metres,
        price_level=price_level,
    )


def _ticket(
    ticket_id: str, minutes: int | None, price: float | None, mode: str = "train"
) -> TicketOption:
    return TicketOption(
        id=ticket_id,
        mode=mode,
        name=ticket_id,
        duration_minutes=minutes,
        price=Money(amount=price, currency="HKD") if price is not None else None,
    )


def test_the_weights_are_the_fixed_ones() -> None:
    assert STAY_WEIGHTS == {
        "price": 0.35,
        "rating": 0.25,
        "distance": 0.20,
        "match": 0.20,
    }
    assert ALTERNATIVE_WEIGHTS == {"rating": 0.40, "distance": 0.35, "price": 0.25}
    assert TRANSPORT_WEIGHTS == {"time": 0.60, "price": 0.40}


def test_stays_are_ranked_and_the_best_says_why() -> None:
    stays = [
        _stay("cheap_far", 800, 3.9, 6.0),
        _stay("best", 1000, 4.8, 0.4, 90),
        _stay("dear", 2400, 4.1, 2.0),
    ]

    rank_stays(stays, per_night_budget=1500)

    assert [s.id for s in sorted(stays, key=lambda s: s.rank)] == [
        "best",
        "cheap_far",
        "dear",
    ]
    best = stays[1]
    assert best.rank == 1 and 0 < best.score <= 1
    assert (
        "best rated" in best.reasons
        and "closest to your stops" in best.reasons
        and len(best.reasons) <= 3
    )
    assert (
        "within budget" in stays[0].reasons and "within budget" not in stays[2].reasons
    )


def test_a_stay_that_was_asked_for_something_it_has_gets_that_as_a_reason() -> None:
    stays = [_stay("a", 1000, 4.0, 1.0, 95), _stay("b", 1000, 4.0, 1.0, 20)]

    rank_stays(stays, per_night_budget=None)

    assert (
        stays[0].rank == 1 and "matches what you asked of the room" in stays[0].reasons
    )
    assert "matches what you asked of the room" not in stays[1].reasons


def test_options_that_tie_keep_the_order_they_came_in_and_say_nothing_special() -> None:
    stays = [_stay("first", 1000, 4.0, 1.0), _stay("second", 1000, 4.0, 1.0)]

    rank_stays(stays, per_night_budget=None)

    assert [s.rank for s in stays] == [1, 2] and stays[0].score == stays[1].score
    assert stays[0].reasons == stays[1].reasons == []


def test_a_missing_value_counts_as_the_middle_and_is_never_a_reason() -> None:
    stays = [
        _stay("known", 1000, 4.9, 0.5),
        _stay("unknown", None, None, None),
        _stay("worse", 3000, 3.0, 5.0),
    ]

    rank_stays(stays, per_night_budget=None)

    assert stays[0].rank == 1
    assert stays[1].reasons == []
    assert 0 < stays[1].score < stays[0].score


def test_alternatives_rank_by_rating_distance_and_price_level() -> None:
    alts = [
        _alt("far", 4.8, 2500, "$$$"),
        _alt("near", 4.5, 200, "$$"),
        _alt("meh", 3.5, 900, "$"),
    ]

    rank_alternatives(alts)

    assert [a.id for a in sorted(alts, key=lambda a: a.rank)] == ["near", "meh", "far"]
    assert "closest" in alts[1].reasons and "best rated" in alts[0].reasons
    assert "lowest price" in alts[2].reasons


def test_one_option_is_ranked_first_with_no_reason() -> None:
    only = [_alt("only", 4.0, 100)]

    rank_alternatives(only)

    assert only[0].rank == 1 and only[0].reasons == []


def test_transport_ranks_by_door_to_door_time_then_price() -> None:
    options = [
        _ticket("slow_cheap", 300, 100),
        _ticket(
            "flight", 60, 900, mode="flight"
        ),  # 60 minutes in the air + 150 at the airports
        _ticket("train", 150, 400),
    ]

    rank_tickets(options)

    # door to door: train 150, flight 60 + 150 = 210, slow 300. Scores: train .85, slow_cheap .40, flight .36
    # (the flight is 800 dearer than the slow option, which outweighs the 90 minutes it saves).
    assert [o.id for o in sorted(options, key=lambda o: o.rank)] == [
        "train",
        "slow_cheap",
        "flight",
    ]
    assert options[1].score == 0.36
    fastest = min(options, key=lambda o: o.rank)
    assert "fastest door to door" in fastest.reasons
    assert "cheapest" in options[0].reasons


def test_ranking_does_not_reorder_or_change_the_choice() -> None:
    plan = make_route_plan(["Kyoto", "Osaka"])
    plan.hotel_options = [
        plan.hotels[1].model_copy(
            update={"id": "opt_b2", "name": "B2", "segment_id": "seg_b"}
        ),
        plan.hotels[1],
    ]
    chosen = [t.id for t in plan.tickets if t.selected]
    options_before = [o.id for o in plan.hotel_options]

    plan.refresh_derived()

    assert [t.id for t in plan.tickets if t.selected] == chosen
    assert [o.id for o in plan.hotel_options] == options_before
    assert all(o.rank in (1, 2) for o in plan.hotel_options if o.segment_id == "seg_b")


def test_a_plan_is_ranked_per_city_per_leg_and_per_stop() -> None:
    plan = make_plan(alternatives=2)

    plan.refresh_derived()

    meal = next(item for _, item in plan.iter_items() if item.alternatives)
    assert sorted(a.rank for a in meal.alternatives) == [1, 2]
    assert {t.rank for t in plan.tickets} == {1, 2}
    assert datetime and date and TripPlan


def test_options_of_a_single_city_plan_are_ranked_together_whether_or_not_they_name_a_segment() -> None:
    plan = make_plan()
    chosen = plan.hotels[0]
    other = chosen.model_copy(update={"id": "opt_other", "name": "Other", "segment_id": ""})
    plan.hotel_options = [chosen.model_copy(update={"segment_id": "seg_1"}), other]

    plan.refresh_derived()

    assert sorted(o.rank for o in plan.hotel_options) == [1, 2]
