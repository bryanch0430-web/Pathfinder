from __future__ import annotations

import pytest

from backend.memory.reranker import JevReranker, SimilarityReranker, build_reranker
from backend.schemas.memory import SimilarTrip
from backend.tests.memory.helpers import default_settings, make_plan


def candidate(trip_id: str, similarity: float, rating: int) -> SimilarTrip:
    plan = make_plan("Kyoto")
    return SimilarTrip(
        trip_id=trip_id,
        destination=plan.destination,
        rating=rating,
        similarity=similarity,
        summary="s",
        plan=plan,
    )


async def test_orders_by_similarity_then_rating_then_trip_id() -> None:
    candidates = [
        candidate("c", 0.80, 3),
        candidate("b", 0.90, 2),
        candidate("a", 0.80, 5),
        candidate("d", 0.80, 5),
        candidate("e", 0.95, 1),
    ]
    ranked = await SimilarityReranker().rerank("q", candidates)
    assert [c.trip_id for c in ranked] == ["e", "b", "a", "d", "c"]


async def test_rerank_returns_a_new_list_and_handles_empty() -> None:
    candidates = [candidate("x", 0.8, 4)]
    ranked = await SimilarityReranker().rerank("q", candidates)
    assert ranked == candidates and ranked is not candidates
    assert await SimilarityReranker().rerank("q", []) == []


def test_build_reranker_by_provider() -> None:
    assert isinstance(build_reranker(default_settings(reranker_provider="mock")), SimilarityReranker)
    with pytest.raises(NotImplementedError, match="Jev"):
        build_reranker(default_settings(reranker_provider="jev"))
    with pytest.raises(NotImplementedError):
        JevReranker(api_key="")
