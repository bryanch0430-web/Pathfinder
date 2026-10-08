"""Azure Cosmos DB repositories.

TODO(provisional): Azure Cosmos DB (team alternative under consideration). The team decision is
PostgreSQL + pgvector behind the repository protocols; these classes exist so the Cosmos option
is a typed, selectable (but unimplemented) backend. Every method raises `NotImplementedError`.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.schemas.memory import PreferenceProfile, SavedTripRecord, SimilarityHit

_MESSAGE = "TODO(provisional): Azure Cosmos DB (team alternative under consideration)"


class CosmosTripRepository:
    async def add(self, record: SavedTripRecord) -> None:
        # TODO(provisional): Azure Cosmos DB (team alternative under consideration)
        raise NotImplementedError(_MESSAGE)

    async def get(self, trip_id: str) -> SavedTripRecord | None:
        # TODO(provisional): Azure Cosmos DB (team alternative under consideration)
        raise NotImplementedError(_MESSAGE)

    async def get_many(self, trip_ids: Sequence[str]) -> list[SavedTripRecord]:
        # TODO(provisional): Azure Cosmos DB (team alternative under consideration)
        raise NotImplementedError(_MESSAGE)


class CosmosEmbeddingRepository:
    async def upsert(self, trip_id: str, model: str, vector: Sequence[float]) -> None:
        # TODO(provisional): Azure Cosmos DB (team alternative under consideration)
        raise NotImplementedError(_MESSAGE)

    async def search(
        self, vector: Sequence[float], *, model: str, top_k: int, min_similarity: float
    ) -> list[SimilarityHit]:
        # TODO(provisional): Azure Cosmos DB (team alternative under consideration)
        raise NotImplementedError(_MESSAGE)


class CosmosPreferenceRepository:
    async def upsert(self, profile_id: str, profile: PreferenceProfile) -> None:
        # TODO(provisional): Azure Cosmos DB (team alternative under consideration)
        raise NotImplementedError(_MESSAGE)

    async def get(self, profile_id: str) -> PreferenceProfile | None:
        # TODO(provisional): Azure Cosmos DB (team alternative under consideration)
        raise NotImplementedError(_MESSAGE)
