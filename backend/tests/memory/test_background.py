"""Background writer (scenario 19): a useful plan is saved, embedded and written off-path."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace

import pytest

from backend.db.factory import build_repositories
from backend.db.repositories.base import Repositories
from backend.memory.background import BackgroundWriter, SaveUsefulPlanJob
from backend.memory.embeddings import HashingEmbeddingClient
from backend.memory.retrieval import plan_summary_text
from backend.schemas.memory import PreferenceProfile, SavedTripRecord
from backend.tests.memory.helpers import default_settings, make_plan


def make_repos() -> Repositories:
    return build_repositories(default_settings())


def make_job(rating: int = 5, destination: str = "Kyoto", profile_id: str = "user-1") -> SaveUsefulPlanJob:
    return SaveUsefulPlanJob(
        plan=make_plan(destination),
        rating=rating,
        profile_id=profile_id,
        preferences=PreferenceProfile(hotel_style="ryokan", liked_categories=["museum"]),
    )


async def find_saved(
    repos: Repositories, embedder: HashingEmbeddingClient, destination: str
) -> list[SavedTripRecord]:
    """All stored trips for a plan like `make_plan(destination)`, found via the vector index."""
    [vector] = await embedder.embed([plan_summary_text(make_plan(destination))])
    hits = await repos.embeddings.search(
        vector, model=embedder.model, top_k=50, min_similarity=0.99
    )
    return await repos.trips.get_many([h.trip_id for h in hits])


class GatedTripRepository:
    """`add` blocks until `release` is set: proves submit() does not await the write."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def add(self, record: SavedTripRecord) -> None:
        self.started.set()
        await self.release.wait()
        await self._inner.add(record)  # type: ignore[attr-defined]

    async def get(self, trip_id: str) -> SavedTripRecord | None:
        return await self._inner.get(trip_id)  # type: ignore[attr-defined]

    async def get_many(self, trip_ids: Sequence[str]) -> list[SavedTripRecord]:
        return await self._inner.get_many(trip_ids)  # type: ignore[attr-defined]


class FlakyTripRepository:
    """Fails the first `failures` adds, then delegates."""

    def __init__(self, inner: object, failures: int) -> None:
        self._inner = inner
        self.remaining = failures

    async def add(self, record: SavedTripRecord) -> None:
        if self.remaining > 0:
            self.remaining -= 1
            raise RuntimeError("database is down")
        await self._inner.add(record)  # type: ignore[attr-defined]

    async def get(self, trip_id: str) -> SavedTripRecord | None:
        return await self._inner.get(trip_id)  # type: ignore[attr-defined]

    async def get_many(self, trip_ids: Sequence[str]) -> list[SavedTripRecord]:
        return await self._inner.get_many(trip_ids)  # type: ignore[attr-defined]


async def test_s19_useful_plan_is_saved_embedded_and_preferences_written() -> None:
    repos = make_repos()
    embedder = HashingEmbeddingClient(dim=1536)
    writer = BackgroundWriter(embedder=embedder, repos=repos)
    await writer.start()
    try:
        job = make_job(rating=5)
        writer.submit(job)
        await writer.drain()
    finally:
        await writer.stop()

    assert (writer.completed, writer.failed) == (1, 0)

    # trips repo: a record with destination and rating, summary from the shared builder.
    saved = await find_saved(repos, embedder, "Kyoto")
    assert len(saved) == 1
    record = saved[0]
    assert record.destination == "Kyoto"
    assert record.rating == 5
    assert record.summary == plan_summary_text(job.plan)
    assert record.plan == job.plan
    assert len(record.trip_id) == 32  # uuid4 hex

    # embeddings repo: a vector exists for exactly the embedder's model.
    [vector] = await embedder.embed([record.summary])
    hits = await repos.embeddings.search(vector, model=embedder.model, top_k=5, min_similarity=0.99)
    assert [h.trip_id for h in hits] == [record.trip_id]
    assert await repos.embeddings.search(vector, model="another-model", top_k=5, min_similarity=0.0) == []

    # preferences repo: merged profile (existing values kept, plan categories added first).
    profile = await repos.preferences.get("user-1")
    assert profile is not None
    assert profile.hotel_style == "ryokan"
    assert profile.liked_categories == ["shrine", "temple", "garden", "museum"]


async def test_s19_submit_returns_immediately_and_does_not_await_the_write() -> None:
    repos = make_repos()
    gate = GatedTripRepository(repos.trips)
    writer = BackgroundWriter(embedder=HashingEmbeddingClient(dim=1536), repos=replace(repos, trips=gate))
    await writer.start()

    writer.submit(make_job())  # a plain (non-async) call: nothing to await

    await asyncio.wait_for(gate.started.wait(), timeout=1)  # the worker is mid-write ...
    assert writer.completed == 0  # ... and submit() has long since returned
    assert await find_saved(repos, HashingEmbeddingClient(dim=1536), "Kyoto") == []

    gate.release.set()
    await writer.drain()
    assert writer.completed == 1
    assert len(await find_saved(repos, HashingEmbeddingClient(dim=1536), "Kyoto")) == 1
    await writer.stop()


async def test_s19_a_failing_repository_is_counted_not_raised_and_the_worker_survives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repos = make_repos()
    embedder = HashingEmbeddingClient(dim=1536)
    flaky = FlakyTripRepository(repos.trips, failures=1)
    writer = BackgroundWriter(embedder=embedder, repos=replace(repos, trips=flaky))
    await writer.start()

    with caplog.at_level(logging.ERROR, logger="backend.memory.background"):
        writer.submit(make_job(destination="Kyoto"))  # fails: must not raise
        await writer.drain()
        assert (writer.completed, writer.failed) == (0, 1)

        writer.submit(make_job(destination="Paris"))  # the same worker still processes jobs
        await writer.drain()

    assert (writer.completed, writer.failed) == (1, 1)
    assert "background save failed: RuntimeError" in caplog.text
    assert await find_saved(repos, embedder, "Kyoto") == []
    assert len(await find_saved(repos, embedder, "Paris")) == 1
    await writer.stop()


async def test_embedding_failure_leaves_no_half_saved_trip() -> None:
    repos = make_repos()

    class BrokenEmbedder:
        model = "broken"
        dim = 1536

        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise TimeoutError("embedding provider timed out")

    writer = BackgroundWriter(embedder=BrokenEmbedder(), repos=repos)
    writer.submit(make_job())
    await writer.drain()
    assert (writer.completed, writer.failed) == (0, 1)
    assert await repos.preferences.get("user-1") is None
    assert await find_saved(repos, HashingEmbeddingClient(dim=1536), "Kyoto") == []
    await writer.stop()


async def test_jobs_submitted_before_start_are_processed_once_started() -> None:
    repos = make_repos()
    embedder = HashingEmbeddingClient(dim=1536)
    writer = BackgroundWriter(embedder=embedder, repos=repos)
    writer.submit(make_job(destination="Kyoto"))
    writer.submit(make_job(destination="Paris"))
    await writer.start()
    await writer.drain()
    assert writer.completed == 2
    await writer.stop()


async def test_stop_drains_pending_jobs_then_stops_the_worker() -> None:
    repos = make_repos()
    embedder = HashingEmbeddingClient(dim=1536)
    writer = BackgroundWriter(embedder=embedder, repos=repos)
    await writer.start()
    for destination in ("Kyoto", "Paris", "Hong Kong"):
        writer.submit(make_job(destination=destination))
    await writer.stop()  # nothing awaited in between
    assert writer.completed == 3
    for destination in ("Kyoto", "Paris", "Hong Kong"):
        assert len(await find_saved(repos, embedder, destination)) == 1
    # idempotent
    await writer.stop()
    # and restartable
    writer.submit(make_job(destination="Kyoto", profile_id="user-2"))
    await writer.stop()
    assert writer.completed == 4


async def test_drain_on_an_idle_writer_returns_immediately() -> None:
    writer = BackgroundWriter(embedder=HashingEmbeddingClient(dim=64), repos=make_repos())
    await asyncio.wait_for(writer.drain(), timeout=1)
    assert (writer.completed, writer.failed) == (0, 0)
