"""In-memory repositories: same cutoff / ordering semantics as the PostgreSQL ones."""

from __future__ import annotations

import math

import pytest

from backend.db.repositories.errors import DuplicateTripError, EmbeddingDimensionError
from backend.db.repositories.memory import (
    InMemoryEmbeddingRepository,
    InMemoryPreferenceRepository,
    InMemoryTripRepository,
    cosine_similarity,
)
from backend.schemas.common import Money
from backend.schemas.memory import PreferenceProfile, SavedTripRecord
from backend.tests.memory.helpers import make_plan

MODEL = "test-model"


def record(trip_id: str, destination: str = "Kyoto", rating: int = 4) -> SavedTripRecord:
    plan = make_plan(destination, plan_id=f"plan-{trip_id}")
    return SavedTripRecord(
        trip_id=trip_id, destination=destination, rating=rating, summary=f"s-{trip_id}", plan=plan
    )


# ---- trips ---------------------------------------------------------------------------------------
async def test_trip_add_get_roundtrip_returns_independent_copies() -> None:
    repo = InMemoryTripRepository()
    original = record("t1")
    await repo.add(original)
    original.plan.destination = "mutated after add"

    fetched = await repo.get("t1")
    assert fetched is not None and fetched.plan.destination == "Kyoto"
    fetched.plan.destination = "mutated after get"
    again = await repo.get("t1")
    assert again is not None and again.plan.destination == "Kyoto"
    assert await repo.get("missing") is None


async def test_trip_get_many_follows_the_requested_order_and_skips_unknown_ids() -> None:
    repo = InMemoryTripRepository()
    for trip_id in ("a", "b", "c"):
        await repo.add(record(trip_id))
    found = await repo.get_many(["c", "nope", "a"])
    assert [r.trip_id for r in found] == ["c", "a"]
    assert await repo.get_many([]) == []


async def test_adding_the_same_trip_id_twice_is_rejected() -> None:
    repo = InMemoryTripRepository()
    await repo.add(record("dup"))
    with pytest.raises(DuplicateTripError):
        await repo.add(record("dup", destination="Paris"))
    stored = await repo.get("dup")
    assert stored is not None and stored.destination == "Kyoto"


# ---- embeddings: the similarity cutoff -----------------------------------------------------------
async def test_hit_below_min_similarity_is_excluded_and_hit_at_or_above_is_included() -> None:
    repo = InMemoryEmbeddingRepository()
    query = [1.0, 0.0]
    await repo.upsert("at", MODEL, [3.0, 4.0])  # cosine == 0.6 exactly (3 / 5)
    await repo.upsert("below", MODEL, [2.9, 4.0])  # cosine ~ 0.587
    await repo.upsert("above", MODEL, [4.0, 3.0])  # cosine == 0.8

    hits = await repo.search(query, model=MODEL, top_k=10, min_similarity=0.6)
    assert [h.trip_id for h in hits] == ["above", "at"]  # best first; "at" is INCLUDED
    assert hits[0].similarity == pytest.approx(0.8)
    assert hits[1].similarity == 0.6
    assert "below" not in {h.trip_id for h in hits}

    everything = await repo.search(query, model=MODEL, top_k=10, min_similarity=0.5)
    assert [h.trip_id for h in everything] == ["above", "at", "below"]
    assert await repo.search(query, model=MODEL, top_k=10, min_similarity=0.81) == []


async def test_search_respects_top_k_and_breaks_ties_by_trip_id() -> None:
    repo = InMemoryEmbeddingRepository()
    for trip_id in ("c", "a", "b"):
        await repo.upsert(trip_id, MODEL, [1.0, 0.0])
    await repo.upsert("far", MODEL, [0.0, 1.0])
    hits = await repo.search([1.0, 0.0], model=MODEL, top_k=2, min_similarity=0.5)
    assert [h.trip_id for h in hits] == ["a", "b"]
    assert await repo.search([1.0, 0.0], model=MODEL, top_k=0, min_similarity=0.0) == []


async def test_search_is_restricted_to_the_requested_model() -> None:
    repo = InMemoryEmbeddingRepository()
    await repo.upsert("t", "model-a", [1.0, 0.0])
    await repo.upsert("t", "model-b", [0.0, 1.0])
    a = await repo.search([1.0, 0.0], model="model-a", top_k=5, min_similarity=0.9)
    b = await repo.search([1.0, 0.0], model="model-b", top_k=5, min_similarity=0.9)
    assert [h.trip_id for h in a] == ["t"] and b == []
    assert await repo.search([1.0, 0.0], model="unknown", top_k=5, min_similarity=-1.0) == []


async def test_upsert_replaces_the_vector_for_the_same_trip_and_model() -> None:
    repo = InMemoryEmbeddingRepository()
    await repo.upsert("t", MODEL, [1.0, 0.0])
    await repo.upsert("t", MODEL, [0.0, 1.0])
    assert await repo.search([1.0, 0.0], model=MODEL, top_k=5, min_similarity=0.5) == []
    hits = await repo.search([0.0, 1.0], model=MODEL, top_k=5, min_similarity=0.5)
    assert [h.trip_id for h in hits] == ["t"]


async def test_zero_vectors_have_similarity_zero_on_either_side() -> None:
    repo = InMemoryEmbeddingRepository()
    await repo.upsert("zero", MODEL, [0.0, 0.0])
    await repo.upsert("real", MODEL, [1.0, 0.0])
    # Zero stored vector never clears a positive cutoff ...
    hits = await repo.search([1.0, 0.0], model=MODEL, top_k=5, min_similarity=0.75)
    assert [h.trip_id for h in hits] == ["real"]
    # ... a zero query matches nothing at a positive cutoff ...
    assert await repo.search([0.0, 0.0], model=MODEL, top_k=5, min_similarity=0.75) == []
    # ... and sits at similarity exactly 0 (not NaN) when the cutoff allows it.
    everything = await repo.search([0.0, 0.0], model=MODEL, top_k=5, min_similarity=0.0)
    assert {h.trip_id: h.similarity for h in everything} == {"zero": 0.0, "real": 0.0}


async def test_mismatched_dimensions_raise() -> None:
    repo = InMemoryEmbeddingRepository()
    await repo.upsert("t", MODEL, [1.0, 0.0, 0.0])
    with pytest.raises(EmbeddingDimensionError):
        await repo.search([1.0, 0.0], model=MODEL, top_k=5, min_similarity=0.0)


def test_cosine_similarity_basics() -> None:
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine_similarity([0, 0], [1, 0]) == 0.0
    assert not math.isnan(cosine_similarity([0, 0], [0, 0]))
    assert -1.0 <= cosine_similarity([0.1] * 7, [0.1] * 7) <= 1.0  # clamped against rounding


# ---- preferences ---------------------------------------------------------------------------------
async def test_preference_upsert_get_overwrites_and_copies() -> None:
    repo = InMemoryPreferenceRepository()
    assert await repo.get("u") is None
    profile = PreferenceProfile(
        hotel_style="ryokan", liked_categories=["temple"], budget_hint=Money(amount=5, currency="USD")
    )
    await repo.upsert("u", profile)
    profile.liked_categories.append("mutated after upsert")

    fetched = await repo.get("u")
    assert fetched is not None and fetched.liked_categories == ["temple"]
    fetched.liked_categories.append("mutated after get")
    assert (await repo.get("u")).liked_categories == ["temple"]  # type: ignore[union-attr]

    await repo.upsert("u", PreferenceProfile(hotel_style="hostel"))
    replaced = await repo.get("u")
    assert replaced is not None and replaced.hotel_style == "hostel" and replaced.liked_categories == []
