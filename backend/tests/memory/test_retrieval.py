"""Saved-trip retrieval (scenario 7, retrieval level) and the shared query/summary vocabulary."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from backend.db.factory import build_repositories
from backend.db.repositories.base import Repositories
from backend.db.repositories.memory import cosine_similarity
from backend.memory.embeddings import HashingEmbeddingClient
from backend.memory.reranker import SimilarityReranker
from backend.memory.retrieval import (
    SavedTripRetriever,
    budget_band,
    plan_summary_text,
    trip_query_text,
)
from backend.schemas.common import Money
from backend.schemas.memory import PreferenceProfile, SavedTripRecord, SimilarityHit
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import TripPlan
from backend.settings import Settings
from backend.tests.memory.helpers import default_settings, make_context, make_plan


@dataclass
class Rig:
    settings: Settings
    repos: Repositories
    embedder: HashingEmbeddingClient
    retriever: SavedTripRetriever
    saved: dict[str, str] = field(default_factory=dict)

    async def save(self, plan: TripPlan, *, rating: int = 5, trip_id: str | None = None) -> str:
        trip_id = trip_id or f"trip-{len(self.saved) + 1}"
        summary = plan_summary_text(plan)
        await self.repos.trips.add(
            SavedTripRecord(
                trip_id=trip_id,
                destination=plan.destination,
                rating=rating,
                summary=summary,
                plan=plan,
            )
        )
        [vector] = await self.embedder.embed([summary])
        await self.repos.embeddings.upsert(trip_id, self.embedder.model, vector)
        self.saved[trip_id] = plan.destination
        return trip_id


def make_rig(**setting_overrides: object) -> Rig:
    settings = default_settings(**setting_overrides)
    repos = build_repositories(settings)
    embedder = HashingEmbeddingClient(dim=settings.embedding_dim)
    retriever = SavedTripRetriever(
        embedder=embedder,
        trips=repos.trips,
        embeddings=repos.embeddings,
        reranker=SimilarityReranker(),
        settings=settings,
    )
    return Rig(settings=settings, repos=repos, embedder=embedder, retriever=retriever)


async def similarity(embedder: HashingEmbeddingClient, a: str, b: str) -> float:
    va, vb = await embedder.embed([a, b])
    return cosine_similarity(va, vb)


# ---- scenario 7 ---------------------------------------------------------------------------------
async def test_s07_similar_kyoto_request_retrieves_the_saved_kyoto_plan() -> None:
    rig = make_rig()
    assert rig.settings.similarity_cutoff == 0.75
    trip_id = await rig.save(make_plan("Kyoto"), rating=5)

    found = await rig.retriever.retrieve(make_context("Kyoto"))

    assert [t.trip_id for t in found] == [trip_id]
    assert found[0].similarity >= rig.settings.similarity_cutoff
    assert found[0].destination == "Kyoto" and found[0].rating == 5
    assert found[0].plan.destination == "Kyoto"
    assert found[0].summary == plan_summary_text(make_plan("Kyoto"))


async def test_s07_hong_kong_request_does_not_retrieve_the_kyoto_plan() -> None:
    rig = make_rig()
    await rig.save(make_plan("Kyoto"))

    # Same party size, days and budget; only the destination differs.
    assert await rig.retriever.retrieve(make_context("Hong Kong")) == []
    # Even when the user's hotel style happens to match the saved plan.
    assert await rig.retriever.retrieve(make_context("Hong Kong", hotel_style="ryokan")) == []


async def test_s07_similarity_numbers_kyoto_vs_kyoto_and_hong_kong() -> None:
    embedder = HashingEmbeddingClient(dim=1536)
    plan_text = plan_summary_text(make_plan("Kyoto"))
    kyoto = await similarity(embedder, plan_text, trip_query_text(make_context("Kyoto")))
    kyoto_styled = await similarity(
        embedder, plan_text, trip_query_text(make_context("Kyoto", hotel_style="ryokan"))
    )
    kyoto_country = await similarity(
        embedder, plan_text, trip_query_text(make_context("Kyoto, Japan"))
    )
    hong_kong = await similarity(embedder, plan_text, trip_query_text(make_context("Hong Kong")))
    assert kyoto >= 0.75 and kyoto_styled >= 0.75 and kyoto_country >= 0.75
    assert kyoto_styled > kyoto  # a matching hotel style makes it closer, not further
    assert hong_kong < 0.75
    assert hong_kong < kyoto - 0.4  # clearly lower, not marginally lower


async def test_s07_only_the_matching_destination_comes_back_from_a_mixed_store() -> None:
    rig = make_rig()
    kyoto = await rig.save(make_plan("Kyoto"))
    await rig.save(make_plan("Hong Kong"))
    await rig.save(make_plan("Paris"))

    found = await rig.retriever.retrieve(make_context("Kyoto"))
    assert [t.trip_id for t in found] == [kyoto]


async def test_s07_results_are_limited_to_top_k_and_reranked_by_similarity_then_rating() -> None:
    rig = make_rig(retrieval_top_k=2)
    low = await rig.save(make_plan("Kyoto", plan_id="a"), rating=2, trip_id="t-low")
    high = await rig.save(make_plan("Kyoto", plan_id="b"), rating=5, trip_id="t-high")
    # A third Kyoto plan that is a worse match (different party size and days).
    await rig.save(make_plan("Kyoto", days=5, party_size=4), rating=5, trip_id="t-worse")

    found = await rig.retriever.retrieve(make_context("Kyoto"))

    assert len(found) == 2
    assert [t.trip_id for t in found] == [high, low]  # equal similarity -> higher rating first
    assert found[0].similarity == pytest.approx(found[1].similarity)


async def test_s07_a_lowered_cutoff_is_honoured_and_a_raised_one_filters_everything() -> None:
    plan = make_plan("Kyoto")
    permissive = make_rig(similarity_cutoff=0.1)
    await permissive.save(plan)
    assert len(await permissive.retriever.retrieve(make_context("Hong Kong"))) == 1

    strict = make_rig(similarity_cutoff=0.99)
    await strict.save(plan)
    assert await strict.retriever.retrieve(make_context("Kyoto")) == []


async def test_s07_cutoff_is_enforced_even_if_a_repository_returns_a_weak_hit() -> None:
    rig = make_rig()
    trip_id = await rig.save(make_plan("Kyoto"))

    class LeakyEmbeddings:
        async def upsert(self, trip_id: str, model: str, vector: Sequence[float]) -> None:
            raise AssertionError("unused")

        async def search(
            self, vector: Sequence[float], *, model: str, top_k: int, min_similarity: float
        ) -> list[SimilarityHit]:
            return [SimilarityHit(trip_id=trip_id, similarity=0.5)]  # ignores min_similarity

    retriever = SavedTripRetriever(
        embedder=rig.embedder,
        trips=rig.repos.trips,
        embeddings=LeakyEmbeddings(),
        reranker=SimilarityReranker(),
        settings=rig.settings,
    )
    assert await retriever.retrieve(make_context("Kyoto")) == []


async def test_retrieval_is_restricted_to_the_embedders_model() -> None:
    rig = make_rig()
    plan = make_plan("Kyoto")
    await rig.save(plan)
    other = HashingEmbeddingClient(dim=rig.settings.embedding_dim, model="other-model")
    retriever = SavedTripRetriever(
        embedder=other,
        trips=rig.repos.trips,
        embeddings=rig.repos.embeddings,
        reranker=SimilarityReranker(),
        settings=rig.settings,
    )
    assert await retriever.retrieve(make_context("Kyoto")) == []


async def test_empty_request_retrieves_nothing_without_embedding() -> None:
    rig = make_rig()
    await rig.save(make_plan("Kyoto"))

    class ExplodingEmbedder:
        model = "x"
        dim = 1536

        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise AssertionError("an empty request must not be embedded")

    retriever = SavedTripRetriever(
        embedder=ExplodingEmbedder(),
        trips=rig.repos.trips,
        embeddings=rig.repos.embeddings,
        reranker=SimilarityReranker(),
        settings=rig.settings,
    )
    empty = TripContext()
    assert trip_query_text(empty) == ""
    assert await retriever.retrieve(empty) == []


async def test_request_with_a_different_city_in_cjk_does_not_match() -> None:
    rig = make_rig()
    await rig.save(make_plan("東京", places=[("淺草寺", "temple"), ("上野公園", "park")]))
    assert len(await rig.retriever.retrieve(make_context("東京"))) == 1
    assert await rig.retriever.retrieve(make_context("京都")) == []


# ---- text builders ------------------------------------------------------------------------------
def test_query_and_summary_share_one_vocabulary() -> None:
    plan = make_plan("Kyoto")
    summary = plan_summary_text(plan)
    query = trip_query_text(make_context("Kyoto", hotel_style="ryokan", must_visit=["Kiyomizu-dera"]))

    assert summary == (
        "kyoto | days: 3 | party: 2 | budget band: mid | hotel style: ryokan | "
        "categories: shrine temple garden | "
        "places: Fushimi Inari Taisha; Kiyomizu-dera; Arashiyama Bamboo Grove"
    )
    assert query == (
        "kyoto | days: 3 | party: 2 | budget band: mid | hotel style: ryokan | "
        "places: Kiyomizu-dera"
    )


def test_query_uses_preferences_for_liked_categories_and_a_missing_hotel_style() -> None:
    prefs = PreferenceProfile(
        hotel_style="ryokan",
        liked_categories=["Shrine", "temple", "shrine"],
        budget_hint=Money(amount=1, currency="USD"),
        party_size_hint=9,
    )
    text = trip_query_text(make_context("Kyoto", budget=None, party_size=None), prefs)
    assert text == "kyoto | days: 3 | hotel style: ryokan | categories: shrine temple"
    # An explicit hotel style on the request wins over the profile.
    assert "hotel style: capsule" in trip_query_text(
        make_context("Kyoto", hotel_style="capsule"), prefs
    )


def test_avoid_constraints_are_not_written_into_the_query() -> None:
    text = trip_query_text(make_context("Kyoto", avoid=["casino"], must_visit=["Kinkaku-ji"]))
    assert "casino" not in text and "Kinkaku-ji" in text


def test_missing_destination_keeps_an_empty_headline_slot() -> None:
    assert trip_query_text(make_context(None)) == " | days: 3 | party: 2 | budget band: mid"


def test_destination_headline_uses_the_city_part_only() -> None:
    assert trip_query_text(make_context("Kyoto, Japan")).startswith("kyoto | ")
    assert plan_summary_text(make_plan("Kyoto, Japan")).startswith("kyoto | ")


def test_summary_lists_only_itinerary_places_and_caps_lengths() -> None:
    places = [(f"Spot {i}", f"cat{i % 7}") for i in range(12)]
    plan = make_plan("Kyoto", days=3, places=places)
    summary = plan_summary_text(plan)
    # Only the 3 places the itinerary visits (one item per day), not the 12 candidates.
    assert summary.count(";") == 2
    categories = next(p for p in summary.split(" | ") if p.startswith("categories:"))
    assert len(categories.split()) - 1 <= 5


def test_summary_without_hotel_or_budget_omits_those_fields() -> None:
    summary = plan_summary_text(make_plan("Kyoto", hotel_style=None, budget=None))
    assert "hotel style" not in summary and "budget band" not in summary


@pytest.mark.parametrize(
    ("amount", "currency", "days", "party", "band"),
    [
        (100, "USD", 2, 1, "low"),  # 50 USD per person per day
        (120, "USD", 2, 1, "mid"),  # 60 is the first mid value
        (319, "USD", 2, 1, "mid"),  # 159.5
        (320, "USD", 2, 1, "high"),  # 160 is the first high value
        (800, "USD", 3, 2, "mid"),  # 133
        (1000, "USD", 3, 2, "high"),  # 167
        (120000, "JPY", 3, 2, "mid"),  # ~134 USD
        (30000, "JPY", 3, 2, "low"),  # ~33 USD
        (6000, "HKD", 2, 2, "high"),  # ~192 USD
        (500, "XXX", 1, 1, "high"),  # unknown currency is treated as USD
    ],
)
def test_budget_band_thresholds(amount: float, currency: str, days: int, party: int, band: str) -> None:
    assert budget_band(Money(amount=amount, currency=currency), days=days, party_size=party) == band


def test_budget_band_is_none_without_a_budget_or_days() -> None:
    assert budget_band(None, days=3, party_size=2) is None
    assert budget_band(Money(amount=100, currency="USD"), days=None, party_size=2) is None
    # A missing party size counts as one traveller.
    assert budget_band(Money(amount=300, currency="USD"), days=3, party_size=None) == "mid"
