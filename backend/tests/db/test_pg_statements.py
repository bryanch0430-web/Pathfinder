"""PostgreSQL layer without a database: SQL generation, schema metadata and repository logic
driven through a fake async session. (Live tests are in test_pg_live.py.)"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg
from sqlalchemy.schema import CreateIndex

from backend.db.models import (
    EMBEDDING_DIM,
    HNSW_INDEX_NAME,
    Base,
    PreferenceProfileRow,
    SavedTripRow,
    TripEmbeddingRow,
)
from backend.db.repositories.errors import DuplicateTripError, EmbeddingDimensionError
from backend.db.repositories.pg import (
    PgEmbeddingRepository,
    PgPreferenceRepository,
    PgTripRepository,
    build_similarity_query,
)
from backend.schemas.memory import PreferenceProfile, SavedTripRecord
from backend.schemas.trip_plan import TripPlan
from backend.tests.memory.helpers import make_plan


# ---- the similarity SELECT -----------------------------------------------------------------------
def compiled_similarity_sql(min_similarity: float = 0.75, *, literal: bool = False) -> str:
    statement = build_similarity_query(
        [0.25] * EMBEDDING_DIM, model="mock-hashing-v1", top_k=3, min_similarity=min_similarity
    )
    kwargs = {"literal_binds": True} if literal else {}
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs=kwargs))


def test_similarity_query_uses_the_cosine_operator_in_sql() -> None:
    sql = compiled_similarity_sql()
    assert "<=>" in sql  # pgvector cosine distance, computed by the database
    assert "trip_embeddings.embedding <=>" in sql
    assert "ORDER BY (trip_embeddings.embedding <=>" in sql  # index-friendly ordering
    assert "LIMIT" in sql


def test_similarity_query_filters_on_model_and_the_similarity_cutoff() -> None:
    sql = compiled_similarity_sql(0.75, literal=True)
    assert "trip_embeddings.model = 'mock-hashing-v1'" in sql
    # similarity = 1 - cosine_distance, compared with the cutoff in the WHERE clause
    where = sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "1 - " in where and ">= 0.75" in where
    assert "LIMIT 3" in sql


def test_similarity_query_maps_nan_distances_to_similarity_zero() -> None:
    """A zero vector gives NaN in pgvector, which PostgreSQL sorts above every number; it must
    not satisfy `1 - distance >= cutoff`."""
    sql = compiled_similarity_sql(literal=True)
    assert "nullif(trip_embeddings.embedding <=>" in sql and "'NaN'::float8" in sql
    assert "coalesce(" in sql


def test_similarity_query_also_compiles_for_the_asyncpg_driver() -> None:
    statement = build_similarity_query([0.5] * EMBEDDING_DIM, model="m", top_k=2, min_similarity=0.5)
    sql = str(statement.compile(dialect=pg_asyncpg.dialect()))
    assert "<=>" in sql and "$1" in sql


# ---- schema metadata -----------------------------------------------------------------------------
def test_metadata_matches_the_specified_schema() -> None:
    tables = Base.metadata.tables
    assert set(tables) == {"saved_trips", "trip_embeddings", "preference_profiles"}

    trips = tables["saved_trips"]
    assert trips.c.trip_id.primary_key
    assert not trips.c.destination.nullable and not trips.c.rating.nullable
    assert not trips.c.plan.nullable
    assert any(ix.columns.keys() == ["destination"] for ix in trips.indexes)
    checks = [c for c in trips.constraints if c.__class__.__name__ == "CheckConstraint"]
    assert any("rating BETWEEN 1 AND 5" in str(c.sqltext) for c in checks)

    emb = tables["trip_embeddings"]
    assert [c.name for c in emb.primary_key.columns] == ["trip_id", "model"]
    (fk,) = emb.foreign_keys
    assert fk.column.table.name == "saved_trips" and fk.ondelete == "CASCADE"
    assert emb.c.embedding.type.dim == EMBEDDING_DIM == 1536

    assert tables["preference_profiles"].c.profile_id.primary_key


def test_hnsw_cosine_index_is_declared_on_the_embedding_column() -> None:
    index = next(ix for ix in TripEmbeddingRow.__table__.indexes if ix.name == HNSW_INDEX_NAME)
    sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "USING hnsw (embedding vector_cosine_ops)" in sql


# ---- repositories through a fake session ---------------------------------------------------------
class FakeResult:
    def __init__(self, *, rows: Sequence[object] = (), scalar: object = None) -> None:
        self._rows = list(rows)
        self._scalar = scalar

    def scalar_one_or_none(self) -> object:
        return self._scalar

    def scalars(self) -> list[object]:
        return self._rows

    def all(self) -> list[object]:
        return self._rows


class _NoopContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeSession:
    def __init__(self, factory: FakeSessionFactory) -> None:
        self._factory = factory

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> _NoopContext:
        return _NoopContext()

    async def execute(self, statement: object) -> FakeResult:
        self._factory.executed.append(statement)
        return self._factory.results.popleft()

    async def get(self, model: type, key: str) -> object | None:
        return self._factory.rows.get((model, key))


class FakeSessionFactory:
    def __init__(self) -> None:
        self.executed: list[object] = []
        self.results: deque[FakeResult] = deque()
        self.rows: dict[tuple[type, str], object] = {}

    def __call__(self) -> FakeSession:
        return FakeSession(self)

    def compiled(self, index: int = -1) -> object:
        return self.executed[index].compile(dialect=pg_asyncpg.dialect())  # type: ignore[attr-defined]


def trip_row(
    trip_id: str, destination: str = "Kyoto", rating: int = 5, *, plan: TripPlan | None = None
) -> SavedTripRow:
    plan = plan or make_plan(destination)
    return SavedTripRow(
        trip_id=trip_id,
        destination=destination,
        rating=rating,
        summary=f"summary {trip_id}",
        plan=plan.model_dump(mode="json"),
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


async def test_trip_add_issues_an_insert_that_stores_the_plan_as_json() -> None:
    factory = FakeSessionFactory()
    factory.results.append(FakeResult(scalar="t1"))
    repo = PgTripRepository(factory)  # type: ignore[arg-type]
    plan = make_plan("Kyoto")
    await repo.add(
        SavedTripRecord(trip_id="t1", destination="Kyoto", rating=4, summary="s", plan=plan)
    )

    compiled = factory.compiled()
    sql = str(compiled)
    assert sql.startswith("INSERT INTO saved_trips") and "ON CONFLICT (trip_id) DO NOTHING" in sql
    params = compiled.params  # type: ignore[attr-defined]
    assert params["destination"] == "Kyoto" and params["rating"] == 4
    assert json.loads(json.dumps(params["plan"])) == plan.model_dump(mode="json")  # pure JSON


async def test_trip_add_raises_duplicate_when_the_row_already_exists() -> None:
    factory = FakeSessionFactory()
    factory.results.append(FakeResult(scalar=None))  # ON CONFLICT DO NOTHING returned no row
    repo = PgTripRepository(factory)  # type: ignore[arg-type]
    with pytest.raises(DuplicateTripError):
        await repo.add(
            SavedTripRecord(
                trip_id="t1", destination="Kyoto", rating=4, summary="s", plan=make_plan("Kyoto")
            )
        )


async def test_trip_get_validates_the_stored_json_back_into_a_trip_plan() -> None:
    factory = FakeSessionFactory()
    plan = make_plan("Kyoto")
    factory.rows[(SavedTripRow, "t1")] = trip_row("t1", plan=plan)
    repo = PgTripRepository(factory)  # type: ignore[arg-type]
    record = await repo.get("t1")
    assert record is not None
    assert record.plan == plan  # JSON -> TripPlan.model_validate round-trips losslessly
    assert (record.destination, record.rating, record.summary) == ("Kyoto", 5, "summary t1")
    assert await repo.get("missing") is None


async def test_trip_get_many_preserves_the_requested_order() -> None:
    factory = FakeSessionFactory()
    # The database may return rows in any order.
    factory.results.append(FakeResult(rows=[trip_row("a"), trip_row("c")]))
    repo = PgTripRepository(factory)  # type: ignore[arg-type]
    found = await repo.get_many(["c", "gone", "a"])
    assert [r.trip_id for r in found] == ["c", "a"]
    assert "IN" in str(factory.compiled()) and "saved_trips" in str(factory.compiled())
    assert await repo.get_many([]) == []
    assert len(factory.executed) == 1  # the empty call never touched the database


async def test_embedding_upsert_is_an_on_conflict_update_keyed_by_trip_and_model() -> None:
    factory = FakeSessionFactory()
    factory.results.append(FakeResult())
    repo = PgEmbeddingRepository(factory)  # type: ignore[arg-type]
    await repo.upsert("t1", "m", [0.5] * EMBEDDING_DIM)
    sql = str(factory.compiled())
    assert sql.startswith("INSERT INTO trip_embeddings")
    assert "ON CONFLICT (trip_id, model) DO UPDATE SET embedding = excluded.embedding" in sql


async def test_embedding_dimension_is_checked_before_touching_the_database() -> None:
    factory = FakeSessionFactory()
    repo = PgEmbeddingRepository(factory)  # type: ignore[arg-type]
    with pytest.raises(EmbeddingDimensionError):
        await repo.upsert("t1", "m", [0.1, 0.2])
    with pytest.raises(EmbeddingDimensionError):
        await repo.search([0.1, 0.2], model="m", top_k=3, min_similarity=0.5)
    assert factory.executed == []


async def test_embedding_search_maps_rows_to_hits_clamping_float32_overshoot() -> None:
    factory = FakeSessionFactory()
    factory.results.append(
        FakeResult(
            rows=[
                SimpleNamespace(trip_id="a", similarity=1.0000001),
                SimpleNamespace(trip_id="b", similarity=0.8),
            ]
        )
    )
    repo = PgEmbeddingRepository(factory)  # type: ignore[arg-type]
    hits = await repo.search([0.1] * EMBEDDING_DIM, model="m", top_k=3, min_similarity=0.75)
    assert [(h.trip_id, h.similarity) for h in hits] == [("a", 1.0), ("b", 0.8)]
    assert "<=>" in str(factory.compiled())
    assert await repo.search([0.1] * EMBEDDING_DIM, model="m", top_k=0, min_similarity=0.0) == []


async def test_preference_upsert_and_get() -> None:
    factory = FakeSessionFactory()
    factory.results.append(FakeResult())
    repo = PgPreferenceRepository(factory)  # type: ignore[arg-type]
    profile = PreferenceProfile(hotel_style="ryokan", liked_categories=["temple"])
    await repo.upsert("u1", profile)
    sql = str(factory.compiled())
    assert sql.startswith("INSERT INTO preference_profiles")
    assert "ON CONFLICT (profile_id) DO UPDATE" in sql
    assert factory.compiled().params["profile"] == profile.model_dump(mode="json")  # type: ignore[attr-defined]

    factory.rows[(PreferenceProfileRow, "u1")] = PreferenceProfileRow(
        profile_id="u1", profile=profile.model_dump(mode="json")
    )
    assert await repo.get("u1") == profile
    assert await repo.get("other") is None
