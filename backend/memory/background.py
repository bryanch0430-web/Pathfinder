"""Background writer to the long-term store.

Proposal: "keeping the users' preferences as session memory and writing them to the long-term
store by a background process." A plan marked useful is saved with destination and rating,
embedded, and written off the request path.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from pydantic import Field

from backend.db.repositories.base import Repositories
from backend.memory.embeddings import EmbeddingClient
from backend.memory.preferences import merge_preferences
from backend.memory.retrieval import plan_summary_text
from backend.schemas.common import StrictModel
from backend.schemas.memory import PreferenceProfile, SavedTripRecord
from backend.schemas.trip_plan import TripPlan

logger = logging.getLogger(__name__)


class SaveUsefulPlanJob(StrictModel):
    plan: TripPlan
    rating: int = Field(ge=1, le=5)
    profile_id: str
    preferences: PreferenceProfile


class BackgroundWriter:
    """asyncio.Queue + one worker task. Job failures are logged and counted, never raised into
    request handlers.

    One worker (not a pool) keeps writes ordered and needs no locking: jobs for the same
    profile are applied in submission order.
    """

    def __init__(self, *, embedder: EmbeddingClient, repos: Repositories) -> None:
        self._embedder = embedder
        self._repos = repos
        self._queue: asyncio.Queue[SaveUsefulPlanJob] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._completed = 0
        self._failed = 0

    # ---- lifecycle ------------------------------------------------------------------------
    async def start(self) -> None:
        """Start the worker task (idempotent)."""
        self._ensure_worker()

    async def stop(self) -> None:
        """Drain pending jobs, then stop the worker."""
        await self.drain()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(
                self._run(), name="pathfinder-background-writer"
            )

    # ---- producer side --------------------------------------------------------------------
    def submit(self, job: SaveUsefulPlanJob) -> None:
        """Non-blocking enqueue. For each job: build SavedTripRecord(trip_id=new uuid,
        destination=plan.destination, rating, summary=plan_summary_text(plan), plan), add it,
        embed the summary and upsert the vector, upsert the preference profile.

        Returns immediately; the work happens on the worker task. If called from a running
        event loop before `start()`, the worker is started lazily so a job is never stranded.
        """
        self._queue.put_nowait(job)
        with contextlib.suppress(RuntimeError):  # no running loop: stays queued until start()
            self._ensure_worker()

    async def drain(self) -> None:
        """Wait until every submitted job has been processed (tests, shutdown)."""
        if not self._queue.empty():
            self._ensure_worker()  # jobs were queued before start(), or the worker died
        await self._queue.join()

    @property
    def completed(self) -> int:
        return self._completed

    @property
    def failed(self) -> int:
        return self._failed

    # ---- worker ---------------------------------------------------------------------------
    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._process(job)
                self._completed += 1
            except Exception as exc:  # a bad job must never kill the worker
                self._failed += 1
                logger.error("background save failed: %s", type(exc).__name__, exc_info=True)
            finally:
                self._queue.task_done()

    async def _process(self, job: SaveUsefulPlanJob) -> None:
        plan = job.plan
        summary = plan_summary_text(plan)
        # Embed first: the embedding call is the likeliest to fail (a network provider), and
        # doing it before any write means a failure leaves no half-saved trip behind.
        [vector] = await self._embedder.embed([summary])
        record = SavedTripRecord(
            trip_id=uuid.uuid4().hex,
            destination=plan.destination,
            rating=job.rating,
            summary=summary,
            plan=plan,
        )
        await self._repos.trips.add(record)
        await self._repos.embeddings.upsert(record.trip_id, self._embedder.model, vector)
        await self._repos.preferences.upsert(
            job.profile_id, merge_preferences(job.preferences, liked_plan=plan)
        )
