import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ConversationSessionStatus, ConversationStage
from app.models.trip import ConversationSession


class ConversationRepository:
    """Repository handling database access for ConversationSession entities."""

    async def create_session(
        self, db: AsyncSession, trip_id: uuid.UUID
    ) -> ConversationSession:
        """Create and flush a new ConversationSession record."""
        session = ConversationSession(
            trip_id=trip_id,
            status=ConversationSessionStatus.ACTIVE,
            current_stage=ConversationStage.TRIP_BASICS,
        )
        db.add(session)
        await db.flush()
        return session

    async def get_active_session_by_trip_id(
        self, db: AsyncSession, trip_id: uuid.UUID
    ) -> Optional[ConversationSession]:
        """Retrieve the latest conversation session for a trip."""
        stmt = (
            select(ConversationSession)
            .where(ConversationSession.trip_id == trip_id)
            .order_by(ConversationSession.created_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    async def get_or_create_active_session(
        self, db: AsyncSession, trip_id: uuid.UUID
    ) -> ConversationSession:
        """Fetch active conversation session or create a new one if absent."""
        session = await self.get_active_session_by_trip_id(db, trip_id)
        if not session:
            session = await self.create_session(db, trip_id)
        return session
