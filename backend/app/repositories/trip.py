import uuid
from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import OnboardingStatus, TripStatus
from app.models.trip import Trip


class TripRepository:
    """Repository handling database access for Trip entities."""

    async def create_trip(
        self,
        db: AsyncSession,
        currency: str = "INR",
        user_id: Optional[str] = None,
        guest_id: Optional[str] = None,
    ) -> Trip:
        """Create and flush a new Trip record (belonging to either user_id or guest_id)."""
        new_trip = Trip(
            status=TripStatus.DRAFT,
            onboarding_status=OnboardingStatus.IN_PROGRESS,
            currency=currency,
            user_id=user_id,
            guest_id=guest_id,
        )
        db.add(new_trip)
        await db.flush()
        return new_trip

    async def get_by_id(
        self, db: AsyncSession, trip_id: uuid.UUID
    ) -> Optional[Trip]:
        """Fetch a Trip by its primary key ID."""
        stmt = select(Trip).where(Trip.id == trip_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_id(
        self, db: AsyncSession, user_id: str
    ) -> list[Trip]:
        """Fetch all trips created by an authenticated user."""
        stmt = select(Trip).where(Trip.user_id == user_id).order_by(Trip.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_guest_id(
        self, db: AsyncSession, guest_id: str
    ) -> list[Trip]:
        """Fetch all trips created by a guest."""
        stmt = select(Trip).where(Trip.guest_id == guest_id).order_by(Trip.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def claim_guest_trips(
        self, db: AsyncSession, guest_id: uuid.UUID, user_id: str
    ) -> int:
        """Transfer trips identified by the guest capability to a verified user."""
        result = await db.execute(
            update(Trip)
            .where(Trip.guest_id == str(guest_id), Trip.user_id.is_(None))
            .values(user_id=user_id, guest_id=None)
        )
        await db.commit()
        return result.rowcount or 0

    async def delete_trip(
        self, db: AsyncSession, trip: Trip
    ) -> None:
        """Delete a Trip record from database."""
        await db.delete(trip)
        await db.flush()



