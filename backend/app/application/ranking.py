"""Rank the options of a stay, a stop or a leg: a rank, a score and up to three reasons.

Ranking only labels. It never re-orders a list, never changes which option is chosen, and never invents a value:
every factor is read from a field a source gave, scaled 0..1 within the option list (the best option of a
factor scores 1, the worst 0), and a missing value scores 0.5 and is never given as a reason. The weights and the
phrases are fixed here; no model writes either. Three further factors (review count, a trip's profile fit for the
alternatives of a stop, business amenities of a business trip's stays) take a fixed share and the original weights
share the rest (``effective_weights``). The phrases are English, the plan's source language; the
interface shows them in the reader's language from its own table (``reason.*``).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from app.application.profile_rules import family_suited

if TYPE_CHECKING:
    from app.domain.models import (
        HotelStay,
        PlanAlternative,
        TicketOption,
        TravellerProfile,
        TripPlan,
    )

STAY_WEIGHTS = {"price": 0.35, "rating": 0.25, "distance": 0.20, "match": 0.20}
#: A business trip: distance (to the centre) counts 0.35 and price 0.20.
BUSINESS_STAY_WEIGHTS = {"price": 0.20, "rating": 0.25, "distance": 0.35, "match": 0.20}
ALTERNATIVE_WEIGHTS = {"rating": 0.40, "distance": 0.35, "price": 0.25}
TRANSPORT_WEIGHTS = {"time": 0.60, "price": 0.40}

#: Shares taken by the added factors; the weights above share what is left (see ``effective_weights``).
#: Review count counts for every stay and alternative; business amenities only for a business trip's stays;
#: profile fit only for the alternatives of a trip with children.
STAY_EXTRA_WEIGHTS = {"reviews": 0.10}
BUSINESS_STAY_EXTRA_WEIGHTS = {"reviews": 0.05, "amenities": 0.10}
ALTERNATIVE_EXTRA_WEIGHTS = {"reviews": 0.10, "profile": 0.10}

#: What a business traveller looks for in a stay's amenities, as words in the source's own amenity labels.
BUSINESS_AMENITY_WORDS = {
    "wifi": ("wi-fi", "wifi"),
    "business centre": ("business center", "business centre"),
    "meeting": ("meeting", "conference"),
    "workspace": ("desk", "workspace", "work space", "coworking"),
}

#: Flights add this many minutes at the airports to the time in the air.
AIRPORT_MINUTES = 150
MAX_REASONS = 3
#: A stay-research match counts as a reason from this score up, and only when the verdict is ``meets``.
MATCH_REASON_SCORE = 70

#: The only phrases a reason can be, by factor (code-written, never from a model).
REASONS = {
    "price_budget": "within budget",
    "price_low": "lowest price",
    "price_cheapest": "cheapest",
    "rating": "best rated",
    "distance_stops": "closest to your stops",
    "distance_centre": "closest to the centre",
    "distance": "closest",
    "match": "matches what you asked of the room",
    "time": "fastest door to door",
    "reviews": "most reviewed",
    "profile_children": "suits children",
    "amenities": "business amenities",
}

_NEUTRAL = 0.5
_PRICE_LEVELS = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


def _scaled(
    values: Sequence[float | None], *, higher_is_better: bool
) -> list[float | None]:
    """Each known value scaled 0..1 within the list (None stays None). With nothing to tell apart they all score 0.5."""
    known = [v for v in values if v is not None]
    if not known:
        return [None] * len(values)
    low, high = min(known), max(known)
    out: list[float | None] = []
    for value in values:
        if value is None:
            out.append(None)
        elif high == low:
            out.append(_NEUTRAL)
        else:
            share = (value - low) / (high - low)
            out.append(round(share if higher_is_better else 1 - share, 6))
    return out


def effective_weights(base: dict, extra: dict) -> dict:
    """The weights in use: each added factor keeps its own share and the base weights share the rest, so they sum to 1."""
    room = 1.0 - sum(extra.values())
    return {**{name: weight * room for name, weight in base.items()}, **extra}


def _review_scale(counts: Sequence[int | None]) -> list[float | None]:
    """Review counts scaled 0..1 on a log scale (4000 reviews against 40 is a clear lead, 4040 against 4000 is not)."""
    return _scaled(
        [math.log1p(c) if c is not None and c >= 0 else None for c in counts],
        higher_is_better=True,
    )


def _business_amenities(amenities: Sequence[str]) -> float | None:
    """How many of the business amenities a stay lists (wifi, business centre, meeting rooms, workspace); None when the
    source listed no amenities at all."""
    if not amenities:
        return None
    lowered = [str(a).lower() for a in amenities]
    return float(
        sum(
            1
            for words in BUSINESS_AMENITY_WORDS.values()
            if any(word in label for label in lowered for word in words)
        )
    )


def _best(scaled: Sequence[float | None], index: int) -> bool:
    """True when this option is the single best of a factor that told options apart (never on a tie or a gap)."""
    value = scaled[index]
    return value == 1.0 and sum(1 for other in scaled if other == 1.0) == 1


def _price_level(text: str | None) -> float | None:
    """A price level as a number, from Google's enum or a run of ``$`` signs; None for anything else (a range, free text)."""
    if not text:
        return None
    text = text.strip()
    if text in _PRICE_LEVELS:
        return float(_PRICE_LEVELS[text])
    if set(text) == {"$"}:
        return float(len(text))
    return None


def _same_currency(prices: Sequence[tuple[float, str] | None]) -> list[float | None]:
    """Amounts of the prices that share the first price's currency; amounts in another currency cannot be compared."""
    first = next((p for p in prices if p is not None), None)
    if first is None:
        return [None] * len(prices)
    return [p[0] if p is not None and p[1] == first[1] else None for p in prices]


def _finish(
    options: Sequence,
    factors: list[tuple[str, float, list[float | None], Callable[[int], str | None]]],
) -> None:
    """Write rank, score and reasons from the scaled factors: (name, weight, scaled values, phrase for an option or None)."""
    scores: list[float] = []
    for index in range(len(options)):
        total = 0.0
        for _name, weight, scaled, _phrase in factors:
            value = scaled[index]
            total += weight * (_NEUTRAL if value is None else value)
        scores.append(round(total, 4))
    order = sorted(range(len(options)), key=lambda i: (-scores[i], i))
    for rank, index in enumerate(order, start=1):
        option = options[index]
        option.rank = rank
        option.score = scores[index]
        reasons: list[str] = []
        for _name, _weight, scaled, phrase in sorted(factors, key=lambda f: -f[1]):
            if len(reasons) >= MAX_REASONS:
                break
            text = phrase(index)
            if text and text not in reasons and scaled[index] is not None:
                reasons.append(text)
        option.reasons = reasons


def rank_stays(
    stays: Sequence[HotelStay],
    *,
    per_night_budget: float | None = None,
    weights: dict | None = None,
    distance_to: str = "stops",
    extra_weights: dict | None = None,
) -> None:
    """Rank the stays offered for one city: price against the per-night budget, rating, distance to that city's stops,
    the stay-research match and the review count (and, when ``extra_weights`` has "amenities", the business amenities).
    ``per_night_budget`` is in the stays' currency; None when there is none."""
    if not stays:
        return
    extra = STAY_EXTRA_WEIGHTS if extra_weights is None else extra_weights
    weights = effective_weights(weights or STAY_WEIGHTS, extra)
    prices = [
        (s.price_per_night.amount, s.price_per_night.currency)
        if s.price_per_night
        else None
        for s in stays
    ]
    amounts = _same_currency(prices)
    price = _scaled(amounts, higher_is_better=False)
    rating = _scaled([s.rating for s in stays], higher_is_better=True)
    # Distance is to the city's stops, or to the centre for a business trip.
    centre = distance_to == "centre"
    distance = _scaled(
        [s.distance_to_centre_km if centre else s.nearest_stop_km for s in stays],
        higher_is_better=False,
    )
    match = _scaled(
        [
            float(s.match.score)
            if s.match is not None and s.match.verdict != "unclear"
            else None
            for s in stays
        ],
        higher_is_better=True,
    )

    def price_phrase(i: int) -> str | None:
        if amounts[i] is None:
            return None
        if per_night_budget is not None:
            return REASONS["price_budget"] if amounts[i] <= per_night_budget else None
        return REASONS["price_low"] if _best(price, i) else None

    def rating_phrase(i: int) -> str | None:
        return REASONS["rating"] if _best(rating, i) else None

    def distance_phrase(i: int) -> str | None:
        key = "distance_centre" if centre else "distance_stops"
        return REASONS[key] if _best(distance, i) else None

    def match_phrase(i: int) -> str | None:
        stay_match = stays[i].match
        if (
            stay_match is not None
            and stay_match.verdict == "meets"
            and stay_match.score >= MATCH_REASON_SCORE
        ):
            return REASONS["match"]
        return None

    reviews = _review_scale([s.review_count for s in stays])
    amenities = _scaled(
        [_business_amenities(s.amenities) for s in stays], higher_is_better=True
    )

    def reviews_phrase(i: int) -> str | None:
        return REASONS["reviews"] if _best(reviews, i) else None

    def amenities_phrase(i: int) -> str | None:
        return REASONS["amenities"] if _best(amenities, i) else None

    _finish(
        stays,
        [
            ("price", weights["price"], price, price_phrase),
            ("rating", weights["rating"], rating, rating_phrase),
            ("distance", weights["distance"], distance, distance_phrase),
            ("match", weights["match"], match, match_phrase),
            ("reviews", weights["reviews"], reviews, reviews_phrase),
            *(
                [("amenities", weights["amenities"], amenities, amenities_phrase)]
                if "amenities" in weights
                else []
            ),
        ],
    )


def rank_alternatives(
    options: Sequence[PlanAlternative],
    *,
    profile: TravellerProfile | None = None,
) -> None:
    """Rank the choices for one stop or meal: rating, distance from the default, price level and review count, and for
    a trip with children whether the place suits children (from the place source's own type labels)."""
    if not options:
        return
    extra = dict(ALTERNATIVE_EXTRA_WEIGHTS)
    if profile is None or not profile.with_children:
        del extra["profile"]
    weights = effective_weights(ALTERNATIVE_WEIGHTS, extra)
    reviews = _review_scale([o.review_count for o in options])
    suited = _scaled(
        [
            (
                1.0
                if family_suited(
                    {
                        "tags": o.source_types,
                        "name": o.name,
                        "description": o.source_summary,
                    }
                )
                else 0.0
            )
            if o.source_types
            else None
            for o in options
        ],
        higher_is_better=True,
    )
    rating = _scaled([o.rating for o in options], higher_is_better=True)
    distance = _scaled(
        [float(o.distance_m) if o.distance_m is not None else None for o in options],
        higher_is_better=False,
    )
    levels = [_price_level(o.price_level) for o in options]
    price = _scaled(levels, higher_is_better=False)

    _finish(
        options,
        [
            (
                "rating",
                weights["rating"],
                rating,
                lambda i: REASONS["rating"] if _best(rating, i) else None,
            ),
            (
                "distance",
                weights["distance"],
                distance,
                lambda i: REASONS["distance"] if _best(distance, i) else None,
            ),
            (
                "price",
                weights["price"],
                price,
                lambda i: REASONS["price_low"] if _best(price, i) else None,
            ),
            (
                "reviews",
                weights["reviews"],
                reviews,
                lambda i: REASONS["reviews"] if _best(reviews, i) else None,
            ),
            *(
                [
                    (
                        "profile",
                        weights["profile"],
                        suited,
                        lambda i: REASONS["profile_children"]
                        if _best(suited, i)
                        else None,
                    )
                ]
                if "profile" in weights
                else []
            ),
        ],
    )


def door_to_door_minutes(option: TicketOption) -> float | None:
    """Minutes from door to door: the quoted duration, plus the time at the airports for a flight; None without a duration."""
    if option.duration_minutes is None:
        return None
    extra = AIRPORT_MINUTES if option.mode == "flight" else 0
    return float(option.duration_minutes + extra)


def rank_tickets(options: Sequence[TicketOption]) -> None:
    """Rank the options of one leg: door-to-door time, then price."""
    if not options:
        return
    minutes = [door_to_door_minutes(o) for o in options]
    time = _scaled(minutes, higher_is_better=False)
    amounts = _same_currency(
        [(o.price.amount, o.price.currency) if o.price else None for o in options]
    )
    price = _scaled(amounts, higher_is_better=False)

    _finish(
        options,
        [
            (
                "time",
                TRANSPORT_WEIGHTS["time"],
                time,
                lambda i: REASONS["time"] if _best(time, i) else None,
            ),
            (
                "price",
                TRANSPORT_WEIGHTS["price"],
                price,
                lambda i: REASONS["price_cheapest"] if _best(price, i) else None,
            ),
        ],
    )


def per_night_budget(plan: TripPlan) -> tuple[float, str] | None:
    """What is left of the budget after the chosen transport, per night, with its currency; None when there is no budget
    or no nights. Transport in another currency than the budget is left out (it cannot be subtracted honestly)."""
    if plan.budget is None or plan.dates.night_count < 1:
        return None
    spent = sum(
        t.price.amount
        for t in plan.tickets
        if t.selected
        and t.price is not None
        and t.price.currency == plan.budget.currency
    )
    return max(
        0.0, plan.budget.amount - spent
    ) / plan.dates.night_count, plan.budget.currency


def rank_plan(plan: TripPlan) -> None:
    """Label every list of options in the plan; the chosen option and the order of every list stay as they are."""
    allowance = per_night_budget(plan)

    def stays_of(group: list[HotelStay]) -> None:
        budget = None
        if allowance is not None and group:
            first = next(
                (s.price_per_night for s in group if s.price_per_night is not None),
                None,
            )
            if first is not None and first.currency == allowance[1]:
                budget = allowance[0]
        business = plan.profile is not None and plan.profile.purpose == "business"
        if business:
            rank_stays(
                group,
                per_night_budget=budget,
                weights=BUSINESS_STAY_WEIGHTS,
                distance_to="centre",
                extra_weights=BUSINESS_STAY_EXTRA_WEIGHTS,
            )
        else:
            rank_stays(group, per_night_budget=budget)

    by_city: dict[str, list] = {}
    for stay in plan.hotel_options:
        # An option with no segment belongs to the first city (a single-city plan's options carry none).
        by_city.setdefault(stay.segment_id or plan.segments[0].id, []).append(stay)
    for group in by_city.values():
        stays_of(group)
    # A chosen stay is a copy of one of the options: it carries that option's label.
    labelled = {stay.id: stay for stay in plan.hotel_options}
    for stay in plan.hotels:
        option = labelled.get(stay.id)
        if option is not None:
            stay.rank, stay.score, stay.reasons = (
                option.rank,
                option.score,
                list(option.reasons),
            )

    by_leg: dict[str, list] = {}
    for ticket in plan.tickets:
        by_leg.setdefault(ticket.leg_id, []).append(ticket)
    for group in by_leg.values():
        rank_tickets(group)

    for _day, item in plan.iter_items():
        if item.alternatives:
            rank_alternatives(item.alternatives, profile=plan.profile)
