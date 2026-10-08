"""Live PostgreSQL + pgvector tests.

SKIPPED unless PATHFINDER_TEST_DATABASE_URL points at a scratch database whose server has the
pgvector extension available, e.g.

    PATHFINDER_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/pf_test \
        uv run pytest backend/tests/db/test_pg_live.py

The fixture creates the three tables (after CREATE EXTENSION IF NOT EXISTS vector) and drops
them again afterwards. If any of those tables already exists the tests skip rather than touch
data that might be real.
"""

from __future__ import annotations

import math
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from backend.db.models import EMBEDDING_DIM, Base
from backend.db.repositories.base import Repositories
from backend.db.repositories.errors import DuplicateTripError
from backend.db.repositories.pg import (
    PgEmbeddingRepository,
    PgPreferenceRepository,
    PgTripRepository,
)
from backend.db.session import create_engine_and_sessionmaker
from backend.memory.background import BackgroundWriter, SaveUsefulPlanJob
from backend.memory.embeddings import HashingEmbeddingClient
from backend.memory.reranker import SimilarityReranker
from backend.memory.retrieval import SavedTripRetriever, plan_summary_text
from backend.schemas.memory import PreferenceProfile, SavedTripRecord
from backend.tests.memory.helpers import default_settings, make_context, make_plan

DATABASE_URL = os.environ.get("PATHFINDER_TEST_DATABASE_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DATABASE_URL, reason="PATHFINDER_TEST_DATABASE_URL is not set"),
]

MODEL = "live-test-model"


@dataclass
class Live:
    trips: PgTripRepository
    embeddings: PgEmbeddingRepository
    preferences: PgPreferenceRepository
    engine: object

    def repositories(self) -> Repositories:
        async def _close() -> None:
            return None

        return Repositories(
            trips=self.trips, embeddings=self.embeddings, preferences=self.preferences, close=_close
        )


@pytest.fixture
async def live() -> AsyncIterator[Live]:
    engine, session_factory = create_engine_and_sessionmaker(DATABASE_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            existing = await connection.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
                & set(Base.metadata.tables)
            )
        if existing:
            pytest.skip(f"refusing to touch existing tables: {sorted(existing)}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            yield Live(
                trips=PgTripRepository(session_factory),
                embeddings=PgEmbeddingRepository(session_factory),
                preferences=PgPreferenceRepository(session_factory),
                engine=engine,
            )
        finally:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()


def vector_at(cosine: float) -> list[float]:
    """A unit vector whose cosine similarity with `vector_at(1.0)` (the first axis) is `cosine`."""
    v = [0.0] * EMBEDDING_DIM
    v[0] = cosine
    v[1] = math.sqrt(max(0.0, 1.0 - cosine * cosine))
    return v


def record(trip_id: str, destination: str = "Kyoto", rating: int = 4) -> SavedTripRecord:
    return SavedTripRecord(
        trip_id=trip_id,
        destination=destination,
        rating=rating,
        summary=f"summary {trip_id}",
        plan=make_plan(destination, plan_id=f"plan-{trip_id}"),
    )


async def test_trip_add_get_get_many_and_duplicate(live: Live) -> None:
    kyoto, paris = record("t-kyoto"), record("t-paris", "Paris", rating=2)
    await live.trips.add(kyoto)
    await live.trips.add(paris)

    fetched = await live.trips.get("t-kyoto")
    assert fetched is not None
    assert fetched.plan == kyoto.plan  # JSONB round trip through TripPlan.model_validate
    assert (fetched.destination, fetched.rating, fetched.summary) == ("Kyoto", 4, "summary t-kyoto")
    assert await live.trips.get("nope") is None

    many = await live.trips.get_many(["t-paris", "missing", "t-kyoto"])
    assert [r.trip_id for r in many] == ["t-paris", "t-kyoto"]

    with pytest.raises(DuplicateTripError):
        await live.trips.add(kyoto)


async def test_rating_outside_1_to_5_is_rejected_by_the_database(live: Live) -> None:
    async with live.engine.begin() as connection:  # type: ignore[attr-defined]
        with pytest.raises(IntegrityError):
            await connection.execute(
                text(
                    "INSERT INTO saved_trips (trip_id, destination, rating, plan) "
                    "VALUES ('bad', 'Kyoto', 9, '{}'::jsonb)"
                )
            )


async def test_similarity_search_applies_cutoff_order_top_k_and_model(live: Live) -> None:
    for trip_id in ("a", "b", "c", "other-model"):
        await live.trips.add(record(trip_id))
    await live.embeddings.upsert("a", MODEL, vector_at(0.95))
    await live.embeddings.upsert("b", MODEL, vector_at(0.85))
    await live.embeddings.upsert("c", MODEL, vector_at(0.60))  # below the cutoff
    await live.embeddings.upsert("other-model", "different", vector_at(1.0))

    query = vector_at(1.0)
    hits = await live.embeddings.search(query, model=MODEL, top_k=10, min_similarity=0.75)
    assert [h.trip_id for h in hits] == ["a", "b"]
    assert hits[0].similarity == pytest.approx(0.95, abs=1e-4)
    assert hits[1].similarity == pytest.approx(0.85, abs=1e-4)

    top1 = await live.embeddings.search(query, model=MODEL, top_k=1, min_similarity=0.75)
    assert [h.trip_id for h in top1] == ["a"]
    loose = await live.embeddings.search(query, model=MODEL, top_k=10, min_similarity=0.5)
    assert [h.trip_id for h in loose] == ["a", "b", "c"]
    assert await live.embeddings.search(query, model=MODEL, top_k=10, min_similarity=0.99) == []


async def test_zero_vectors_never_clear_the_cutoff(live: Live) -> None:
    await live.trips.add(record("zero"))
    await live.trips.add(record("real"))
    await live.embeddings.upsert("zero", MODEL, [0.0] * EMBEDDING_DIM)
    await live.embeddings.upsert("real", MODEL, vector_at(1.0))

    hits = await live.embeddings.search(vector_at(1.0), model=MODEL, top_k=5, min_similarity=0.75)
    assert [h.trip_id for h in hits] == ["real"]
    zero_query = [0.0] * EMBEDDING_DIM
    assert await live.embeddings.search(zero_query, model=MODEL, top_k=5, min_similarity=0.75) == []


async def test_embedding_upsert_replaces_and_trip_delete_cascades(live: Live) -> None:
    await live.trips.add(record("t"))
    await live.embeddings.upsert("t", MODEL, vector_at(0.0))
    assert await live.embeddings.search(vector_at(1.0), model=MODEL, top_k=5, min_similarity=0.75) == []

    await live.embeddings.upsert("t", MODEL, vector_at(1.0))  # same key: replaced, not duplicated
    hits = await live.embeddings.search(vector_at(1.0), model=MODEL, top_k=5, min_similarity=0.75)
    assert [h.trip_id for h in hits] == ["t"]

    async with live.engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(text("DELETE FROM saved_trips WHERE trip_id = 't'"))
    assert await live.embeddings.search(vector_at(1.0), model=MODEL, top_k=5, min_similarity=0.0) == []


async def test_preference_upsert_get_and_overwrite(live: Live) -> None:
    assert await live.preferences.get("u1") is None
    first = PreferenceProfile(hotel_style="ryokan", liked_categories=["temple", "shrine"])
    await live.preferences.upsert("u1", first)
    assert await live.preferences.get("u1") == first

    second = PreferenceProfile(hotel_style="hostel")
    await live.preferences.upsert("u1", second)
    assert await live.preferences.get("u1") == second


async def test_s07_s19_end_to_end_on_postgres(live: Live) -> None:
    """Background writer saves a useful plan; the retriever finds it above the cutoff and a
    Hong Kong request gets nothing."""
    settings = default_settings()
    embedder = HashingEmbeddingClient(dim=settings.embedding_dim)
    repos = live.repositories()
    writer = BackgroundWriter(embedder=embedder, repos=repos)
    plan = make_plan("Kyoto")
    writer.submit(
        SaveUsefulPlanJob(plan=plan, rating=5, profile_id="u1", preferences=PreferenceProfile())
    )
    await writer.drain()
    await writer.stop()
    assert (writer.completed, writer.failed) == (1, 0)

    retriever = SavedTripRetriever(
        embedder=embedder,
        trips=repos.trips,
        embeddings=repos.embeddings,
        reranker=SimilarityReranker(),
        settings=settings,
    )
    found = await retriever.retrieve(make_context("Kyoto"))
    assert len(found) == 1
    assert found[0].similarity >= settings.similarity_cutoff
    assert found[0].summary == plan_summary_text(plan) and found[0].rating == 5
    assert await retriever.retrieve(make_context("Hong Kong")) == []
    profile = await repos.preferences.get("u1")
    assert profile is not None and profile.liked_categories[:3] == ["shrine", "temple", "garden"]
