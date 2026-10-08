"""Reranker over the retrieved short list.

Appendix B: "Retrieved candidates will be reordered by a score over that short list,
provisionally also from Jev, rather than by asking a language model to rank free text."
TODO(provisional): Jev reranker; the mock sorts by similarity then rating.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from backend.schemas.memory import SimilarTrip
from backend.settings import Settings


class Reranker(Protocol):
    async def rerank(self, query: str, candidates: Sequence[SimilarTrip]) -> list[SimilarTrip]: ...


class SimilarityReranker:
    """Offline mock: similarity (desc), then the user's rating (desc), then trip_id so the order
    is total and deterministic. A score over the short list, never a language-model ranking."""

    async def rerank(self, query: str, candidates: Sequence[SimilarTrip]) -> list[SimilarTrip]:
        return sorted(candidates, key=lambda c: (-c.similarity, -c.rating, c.trip_id))


class JevReranker:
    """TODO(provisional): Jev reranker (Appendix B). Raises `NotImplementedError` on construction
    so a misconfigured deployment fails at startup, not on the first user request."""

    def __init__(self, *, api_key: str) -> None:
        # TODO(provisional): Jev reranker
        raise NotImplementedError("TODO(provisional): Jev reranker")

    async def rerank(self, query: str, candidates: Sequence[SimilarTrip]) -> list[SimilarTrip]:
        # TODO(provisional): Jev reranker
        raise NotImplementedError("TODO(provisional): Jev reranker")


def build_reranker(settings: Settings) -> Reranker:
    if settings.reranker_provider == "mock":
        return SimilarityReranker()
    if settings.reranker_provider == "jev":
        # TODO(provisional): Jev reranker
        return JevReranker(api_key=settings.jev_api_key.get_secret_value())
    raise ValueError(f"unknown reranker_provider: {settings.reranker_provider!r}")
