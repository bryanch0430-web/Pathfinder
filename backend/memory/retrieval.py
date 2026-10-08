"""Saved-trip retrieval (RAG over plans users marked useful).

Proposal: "A saved trip is retrieved only when it is above the similarity cutoff, so a weak
match cannot steer a new plan."

Both texts below use ONE composer and ONE vocabulary - same field order, same labels, same
budget bands - so a saved plan and a new request about the same trip land close together in the
embedding space whichever embedder is configured. Format (fields joined by `FIELD_SEPARATOR`;
a field is omitted when its value is unknown, except the leading destination slot which stays
as an empty headline so the next field is never mistaken for it):

    kyoto | days: 3 | party: 2 | budget band: mid | hotel style: ryokan |
    categories: shrine temple garden | places: Fushimi Inari Taisha; Kiyomizu-dera

The destination (city part only) leads, with no label, because it is the headline field the
hashing embedder up-weights (see `HashingEmbeddingClient`). Hard AVOID constraints are deliberately NOT written
into the query text: embedding "avoid casinos" would pull plans that contain casinos closer, the
opposite of the intent (avoided categories live in `PreferenceProfile.avoided_categories`).
"""

from __future__ import annotations

from collections import Counter

from backend.db.repositories.base import EmbeddingRepository, TripRepository
from backend.memory.embeddings import FIELD_SEPARATOR, EmbeddingClient
from backend.memory.reranker import Reranker
from backend.schemas.common import Money
from backend.schemas.memory import PreferenceProfile, SimilarTrip
from backend.schemas.trip import ConstraintKind, TripContext
from backend.schemas.trip_plan import TripPlan
from backend.settings import Settings

MAX_CATEGORIES = 5
MAX_PLACES = 5

# ---- Budget bands ------------------------------------------------------------------------------
# Band = per-person-per-day amount, converted to USD with the COARSE static rates below (only
# used to bucket a budget, never to price anything):
#     low   < 60 USD          mid   60 .. < 160 USD          high   >= 160 USD
LOW_BAND_MAX_USD = 60.0
MID_BAND_MAX_USD = 160.0
_USD_PER_UNIT: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CHF": 1.1,
    "CAD": 0.73,
    "AUD": 0.65,
    "JPY": 0.0067,
    "CNY": 0.14,
    "HKD": 0.128,
    "TWD": 0.031,
    "KRW": 0.00072,
    "SGD": 0.74,
    "THB": 0.028,
    "MYR": 0.21,
}


def budget_band(budget: Money | None, *, days: int | None, party_size: int | None) -> str | None:
    """`low` / `mid` / `high` from the per-person-per-day amount; None when it cannot be
    computed (no budget or unknown number of days). Unknown currencies are treated as USD and a
    missing party size as one traveller."""
    if budget is None or not days or days < 1:
        return None
    travellers = party_size if party_size and party_size > 0 else 1
    usd = budget.amount * _USD_PER_UNIT.get(budget.currency.upper(), 1.0)
    per_person_per_day = usd / travellers / days
    if per_person_per_day < LOW_BAND_MAX_USD:
        return "low"
    if per_person_per_day < MID_BAND_MAX_USD:
        return "mid"
    return "high"


# ---- Shared composer ---------------------------------------------------------------------------
def _clean(value: str) -> str:
    """Single-line, delimiter-free text (a stray '|' would split a field)."""
    return " ".join(value.replace("|", " ").split())


def _headline(destination: str | None) -> str:
    """The city part of a destination ("Kyoto, Japan" -> "kyoto"), so a request that adds the
    country still matches a plan saved under the bare city name (and vice versa)."""
    return _clean((destination or "").split(",")[0]).lower()


def _unique(values: list[str], cap: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = _clean(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out[:cap]


def _compose(
    *,
    destination: str | None,
    days: int | None,
    party_size: int | None,
    band: str | None,
    hotel_style: str | None,
    categories: list[str],
    places: list[str],
) -> str:
    fields = [_headline(destination)]
    if days:
        fields.append(f"days: {days}")
    if party_size:
        fields.append(f"party: {party_size}")
    if band:
        fields.append(f"budget band: {band}")
    if hotel_style and _clean(hotel_style):
        fields.append(f"hotel style: {_clean(hotel_style).lower()}")
    if categories:
        fields.append("categories: " + " ".join(c.lower() for c in categories))
    if places:
        fields.append("places: " + "; ".join(places))
    return FIELD_SEPARATOR.join(fields)


def trip_query_text(context: TripContext, preferences: PreferenceProfile | None = None) -> str:
    """Deterministic text for a new request: destination, days, party size, budget band,
    hotel style, constraint values, liked categories.

    Must-visit constraint values are written as `places`; liked categories (from the preference
    profile) as `categories`; the profile's hotel style fills in when the request names none.
    Budget / party hints from the profile are NOT used: they describe past trips, not this one.
    """
    hotel_style = context.hotel_style or (preferences.hotel_style if preferences else None)
    liked = preferences.liked_categories if preferences else []
    must_visit = [c.value for c in context.hard_constraints if c.kind is ConstraintKind.MUST_VISIT]
    return _compose(
        destination=context.destination,
        days=context.days,
        party_size=context.party_size,
        band=budget_band(context.budget, days=context.days, party_size=context.party_size),
        hotel_style=hotel_style,
        categories=_unique(liked, MAX_CATEGORIES),
        places=_unique(must_visit, MAX_PLACES),
    )


def _plan_categories(plan: TripPlan) -> list[str]:
    """Place categories, most frequent first (ties: first seen), case-insensitively merged."""
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    for index, place in enumerate(plan.places):
        key = _clean(place.category).casefold()
        if key:
            counts[key] += 1
            first_seen.setdefault(key, index)
    ordered = sorted(counts, key=lambda k: (-counts[k], first_seen[k]))
    return ordered[:MAX_CATEGORIES]


def _plan_place_names(plan: TripPlan) -> list[str]:
    """Names of the places the itinerary actually visits, in visiting order (the plan's other
    candidate places are not part of the trip); all places if no item references one."""
    visited: list[str] = []
    for item in plan.all_items():
        place = plan.place(item.place_id)
        if place is not None:
            visited.append(place.name)
    names = visited or [place.name for place in plan.places]
    return _unique(names, MAX_PLACES)


def plan_summary_text(plan: TripPlan) -> str:
    """Deterministic text for a saved plan in the SAME vocabulary as trip_query_text (destination,
    days, party size, budget band, hotel style, place categories and names)."""
    days = len(plan.days)
    budget = plan.budget
    if budget is None and plan.cost is not None:
        budget = Money(amount=plan.cost.total, currency=plan.cost.currency)
    return _compose(
        destination=plan.destination,
        days=days,
        party_size=plan.party_size,
        band=budget_band(budget, days=days, party_size=plan.party_size),
        hotel_style=plan.hotel.hotel.style if plan.hotel else None,
        categories=_plan_categories(plan),
        places=_plan_place_names(plan),
    )


class SavedTripRetriever:
    def __init__(
        self,
        *,
        embedder: EmbeddingClient,
        trips: TripRepository,
        embeddings: EmbeddingRepository,
        reranker: Reranker,
        settings: Settings,
    ) -> None:
        self._embedder = embedder
        self._trips = trips
        self._embeddings = embeddings
        self._reranker = reranker
        self._settings = settings

    async def retrieve(
        self, context: TripContext, preferences: PreferenceProfile | None = None
    ) -> list[SimilarTrip]:
        """Embed the query, search with top_k=settings.retrieval_top_k and
        min_similarity=settings.similarity_cutoff, load the trips, rerank, return. Matches below
        the cutoff are never returned."""
        cutoff = self._settings.similarity_cutoff
        query = trip_query_text(context, preferences)
        if not query.replace(FIELD_SEPARATOR.strip(), "").strip():
            return []  # nothing to match on
        [vector] = await self._embedder.embed([query])
        hits = await self._embeddings.search(
            vector,
            model=self._embedder.model,
            top_k=self._settings.retrieval_top_k,
            min_similarity=cutoff,
        )
        # Second guard: the cutoff is a hard rule, not something to trust a repository for.
        hits = [hit for hit in hits if hit.similarity >= cutoff]
        if not hits:
            return []
        records = {r.trip_id: r for r in await self._trips.get_many([h.trip_id for h in hits])}
        candidates = [
            SimilarTrip(
                trip_id=hit.trip_id,
                destination=records[hit.trip_id].destination,
                rating=records[hit.trip_id].rating,
                similarity=hit.similarity,
                summary=records[hit.trip_id].summary,
                plan=records[hit.trip_id].plan,
            )
            for hit in hits
            if hit.trip_id in records
        ]
        return await self._reranker.rerank(query, candidates)
