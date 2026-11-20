"""Persistent trip budget inputs; totals are always derived from these rows."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BudgetPlan(Base):
    __tablename__ = "budget_plans"

    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True)
    target_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    trip: Mapped["Trip"] = relationship("Trip", back_populates="budget_plan")


class BudgetItem(Base):
    __tablename__ = "budget_items"
    __table_args__ = (UniqueConstraint("trip_id", "source_key", name="uq_budget_trip_source"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    source_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    place_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal("1"))
    unit_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    quote_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Suggested per-unit amount; never counted until the traveler accepts it into unit_amount.
    estimate_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    estimate_note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    trip: Mapped["Trip"] = relationship("Trip", back_populates="budget_items")
