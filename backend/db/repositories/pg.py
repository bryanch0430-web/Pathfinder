"""PostgreSQL + pgvector repositories (async SQLAlchemy / asyncpg).

Similarity search runs INSIDE the database so the HNSW index can serve it and only `top_k`
rows ever cross the wire:

    SELECT trip_id, 1 - (embedding <=> :vec) AS similarity
    FROM trip_embeddings
    WHERE model = :model AND 1 - (embedding <=> :vec) >= :min_similarity
    ORDER BY embedding <=> :vec, trip_id
    LIMIT :top_k

``<=>`` is pgvector's cosine distance, so ``1 - distance`` is cosine similarity and the
cutoff predicate is the proposal's "retrieved only when it is above the similarity cutoff".

Zero vectors: pgvector returns NaN for the cosine distance against a zero vector, and
PostgreSQL orders NaN *above* every number, so a naive ``1 - NaN >= cutoff`` would be TRUE and
a zero vector would match everything. The query below maps NaN to distance 1 (similarity 0),
which is what the in-memory repository does too.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, func, literal, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import EMBEDDING_DIM, PreferenceProfileRow, SavedTripRow, TripEmbeddingRow
from backend.db.repositories.errors import DuplicateTripError, EmbeddingDimensionError
from backend.schemas.memory import PreferenceProfile, SavedTripRecord, SimilarityHit
from backend.schemas.trip_plan import TripPlan

SessionFactory = async_sessionmaker[AsyncSession]


def build_similarity_query(
    vector: Sequence[float], *, model: str, top_k: int, min_similarity: float
) -> Select[tuple[str, float]]:
    """The cosine-similarity SELECT (exposed so tests can compile it without a database)."""
    query_vector = [float(x) for x in vector]
    distance = TripEmbeddingRow.embedding.cosine_distance(query_vector)
    # NULLIF(distance, 'NaN') is NULL exactly when the distance is NaN (PostgreSQL treats
    # NaN = NaN as true); COALESCE then maps that to distance 1.0, i.e. similarity 0.
    safe_distance = func.coalesce(
        func.nullif(distance, literal_column("'NaN'::float8")), literal(1.0)
    )
    similarity = (1 - safe_distance).label("similarity")
    return (
        select(TripEmbeddingRow.trip_id, similarity)
        .where(TripEmbeddingRow.model == model)
        .where((1 - safe_distance) >= min_similarity)
        .order_by(distance.asc(), TripEmbeddingRow.trip_id.asc())
        .limit(top_k)
    )


def _row_to_record(row: SavedTripRow) -> SavedTripRecord:
    return SavedTripRecord(
        trip_id=row.trip_id,
        destination=row.destination,
        rating=row.rating,
        summary=row.summary,
        plan=TripPlan.model_validate(row.plan),
        created_at=row.created_at,
    )


def _check_dim(vector: Sequence[float]) -> list[float]:
    if len(vector) != EMBEDDING_DIM:
        raise EmbeddingDimensionError(
            f"expected a {EMBEDDING_DIM}-dimensional vector, got {len(vector)}"
        )
    return [float(x) for x in vector]


class PgTripRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def add(self, record: SavedTripRecord) -> None:
        statement = (
            pg_insert(SavedTripRow)
            .values(
                trip_id=record.trip_id,
                destination=record.destination,
                rating=record.rating,
                summary=record.summary,
                plan=record.plan.model_dump(mode="json"),
                created_at=record.created_at,
            )
            .on_conflict_do_nothing(index_elements=[SavedTripRow.trip_id])
            .returning(SavedTripRow.trip_id)
        )
        async with self._session_factory() as session, session.begin():
            inserted = (await session.execute(statement)).scalar_one_or_none()
        if inserted is None:
            raise DuplicateTripError(record.trip_id)

    async def get(self, trip_id: str) -> SavedTripRecord | None:
        async with self._session_factory() as session:
            row = await session.get(SavedTripRow, trip_id)
            return _row_to_record(row) if row is not None else None

    async def get_many(self, trip_ids: Sequence[str]) -> list[SavedTripRecord]:
        if not trip_ids:
            return []
        async with self._session_factory() as session:
            result = await session.execute(
                select(SavedTripRow).where(SavedTripRow.trip_id.in_(list(trip_ids)))
            )
            by_id = {row.trip_id: _row_to_record(row) for row in result.scalars()}
        return [by_id[trip_id] for trip_id in trip_ids if trip_id in by_id]


class PgEmbeddingRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def upsert(self, trip_id: str, model: str, vector: Sequence[float]) -> None:
        values = _check_dim(vector)
        statement = pg_insert(TripEmbeddingRow).values(
            trip_id=trip_id, model=model, embedding=values
        )
        statement = statement.on_conflict_do_update(
            index_elements=[TripEmbeddingRow.trip_id, TripEmbeddingRow.model],
            set_={"embedding": statement.excluded.embedding, "created_at": func.now()},
        )
        async with self._session_factory() as session, session.begin():
            await session.execute(statement)

    async def search(
        self, vector: Sequence[float], *, model: str, top_k: int, min_similarity: float
    ) -> list[SimilarityHit]:
        if top_k <= 0:
            return []
        values = _check_dim(vector)
        statement = build_similarity_query(
            values, model=model, top_k=top_k, min_similarity=min_similarity
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        # pgvector stores float32, so similarity can land a hair outside [-1, 1].
        return [
            SimilarityHit(
                trip_id=row.trip_id, similarity=max(-1.0, min(1.0, float(row.similarity)))
            )
            for row in rows
        ]


class PgPreferenceRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def upsert(self, profile_id: str, profile: PreferenceProfile) -> None:
        payload = profile.model_dump(mode="json")
        statement = pg_insert(PreferenceProfileRow).values(profile_id=profile_id, profile=payload)
        statement = statement.on_conflict_do_update(
            index_elements=[PreferenceProfileRow.profile_id],
            set_={"profile": statement.excluded.profile, "updated_at": func.now()},
        )
        async with self._session_factory() as session, session.begin():
            await session.execute(statement)

    async def get(self, profile_id: str) -> PreferenceProfile | None:
        async with self._session_factory() as session:
            row = await session.get(PreferenceProfileRow, profile_id)
            return PreferenceProfile.model_validate(row.profile) if row is not None else None
