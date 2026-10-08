"""Initial long-term store: saved trips, trip embeddings (pgvector), preference profiles.

Revision ID: 0001
Revises:
Create Date: 2026-10-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from backend.db.models import EMBEDDING_DIM, HNSW_INDEX_NAME, HNSW_MAX_DIM

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "saved_trips",
        sa.Column("trip_id", sa.Text(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_saved_trips_rating_range"),
        sa.PrimaryKeyConstraint("trip_id"),
    )
    op.create_index("ix_saved_trips_destination", "saved_trips", ["destination"])

    # The vector width follows Settings.embedding_dim (default 1536), the same value the ORM
    # model uses, so schema and model agree for whatever dimension a deployment configures.
    op.create_table(
        "trip_embeddings",
        sa.Column("trip_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["trip_id"], ["saved_trips.trip_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("trip_id", "model"),
    )
    if EMBEDDING_DIM <= HNSW_MAX_DIM:
        # Cosine ops match the `<=>` operator used by the repository's similarity query.
        op.execute(
            f"CREATE INDEX {HNSW_INDEX_NAME} ON trip_embeddings "
            "USING hnsw (embedding vector_cosine_ops)"
        )

    op.create_table(
        "preference_profiles",
        sa.Column("profile_id", sa.Text(), nullable=False),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("profile_id"),
    )


def downgrade() -> None:
    # The `vector` extension is left installed: other schemas in the database may use it.
    op.drop_table("preference_profiles")
    op.execute(f"DROP INDEX IF EXISTS {HNSW_INDEX_NAME}")
    op.drop_table("trip_embeddings")
    op.drop_index("ix_saved_trips_destination", table_name="saved_trips")
    op.drop_table("saved_trips")
