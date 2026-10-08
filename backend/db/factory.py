"""Select the repository backend from settings.

  * "memory"   -> in-memory repositories (tests, no-database local runs)
  * "postgres" -> SQLAlchemy async + asyncpg + pgvector repositories
  * "cosmos"   -> TODO(provisional): raises NotImplementedError
"""

from __future__ import annotations

from backend.db.repositories.base import Repositories
from backend.db.repositories.memory import (
    InMemoryEmbeddingRepository,
    InMemoryPreferenceRepository,
    InMemoryTripRepository,
)
from backend.settings import Settings


async def _close_nothing() -> None:
    """In-memory repositories hold no external resources."""


def build_repositories(settings: Settings) -> Repositories:
    backend = settings.storage_backend
    if backend == "memory":
        return Repositories(
            trips=InMemoryTripRepository(),
            embeddings=InMemoryEmbeddingRepository(),
            preferences=InMemoryPreferenceRepository(),
            close=_close_nothing,
        )
    if backend == "postgres":
        # Imported lazily so the memory backend never loads the database driver stack.
        from backend.db.repositories.pg import (
            PgEmbeddingRepository,
            PgPreferenceRepository,
            PgTripRepository,
        )
        from backend.db.session import create_engine_and_sessionmaker

        engine, session_factory = create_engine_and_sessionmaker(settings.database_url)
        return Repositories(
            trips=PgTripRepository(session_factory),
            embeddings=PgEmbeddingRepository(session_factory),
            preferences=PgPreferenceRepository(session_factory),
            close=engine.dispose,
        )
    if backend == "cosmos":
        # TODO(provisional): Azure Cosmos DB (team alternative under consideration)
        raise NotImplementedError(
            "TODO(provisional): Azure Cosmos DB (team alternative under consideration); "
            "see backend/db/repositories/cosmos.py"
        )
    raise ValueError(f"unknown storage_backend: {backend!r}")
