import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Enum as SQLEnum, Float, ForeignKey, Integer, String, Text, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import (
    ConversationSessionStatus,
    ConversationStage,
    MessageRole,
    MessageType,
    OnboardingStatus,
    TripStatus,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    guest_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    destination: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    places_to_visit: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    planning_preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    origin_text: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    origin_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    origin_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[TripStatus] = mapped_column(
        SQLEnum(TripStatus, native_enum=False),
        nullable=False,
        default=TripStatus.DRAFT,
    )
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        SQLEnum(OnboardingStatus, native_enum=False),
        nullable=False,
        default=OnboardingStatus.IN_PROGRESS,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationships
    conversations: Mapped[List["ConversationSession"]] = relationship(
        "ConversationSession",
        back_populates="trip",
        cascade="all, delete-orphan",
    )
    budget_plan: Mapped[Optional["BudgetPlan"]] = relationship(
        "BudgetPlan", back_populates="trip", cascade="all, delete-orphan", uselist=False,
    )
    budget_items: Mapped[List["BudgetItem"]] = relationship(
        "BudgetItem", back_populates="trip", cascade="all, delete-orphan",
    )


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, index=True
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[ConversationSessionStatus] = mapped_column(
        SQLEnum(ConversationSessionStatus, native_enum=False),
        nullable=False,
        default=ConversationSessionStatus.ACTIVE,
    )
    current_stage: Mapped[ConversationStage] = mapped_column(
        SQLEnum(ConversationStage, native_enum=False),
        nullable=False,
        default=ConversationStage.TRIP_BASICS,
    )
    context_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationships
    trip: Mapped["Trip"] = relationship("Trip", back_populates="conversations")
    messages: Mapped[List["ConversationMessage"]] = relationship(
        "ConversationMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at.asc()",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        SQLEnum(MessageRole, native_enum=False), nullable=False
    )
    message_type: Mapped[MessageType] = mapped_column(
        SQLEnum(MessageType, native_enum=False),
        nullable=False,
        default=MessageType.TEXT,
    )
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # Relationships
    session: Mapped["ConversationSession"] = relationship(
        "ConversationSession", back_populates="messages"
    )
