import uuid
from typing import List, Optional
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MessageRole, MessageType
from app.models.trip import ConversationMessage, utc_now

# Within one timestamp your message comes before the reply. Rows written in one flush could share a
# timestamp (a coarse clock), and reloading the chat then showed some replies above their question.
TURN_ORDER = case((ConversationMessage.role == MessageRole.USER, 0), else_=1)


class MessageRepository:
    """Repository handling database access for ConversationMessage entities."""

    async def create_message(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        role: MessageRole,
        message_type: MessageType = MessageType.TEXT,
        content: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> ConversationMessage:
        """Create and add a ConversationMessage record to the DB session."""
        msg = ConversationMessage(
            session_id=session_id,
            role=role,
            message_type=message_type,
            content=content,
            payload=payload,
            created_at=utc_now(),  # when it was said, not when the turn's single flush ran
        )
        db.add(msg)
        return msg

    async def get_messages_by_session_id(
        self, db: AsyncSession, session_id: uuid.UUID
    ) -> List[ConversationMessage]:
        """Fetch all messages for a conversation session ordered chronologically."""
        stmt = (
            select(ConversationMessage)
            .where(ConversationMessage.session_id == session_id)
            .order_by(ConversationMessage.created_at.asc(), TURN_ORDER)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())
