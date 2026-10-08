"""Repository interfaces for the long-term store.

Team decision: PostgreSQL + pgvector, with Azure Cosmos DB as a provisional alternative; storage
sits behind these protocols so the backend can be swapped without touching agent code. Agents
never import a concrete repository.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from backend.schemas.memory import PreferenceProfile, SavedTripRecord, SimilarityHit


class TripRepository(Protocol):
    async def add(self, record: SavedTripRecord) -> None: ...

    async def get(self, trip_id: str) -> SavedTripRecord | None: ...

    async def get_many(self, trip_ids: Sequence[str]) -> list[SavedTripRecord]:
        """Records for the ids that exist, in the order of `trip_ids`."""
        ...


class EmbeddingRepository(Protocol):
    async def upsert(self, trip_id: str, model: str, vector: Sequence[float]) -> None: ...

    async def search(
        self, vector: Sequence[float], *, model: str, top_k: int, min_similarity: float
    ) -> list[SimilarityHit]:
        """Cosine similarity search restricted to vectors from `model`. Only hits with
        similarity >= min_similarity are returned (the similarity cutoff), best first."""
        ...


class PreferenceRepository(Protocol):
    async def upsert(self, profile_id: str, profile: PreferenceProfile) -> None: ...

    async def get(self, profile_id: str) -> PreferenceProfile | None: ...


@dataclass(frozen=True)
class Repositories:
    trips: TripRepository
    embeddings: EmbeddingRepository
    preferences: PreferenceRepository
    close: Callable[[], Awaitable[None]]
