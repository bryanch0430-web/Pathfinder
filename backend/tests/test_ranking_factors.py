"""Three more ranking factors, all labelling only: review count, the traveller profile's fit for alternatives, and
business amenities for a business trip's stays. Hand-made lists, no sources."""

from __future__ import annotations

from plan_samples import make_plan

from app.application.ranking import (
    ALTERNATIVE_EXTRA_WEIGHTS,
    ALTERNATIVE_WEIGHTS,
    BUSINESS_STAY_EXTRA_WEIGHTS,
    BUSINESS_STAY_WEIGHTS,
    STAY_EXTRA_WEIGHTS,
    STAY_WEIGHTS,
    effective_weights,
    rank_alternatives,
    rank_stays,
)
from app.domain.models import PlanAlternative, TravellerProfile


def _stay(
    stay_id: str,
    *,
    reviews: int | None = None,
    amenities: list[str] | None = None,
    rating: float = 4.0,
):
    base = make_plan().hotels[0]
    return base.model_copy(
        update={
            "id": stay_id,
            "name": stay_id,
            "rating": rating,
            "review_count": reviews,
            "amenities": amenities or [],
            "distance_to_centre_km": 1.0,
            "nearest_stop_km": 1.0,
            "match": None,
        }
    )


def _alt(
    alt_id: str,
    *,
    reviews: int | None = None,
    types: list[str] | None = None,
    name: str | None = None,
) -> PlanAlternative:
    return PlanAlternative(
        id=alt_id,
        name=name or alt_id,
        rating=4.0,
        distance_m=500,
        review_count=reviews,
        source_types=types or [],
    )


def test_the_added_weights_are_fixed_and_every_blend_sums_to_one() -> None:
    assert STAY_EXTRA_WEIGHTS == {"reviews": 0.10}
    assert BUSINESS_STAY_EXTRA_WEIGHTS == {"reviews": 0.05, "amenities": 0.10}
    assert ALTERNATIVE_EXTRA_WEIGHTS == {"reviews": 0.10, "profile": 0.10}
    for base, extra in [
        (STAY_WEIGHTS, STAY_EXTRA_WEIGHTS),
        (BUSINESS_STAY_WEIGHTS, BUSINESS_STAY_EXTRA_WEIGHTS),
        (ALTERNATIVE_WEIGHTS, ALTERNATIVE_EXTRA_WEIGHTS),
    ]:
        blended = effective_weights(base, extra)
        assert abs(sum(blended.values()) - 1.0) < 1e-9
        assert set(blended) == set(base) | set(extra)
        for name, weight in extra.items():
            assert blended[name] == weight


def test_the_stay_with_far_more_reviews_ranks_first_and_says_so() -> None:
    stays = [_stay("few", reviews=40), _stay("many", reviews=4000)]

    rank_stays(stays, per_night_budget=None)

    assert stays[1].rank == 1 and stays[0].rank == 2
    assert "most reviewed" in stays[1].reasons
    assert "most reviewed" not in stays[0].reasons


def test_a_stay_without_a_review_count_is_neither_ranked_down_nor_given_a_reason() -> (
    None
):
    stays = [
        _stay("none", reviews=None),
        _stay("many", reviews=4000),
        _stay("few", reviews=10),
    ]

    rank_stays(stays, per_night_budget=None)

    by_id = {s.id: s for s in stays}
    assert by_id["many"].score > by_id["none"].score > by_id["few"].score
    assert by_id["none"].reasons == []


def test_equal_review_counts_change_nothing() -> None:
    stays = [_stay("a", reviews=300), _stay("b", reviews=300)]

    rank_stays(stays, per_night_budget=None)

    assert [s.rank for s in stays] == [1, 2] and stays[0].score == stays[1].score
    assert stays[0].reasons == stays[1].reasons == []


def test_reviews_never_re_order_the_list_or_change_the_chosen_stay() -> None:
    plan = make_plan()
    chosen = plan.hotels[0]
    many = _stay("many", reviews=9000)
    few = _stay("few", reviews=5)
    plan.hotel_options = [few, many, chosen.model_copy(update={"id": chosen.id})]
    ids_before = [o.id for o in plan.hotel_options]
    chosen_before = [h.id for h in plan.hotels]

    plan.refresh_derived()

    assert [o.id for o in plan.hotel_options] == ids_before
    assert [h.id for h in plan.hotels] == chosen_before


def test_business_amenities_lift_a_stay_on_a_business_trip_only() -> None:
    equipped = ["Free Wi-Fi", "Business center", "Pool"]
    plain = ["Pool", "Spa"]

    business = [_stay("plain", amenities=plain), _stay("equipped", amenities=equipped)]
    rank_stays(
        business,
        per_night_budget=None,
        weights=BUSINESS_STAY_WEIGHTS,
        distance_to="centre",
        extra_weights=BUSINESS_STAY_EXTRA_WEIGHTS,
    )
    leisure = [_stay("plain", amenities=plain), _stay("equipped", amenities=equipped)]
    rank_stays(leisure, per_night_budget=None)

    assert business[1].rank == 1 and business[0].rank == 2
    assert "business amenities" in business[1].reasons
    assert [s.rank for s in leisure] == [1, 2]
    assert all("business amenities" not in s.reasons for s in leisure)


def test_a_stay_whose_source_listed_no_amenities_counts_as_the_middle() -> None:
    stays = [
        _stay("unknown", amenities=[]),
        _stay("equipped", amenities=["Wi-Fi", "Meeting rooms"]),
        _stay("plain", amenities=["Pool"]),
    ]

    rank_stays(
        stays,
        per_night_budget=None,
        weights=BUSINESS_STAY_WEIGHTS,
        distance_to="centre",
        extra_weights=BUSINESS_STAY_EXTRA_WEIGHTS,
    )

    by_id = {s.id: s for s in stays}
    assert by_id["equipped"].score > by_id["unknown"].score > by_id["plain"].score
    assert by_id["unknown"].reasons == []


def test_a_plan_made_for_business_labels_its_stays_by_their_amenities() -> None:
    plan = make_plan()
    plain = _stay("plain", amenities=["Pool"])
    equipped = _stay("equipped", amenities=["Free WiFi", "Desk"])
    plan.hotel_options = [plain, equipped]
    plan.profile = TravellerProfile(purpose="business")

    plan.refresh_derived()

    by_id = {o.id: o for o in plan.hotel_options}
    assert [o.id for o in plan.hotel_options] == ["plain", "equipped"]
    assert (
        by_id["equipped"].rank == 1
        and "business amenities" in by_id["equipped"].reasons
    )
    plan.profile = TravellerProfile()
    plan.refresh_derived()
    assert all("business amenities" not in o.reasons for o in plan.hotel_options)


def test_an_alternative_with_far_more_reviews_ranks_first_and_says_so() -> None:
    alts = [_alt("few", reviews=12), _alt("many", reviews=8000)]

    rank_alternatives(alts)

    assert alts[1].rank == 1 and alts[0].rank == 2
    assert "most reviewed" in alts[1].reasons


def test_a_family_place_suits_a_trip_with_children_and_only_that_trip() -> None:
    zoo = _alt("zoo", types=["zoo", "tourist_attraction"])
    office = _alt("gallery", types=["art_gallery"])

    with_children = [office.model_copy(), zoo.model_copy()]
    rank_alternatives(with_children, profile=TravellerProfile(with_children=True))
    without = [office.model_copy(), zoo.model_copy()]
    rank_alternatives(without, profile=TravellerProfile())
    no_profile = [office.model_copy(), zoo.model_copy()]
    rank_alternatives(no_profile)

    assert with_children[1].rank == 1 and "suits children" in with_children[1].reasons
    assert "suits children" not in with_children[0].reasons
    for options in (without, no_profile):
        assert [a.rank for a in options] == [1, 2]
        assert all(a.reasons == [] for a in options)


def test_a_museum_for_children_suits_children_by_its_name() -> None:
    museum = _alt("m1", types=["museum"], name="Children's Science Museum")
    other = _alt("m2", types=["museum"], name="City Museum of Law")

    options = [other, museum]
    rank_alternatives(options, profile=TravellerProfile(with_children=True))

    assert options[1].rank == 1 and "suits children" in options[1].reasons


def test_a_plan_with_children_labels_its_alternatives_without_reordering_them() -> None:
    plan = make_plan(alternatives=2)
    item = next(i for _, i in plan.iter_items() if i.alternatives)
    first, second = item.alternatives
    first.source_types, second.source_types = ["art_gallery"], ["playground"]
    order_before = [a.id for a in item.alternatives]
    plan.profile = TravellerProfile(with_children=True)

    plan.refresh_derived()

    assert [a.id for a in item.alternatives] == order_before
    assert "suits children" in second.reasons
