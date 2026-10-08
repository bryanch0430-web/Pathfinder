"""Session memory: TripPlan, conversation history and preference profile across turns.

Session memory is process-local; durable memory is written to the long-term store by the
background writer.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from backend.schemas.common import utcnow
from backend.schemas.memory import ConversationTurn, SessionState
from backend.schemas.trip import TripContext


class SessionNotFound(KeyError):
    pass


class SessionStore(Protocol):
    async def create(self, context: TripContext | None = None) -> SessionState: ...

    async def get(self, session_id: str) -> SessionState | None: ...

    async def save(self, state: SessionState) -> None: ...


class InMemorySessionStore:
    """Dict-backed store. `get` returns a deep copy so callers cannot mutate stored state
    without `save` (keeps a failed turn from half-writing the session).

    No `await` sits between a read and the write that depends on it, so a single event loop
    needs no lock; there is deliberately no threading here.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    async def create(self, context: TripContext | None = None) -> SessionState:
        state = SessionState(
            session_id=uuid.uuid4().hex,
            context=context.model_copy(deep=True) if context is not None else TripContext(),
        )
        self._sessions[state.session_id] = state.model_copy(deep=True)
        return state

    async def get(self, session_id: str) -> SessionState | None:
        stored = self._sessions.get(session_id)
        return stored.model_copy(deep=True) if stored is not None else None

    async def require(self, session_id: str) -> SessionState:
        """Like `get` but raises `SessionNotFound` for an unknown id."""
        state = await self.get(session_id)
        if state is None:
            raise SessionNotFound(session_id)
        return state

    async def save(self, state: SessionState) -> None:
        """Store a deep copy and bump `updated_at` (on the caller's object too, so the copy the
        caller holds and the stored one agree). Saving an unknown id creates it."""
        state.updated_at = utcnow()
        self._sessions[state.session_id] = state.model_copy(deep=True)

    async def delete(self, session_id: str) -> bool:
        """Forget a session; True if it existed."""
        return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        return len(self._sessions)


def append_turn(state: SessionState, turn: ConversationTurn) -> None:
    """Append to history and bump updated_at (full history is kept in session memory)."""
    state.history.append(turn)
    state.updated_at = utcnow()


def recent_history(state: SessionState, window: int) -> list[ConversationTurn]:
    """The last `window` turns: what the router and agents see."""
    if window <= 0:
        return []
    return list(state.history[-window:])
