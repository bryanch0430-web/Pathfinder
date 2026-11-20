"""The traveler's own drawings over a sketchbook page (Excalidraw elements), one row per trip page."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SketchNote(Base):
    __tablename__ = "sketch_notes"
    __table_args__ = (UniqueConstraint("trip_id", "page_id", name="uq_sketch_note_page"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The database removes a deleted trip's notes (ON DELETE CASCADE); no ORM relationship needed.
    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[str] = mapped_column(String(16), nullable=False)  # "overview", "d1", "d2", …
    scene: Mapped[dict] = mapped_column(JSON, nullable=False)  # {"elements": [...], "files": {...}}
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
