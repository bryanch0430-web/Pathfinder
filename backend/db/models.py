"""SQLAlchemy 2.x models for the long-term store (PostgreSQL + pgvector).

Three tables, matching the repository protocols in `backend.db.repositories.base`:

* ``saved_trips``       - a plan the user marked useful, saved with destination and rating.
* ``trip_embeddings``   - one pgvector embedding per (trip, embedding model). The model name is
  part of the key so vectors from different embedders are never compared with each other.
* ``preference_profiles`` - the persisted preference profile, keyed by a caller-chosen id.

The vector column width comes from ``Settings.embedding_dim`` (default 1536); the Alembic
migration reads the same setting so model and schema cannot drift.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.settings import get_settings

EMBEDDING_DIM: int = get_settings().embedding_dim

# pgvector's HNSW index supports at most 2000 dimensions for the plain `vector` type. Above
# that the index is skipped (exact scan still works) rather than failing the migration.
HNSW_MAX_DIM = 2000
HNSW_INDEX_NAME = "ix_trip_embeddings_embedding_hnsw"


class Base(DeclarativeBase):
    """Declarative base; ``Base.metadata`` is what Alembic autogenerate/offline mode targets."""


class SavedTripRow(Base):
    __tablename__ = "saved_trips"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_saved_trips_rating_range"),
    )

    trip_id: Mapped[str] = mapped_column(Text, primary_key=True)
    destination: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    plan: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _embedding_table_args() -> tuple[Index, ...]:
    if EMBEDDING_DIM > HNSW_MAX_DIM:
        return ()
    return (
        Index(
            HNSW_INDEX_NAME,
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class TripEmbeddingRow(Base):
    __tablename__ = "trip_embeddings"
    __table_args__ = _embedding_table_args()

    trip_id: Mapped[str] = mapped_column(
        Text, ForeignKey("saved_trips.trip_id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PreferenceProfileRow(Base):
    __tablename__ = "preference_profiles"

    profile_id: Mapped[str] = mapped_column(Text, primary_key=True)
    profile: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
