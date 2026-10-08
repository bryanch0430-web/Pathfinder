"""Backend selection, engine construction and the Alembic migration (offline SQL only)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from backend.db.factory import build_repositories
from backend.db.repositories.cosmos import (
    CosmosEmbeddingRepository,
    CosmosPreferenceRepository,
    CosmosTripRepository,
)
from backend.db.repositories.memory import (
    InMemoryEmbeddingRepository,
    InMemoryPreferenceRepository,
    InMemoryTripRepository,
)
from backend.db.repositories.pg import (
    PgEmbeddingRepository,
    PgPreferenceRepository,
    PgTripRepository,
)
from backend.db.session import _with_asyncpg_driver, create_engine_and_sessionmaker
from backend.schemas.memory import PreferenceProfile
from backend.tests.memory.helpers import default_settings, make_plan
from backend.schemas.memory import SavedTripRecord

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "db" / "alembic.ini"


# ---- factory -------------------------------------------------------------------------------------
async def test_memory_backend_builds_in_memory_repositories_and_close_is_a_noop() -> None:
    repos = build_repositories(default_settings(storage_backend="memory"))
    assert isinstance(repos.trips, InMemoryTripRepository)
    assert isinstance(repos.embeddings, InMemoryEmbeddingRepository)
    assert isinstance(repos.preferences, InMemoryPreferenceRepository)
    await repos.close()
    await repos.close()  # idempotent

    # Each call gives an isolated store.
    other = build_repositories(default_settings(storage_backend="memory"))
    await repos.preferences.upsert("u", PreferenceProfile(hotel_style="x"))
    assert await other.preferences.get("u") is None


async def test_postgres_backend_builds_repositories_without_connecting_and_close_disposes() -> None:
    repos = build_repositories(
        default_settings(
            storage_backend="postgres",
            database_url="postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none",
        )
    )
    assert isinstance(repos.trips, PgTripRepository)
    assert isinstance(repos.embeddings, PgEmbeddingRepository)
    assert isinstance(repos.preferences, PgPreferenceRepository)
    await repos.close()  # engine.dispose() on a never-used engine: no connection attempted


def test_cosmos_backend_is_provisional() -> None:
    with pytest.raises(NotImplementedError, match="TODO\\(provisional\\)"):
        build_repositories(default_settings(storage_backend="cosmos"))


async def test_cosmos_repositories_raise_not_implemented_on_every_method() -> None:
    plan = make_plan("Kyoto")
    record = SavedTripRecord(trip_id="t", destination="Kyoto", rating=3, summary="s", plan=plan)
    trips, embeddings, prefs = (
        CosmosTripRepository(),
        CosmosEmbeddingRepository(),
        CosmosPreferenceRepository(),
    )
    for call in (
        trips.add(record),
        trips.get("t"),
        trips.get_many(["t"]),
        embeddings.upsert("t", "m", [0.1]),
        embeddings.search([0.1], model="m", top_k=1, min_similarity=0.5),
        prefs.upsert("u", PreferenceProfile()),
        prefs.get("u"),
    ):
        with pytest.raises(NotImplementedError, match="Cosmos"):
            await call


# ---- engine / session ----------------------------------------------------------------------------
def test_create_engine_and_sessionmaker_uses_asyncpg_and_does_not_connect() -> None:
    engine, session_factory = create_engine_and_sessionmaker(
        "postgresql://u:p@127.0.0.1:1/db"  # plain URL gets the async driver pinned
    )
    assert isinstance(engine, AsyncEngine)
    assert isinstance(session_factory, async_sessionmaker)
    assert engine.url.drivername == "postgresql+asyncpg"


def test_driver_rewrite_leaves_explicit_drivers_alone() -> None:
    assert _with_asyncpg_driver("postgres://h/db") == "postgresql+asyncpg://h/db"
    assert _with_asyncpg_driver("postgresql+asyncpg://h/db") == "postgresql+asyncpg://h/db"


# ---- alembic (offline: no database needed) -------------------------------------------------------
def offline_sql(*, direction: str) -> str:
    config = Config(str(ALEMBIC_INI))
    config.attributes["configure_logger"] = False
    config.output_buffer = io.StringIO()
    if direction == "up":
        command.upgrade(config, "head", sql=True)
    else:
        command.downgrade(config, "head:base", sql=True)
    return config.output_buffer.getvalue()


def test_upgrade_sql_creates_extension_tables_and_hnsw_index() -> None:
    sql = offline_sql(direction="up")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    for table in ("saved_trips", "trip_embeddings", "preference_profiles"):
        assert f"CREATE TABLE {table}" in sql
    assert "CREATE INDEX ix_saved_trips_destination ON saved_trips (destination)" in sql
    assert (
        "CREATE INDEX ix_trip_embeddings_embedding_hnsw ON trip_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    ) in sql
    assert "embedding VECTOR(1536) NOT NULL" in sql
    assert "CHECK (rating BETWEEN 1 AND 5)" in sql
    assert "FOREIGN KEY(trip_id) REFERENCES saved_trips (trip_id) ON DELETE CASCADE" in sql
    assert "PRIMARY KEY (trip_id, model)" in sql
    # the extension must exist before the first table that uses its type
    assert sql.index("CREATE EXTENSION") < sql.index("CREATE TABLE trip_embeddings")


def test_downgrade_sql_drops_tables_but_not_the_extension() -> None:
    sql = offline_sql(direction="down")
    for table in ("saved_trips", "trip_embeddings", "preference_profiles"):
        assert f"DROP TABLE {table}" in sql
    assert "DROP INDEX IF EXISTS ix_trip_embeddings_embedding_hnsw" in sql
    assert "DROP EXTENSION" not in sql
    # children before parents: the FK table goes first
    assert sql.index("DROP TABLE trip_embeddings") < sql.index("DROP TABLE saved_trips")
