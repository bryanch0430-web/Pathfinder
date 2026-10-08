"""In-memory repositories: tests and no-database local runs (`storage_backend="memory"`).

Everything here is plain dict bookkeeping with no `await` between a read and the write that
depends on it, so on a single event loop each method is atomic without locks (the project rule
is asyncio primitives only, no threads). Semantics mirror the PostgreSQL implementation:

* similarity is cosine similarity; a zero vector on either side has similarity 0.0;
* a hit is returned only if ``similarity >= min_similarity`` (the similarity cutoff);
* results are ordered best-first, ties broken by ``trip_id`` so the order is deterministic.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from backend.db.repositories.errors import DuplicateTripError, EmbeddingDimensionError
from backend.schemas.memory import PreferenceProfile, SavedTripRecord, SimilarityHit


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 when either vector has zero length (undefined angle)."""
    if len(a) != len(b):
        raise EmbeddingDimensionError(f"vector length mismatch: {len(a)} != {len(b)}")
    dot = math.fsum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(math.fsum(x * x for x in a))
    norm_b = math.sqrt(math.fsum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    # Clamp: float rounding can land a hair outside [-1, 1] and SimilarityHit validates range.
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


class InMemoryTripRepository:
    def __init__(self) -> None:
        self._records: dict[str, SavedTripRecord] = {}

    async def add(self, record: SavedTripRecord) -> None:
        if record.trip_id in self._records:
            raise DuplicateTripError(record.trip_id)
        self._records[record.trip_id] = record.model_copy(deep=True)

    async def get(self, trip_id: str) -> SavedTripRecord | None:
        record = self._records.get(trip_id)
        return record.model_copy(deep=True) if record is not None else None

    async def get_many(self, trip_ids: Sequence[str]) -> list[SavedTripRecord]:
        return [
            self._records[trip_id].model_copy(deep=True)
            for trip_id in trip_ids
            if trip_id in self._records
        ]


class InMemoryEmbeddingRepository:
    def __init__(self) -> None:
        self._vectors: dict[tuple[str, str], tuple[float, ...]] = {}

    async def upsert(self, trip_id: str, model: str, vector: Sequence[float]) -> None:
        self._vectors[(trip_id, model)] = tuple(float(x) for x in vector)

    async def search(
        self, vector: Sequence[float], *, model: str, top_k: int, min_similarity: float
    ) -> list[SimilarityHit]:
        if top_k <= 0:
            return []
        hits = [
            SimilarityHit(trip_id=trip_id, similarity=sim)
            for (trip_id, stored_model), stored in self._vectors.items()
            if stored_model == model
            and (sim := cosine_similarity(vector, stored)) >= min_similarity
        ]
        hits.sort(key=lambda h: (-h.similarity, h.trip_id))
        return hits[:top_k]


class InMemoryPreferenceRepository:
    def __init__(self) -> None:
        self._profiles: dict[str, PreferenceProfile] = {}

    async def upsert(self, profile_id: str, profile: PreferenceProfile) -> None:
        self._profiles[profile_id] = profile.model_copy(deep=True)

    async def get(self, profile_id: str) -> PreferenceProfile | None:
        profile = self._profiles.get(profile_id)
        return profile.model_copy(deep=True) if profile is not None else None
