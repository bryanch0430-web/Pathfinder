"""One user turn, one path. The orchestrator is the only place that knows the
full sequence: screen -> understand -> route -> gate -> plan/modify/ask/clarify.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import aclosing, asynccontextmanager, contextmanager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable, Iterator, Sequence

import httpx

from app.application.asking import AskPath
from app.application.limits import segment_count, tool_cap_for
from app.application.route_edit import (
    route_differs,
    rule_route_change,
    rule_split_stay,
    shares_a_city,
    sync_route,
)
from app.application.route_plan import route_question
from app.application.assessment import reassess
from app.application.intake import Attachment, IntakeResult, plans_from_share, run_intake, shares_something, without_links
from app.application.airport_transfers import sync_session_transfers
from app.application.country_check import whole_place_question
from app.application.budget_check import (
    PrePlanQuestion,
    budget_options,
    budget_question,
    floor_per_person_night,
    too_many_cities_question,
    trip_too_large_question,
)
from app.application.persona import identity_reply
from app.application.clarification import Clarifier, SLOT_LABELS
from app.application.freshness import LABELS as FRESHNESS_LABELS
from app.application.freshness import FreshnessService
from app.application.language import (
    chinese_input_reply,
    cuisine_slot,
    refresh_reply,
    set_language_reply,
    unsupported_reply,
)
from app.application.llm_gateway import LLMGateway
from app.application.memory import MemoryService
from app.application.modification import (
    HOME_FACTS,
    EditQuestion,
    ModifyPath,
    _imperative_edit,
    answers_the_home_question,
    plan_shape,
)
from app.application.proposals import decision_record, proposal_changes
from app.application.planning import PlanPath
from app.application.escalation import is_stuck, keeps_the_planner, queue_reason, should_escalate, stuck_flag
from app.application.router import (
    LANGUAGE_RULES,
    MODIFY_VERBS,
    RULES_FIRST,
    System1Router,
    is_replan_message,
    language_route,
    looks_like_question,
    not_about_the_trip,
    replans_new_dates,
    replans_new_destination,
    same_destination,
)
from app.application.toolbox import Toolbox
from app.application.turn_graph import review_and_reply
from app.application.turn_gate import tighten_after_clarification
from app.application.turn_state import (
    RESUMABLE_PHASES,
    TurnCheckpoint,
    TurnState,
    checkpoint_for,
)
from app.application.prompt_context import conversation_turns_of
from app.application.understanding import (
    PlaceGrounding,
    ground_new_places,
    is_clarify_answer,
    place_reply,
    understand,
    understand_note,
)
from app.application.validation import diff_plans, is_edit_outcome
from app.config import Settings
from app.domain.errors import (
    CallerKeyFailed,
    CanaryLeak,
    PathfinderError,
    PlanInvalidError,
    ProviderError,
    SecurityBlocked,
    ToolBudgetExceeded,
    operator_text,
)
from app.domain.models import (
    TRIP_STOPS_MAX,
    Intent,
    Message,
    RouteDecision,
    TripContext,
    TripPlan,
    TurnEvent,
    new_id,
    utcnow,
)
from app.infrastructure.observability import (
    bind_developer_view,
    bind_live_emit,
    note_turn_step,
    reset_developer_view,
    reset_live_emit,
    start_trace,
)
from app.infrastructure.store import PlanConflict, SQLiteStore
from app.security import SAFE_REPLY, screen_turn
from app.security.pii import mask_pii

logger = logging.getLogger(__name__)


def _duration_search(orchestrator: Any, toolbox: Any):
    """Web search for how long a named stop usually takes. Empty when search is off."""

    async def search(query: str):
        registry = getattr(getattr(orchestrator, "plan_path", None), "registry", None)
        sources = list(getattr(registry, "web", None) or [])
        if not sources or not getattr(sources[0], "available", False):
            return []
        try:
            return await toolbox.call("web.search", sources[0].search, query=query, count=3)
        except Exception:
            return []

    return search

#: What a traveller is told when a data source fails or a turn crashes. Exception text is never
#: shown: it can hold a source's error page, a URL with a key or a file path. It stays in the trace.
SOURCE_FAILED = "A data source failed. Your plan was not changed."
KEY_FAILED = "Your own API key failed, so this answer could not use your model. Check or replace the key in Settings."
TURN_FAILED = "Something went wrong while handling this turn. You can retry."
PLAN_CONFLICT = "Another change to this trip was still running. Please retry."
TRIP_GONE = "This trip no longer exists, so nothing was changed. Start a new trip to continue."
TOOL_LIMIT = "This request reached its limit of data lookups and was stopped. Please ask for a smaller change."
BLOCKED = "This request was stopped by a safety check."
#: How long a turn waits for a cancelled path to unwind before it goes on without it.
SETTLE_GRACE_SECONDS = 3.0


def user_facing_error(exc: BaseException) -> str:
    """The only error text a traveller is shown for a failed turn."""
    if isinstance(exc, PlanConflict):
        return PLAN_CONFLICT
    # Neither is a source failing: one is our own cap, the other names a tool and a path.
    if isinstance(exc, ToolBudgetExceeded):
        return TOOL_LIMIT
    if isinstance(exc, SecurityBlocked):
        return BLOCKED
    if isinstance(exc, CallerKeyFailed):
        return KEY_FAILED
    if isinstance(exc, (ProviderError, httpx.HTTPError, asyncio.TimeoutError)):
        return SOURCE_FAILED
    if isinstance(exc, PathfinderError):
        # Our own typed errors ("I could not locate ...") are written for the traveller.
        return str(exc)
    return TURN_FAILED


def _note_failure(tracer: Any, exc: BaseException) -> None:
    """Keep the exception's own text where only the operator reads it: a span of the trace."""
    with tracer.span("turn.failure", status="error", error_class=type(exc).__name__, error=operator_text(exc)[:500]):
        pass


@contextmanager
def _early_exit(tracer: Any, developer_token: Any, ended: str) -> Iterator[None]:
    """A turn that ends before the pipeline starts (a blocked message, a question about the assistant): its
    trace is finished however it ends, as ``ended`` when it is told to the end, as "closed" when the consumer
    stops reading first."""
    status = "closed"
    try:
        yield
        status = ended
    except Exception:
        status = "error"
        raise
    finally:
        reset_developer_view(developer_token)
        tracer.finish(status=status)


class TurnService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        memory: MemoryService,
        router: System1Router,
        clarifier: Clarifier,
        plan_path: PlanPath,
        modify_path: ModifyPath,
        ask_path: AskPath,
        gateway: LLMGateway,
        settings: Settings,
        tracer_proxy: Any | None = None,
        freshness: FreshnessService | None = None,
    ):
        self.store = store
        self.memory = memory
        self.router = router
        self.clarifier = clarifier
        self.plan_path = plan_path
        self.modify_path = modify_path
        self.ask_path = ask_path
        self.gateway = gateway
        self.settings = settings
        self.tracer_proxy = tracer_proxy
        self._background: set[asyncio.Future] = set()
        # One turn at a time per session: a lock exists only while a turn holds or awaits it.
        # An asyncio lock belongs to one event loop (the server has one), so each lock remembers which.
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_turns: dict[str, int] = {}
        self._session_loops: dict[str, asyncio.AbstractEventLoop] = {}
        self._session_activity: dict[str, str] = {}
        self.freshness = freshness or FreshnessService(
            registry=plan_path.registry, planner=plan_path.planner, settings=settings, store=store
        )
        # Reopening a trip must not save a refresh over the work of a turn that is running on it.
        self.freshness.session_busy = self.session_busy

    # -- turn state and checkpoints (robustness steps 1-3) ----------------
    def _checkpoint(self, state: TurnState, phase: str) -> None:
        """Write the live turn at one of the checkpoint points. A checkpoint is an aid, never a
        requirement: a store that cannot take it never fails the turn."""
        if not bool(getattr(self.settings, "enable_turn_checkpoints", False)):
            return
        try:
            checkpoint = checkpoint_for(
                state, phase=phase, ttl_seconds=int(getattr(self.settings, "turn_checkpoint_ttl_seconds", 900))
            )
            self.store.save_turn_checkpoint(
                request_id=checkpoint.request_id,
                session_id=checkpoint.session_id,
                phase=checkpoint.phase,
                message_hash=checkpoint.message_hash,
                base_plan_version=checkpoint.base_plan_version,
                data=checkpoint.to_json(),
                expires_at=checkpoint.expires_at,
            )
            state.checkpoint_phase = phase
            state.checkpointed_at = checkpoint.created_at
        except Exception:
            logger.exception("turn checkpoint was not kept")

    def _clear_checkpoints(self, session_id: str) -> None:
        try:
            self.store.drop_turn_checkpoints(session_id)
        except Exception:
            logger.exception("turn checkpoints were not cleared")

    def _load_checkpoint(self, session_id: str) -> TurnCheckpoint | None:
        if not bool(getattr(self.settings, "enable_turn_checkpoints", False)):
            return None
        try:
            row = self.store.latest_turn_checkpoint(session_id)
        except Exception:
            logger.exception("turn checkpoint could not be read")
            return None
        if not row:
            return None
        try:
            checkpoint = TurnCheckpoint.from_json(str(row["data"]))
        except Exception:
            logger.warning("a stored turn checkpoint could not be read; ignoring it", exc_info=True)
            self._clear_checkpoints(session_id)
            return None
        if checkpoint.phase not in RESUMABLE_PHASES:
            # A checkpoint from a phase that cannot be finished (only routing was done) is dropped here
            # rather than left to expire: nothing can resume from it.
            try:
                self.store.drop_turn_checkpoint(checkpoint.request_id)
            except Exception:
                logger.exception("an unusable turn checkpoint was not dropped")
            return None
        return checkpoint

    async def _resume_turn(
        self,
        *,
        checkpoint: TurnCheckpoint,
        state: TurnState,
        plan: TripPlan | None,
        context: TripContext,
        session_id: str,
        tracer: Any,
        request_id: str,
    ) -> AsyncIterator[TurnEvent]:
        """Finish a turn from its last checkpoint. Routing is not run again: the checkpoint holds the decision.

        A ``planned`` checkpoint writes the plan the cut-off turn had already built; ``ready`` writes the
        edit it had already validated and phrased; ``agents`` re-composes the plan from the evidence it
        had gathered, so no place, fare or forecast is fetched a second time.
        """
        decision = checkpoint.decision
        if decision is None:
            return
        _store_path(tracer, decision, decision)
        tracer.metadata.update(
            {
                "intent": decision.intent.value,
                "confidence": decision.confidence,
                "reason": decision.reason_code,
                "resumed": checkpoint.phase,
                "routes": "no_plan" if plan is None else f"plan_v{plan.version}",
            }
        )
        tracer.metadata.setdefault("flow_notes", []).append(
            f"Resumed from the {checkpoint.phase} checkpoint; the turn's earlier work was kept."
        )
        yield TurnEvent(event="route", data=_route_data(decision, self.settings))
        yield TurnEvent(
            event="progress",
            data={"phase": "resume", "message": "Continuing where the last attempt stopped.", "progress": 60},
        )
        state.record_route(decision)
        if checkpoint.phase == "planned" and checkpoint.plan is not None:
            state.record_plan(checkpoint.plan)
            async for event in self._persist_plan(
                plan=checkpoint.plan,
                body=checkpoint.reply,
                permissions=list(checkpoint.pending),
                context=context,
                session_id=session_id,
                replay=True,
            ):
                yield event
        elif checkpoint.phase == "ready" and checkpoint.plan is not None:
            updated = checkpoint.plan
            state.record_plan(updated)
            diff = diff_plans(plan, updated) if plan is not None else []
            async for event in self._emit_modify_result(
                updated=updated,
                diff=diff,
                prose=checkpoint.reply or updated.change_summary or "",
                facts=checkpoint.reply or "",
                record=None,
                permissions=list(checkpoint.pending),
                reply_meta={},
                session_id=session_id,
                replay=True,
            ):
                yield event
        elif checkpoint.phase == "agents" and checkpoint.agent_results:
            async for event in self._plan(
                context=context,
                session_id=session_id,
                tracer=tracer,
                request_id=request_id,
                previous=plan,
                state=state,
                given=dict(checkpoint.agent_results),
            ):
                yield event

    def _remember_in_background(self, owner: str, context: TripContext) -> None:
        """Session preferences go to the owner's long-term memory off the turn's critical path."""
        write = asyncio.ensure_future(
            asyncio.to_thread(self.memory.remember_preferences, owner, context.model_copy(deep=True))
        )
        self._background.add(write)
        write.add_done_callback(self._background_done)

    def _background_done(self, write: asyncio.Future) -> None:
        self._background.discard(write)
        if not write.cancelled() and write.exception() is not None:
            logger.warning("background preference write failed: %s", write.exception())

    async def drain_background(self) -> None:
        """Wait for pending background writes (tests and shutdown)."""
        if self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    def session_busy(self, session_id: str) -> bool:
        """True while a turn on the session runs or waits for the session's lock."""
        return session_id in self._session_turns

    def session_activity(self, session_id: str) -> str:
        """What the running turn last said it was doing, or empty when the session is idle."""
        return self._session_activity.get(session_id, "")

    @asynccontextmanager
    async def _lock_for(self, session_id: str, on_wait: Callable[[], None] | None = None) -> AsyncIterator[None]:
        """Hold the session's lock; drop it again when no other turn is waiting for it.

        ``on_wait`` is called first when another turn holds or awaits the lock, so this one has to wait."""
        loop = asyncio.get_running_loop()
        if self._session_loops.setdefault(session_id, loop) is not loop:
            # Waiting on a lock of another loop would never wake up: say so instead of hanging.
            raise RuntimeError("turns on one trip must all run in the same event loop")
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        self._session_turns[session_id] = self._session_turns.get(session_id, 0) + 1
        try:
            if on_wait is not None and self._session_turns[session_id] > 1:
                on_wait()
            async with lock:
                self._session_activity[session_id] = "Understanding your message."
                yield
        finally:
            self._session_turns[session_id] -= 1
            if not self._session_turns[session_id]:
                del self._session_turns[session_id]
                del self._session_locks[session_id]
                del self._session_loops[session_id]
                self._session_activity.pop(session_id, None)

    @contextmanager
    def _removed_if_deleted(self, session_id: str) -> Iterator[None]:
        """A trip deleted while a turn runs on it must not be written back by that turn: when the turn
        ends, whatever it saved after the delete (its plan version, its reply) is removed too."""
        existed = self.store.get_session(session_id) is not None
        try:
            yield
        finally:
            if existed and self.store.get_session(session_id) is None:
                self.store.delete_session(session_id)

    async def handle_turn(
        self,
        *,
        session_id: str,
        message: str,
        locale: str = "en",
        scope_item_id: str | None = None,
        scope_label: str | None = None,
        developer: bool = False,
        attachments: Sequence[Attachment] = (),
        review: bool = False,
        operator: bool = False,
        operator_name: str = "",
        on_wait: Callable[[], None] | None = None,
        on_start: Callable[[], None] | None = None,
        on_segments: Callable[[int], None] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """One turn. Turns on the same session run one after another: the second reads the
        plan the first saved, so neither overwrites the other's version. ``on_wait`` is called when
        this turn has to wait for earlier ones, ``on_start`` once it holds the lock and begins,
        ``on_segments`` with the number of cities the turn works on once that is known."""
        async with self._lock_for(session_id, on_wait):
            if on_start is not None:
                on_start()
            with self._removed_if_deleted(session_id):
                turn = self._run_turn(
                    session_id=session_id,
                    message=message,
                    locale=locale,
                    scope_item_id=scope_item_id,
                    scope_label=scope_label,
                    developer=developer,
                    attachments=attachments,
                    review=review,
                    operator=operator,
                    operator_name=operator_name,
                    on_segments=on_segments,
                )
                async with aclosing(turn) as events:
                    async for event in events:
                        if event.event == "progress":
                            note = str((event.data or {}).get("message") or "").strip()
                            if note:
                                self._session_activity[session_id] = note
                        yield event

    def _keep_decision(self, session_id: str, after: datetime, tracer: Any) -> None:
        """Write this turn's reasoning onto the assistant messages it just stored."""
        try:
            self.store.attach_decisions_since(session_id, after, _decision_payload(tracer))
        except Exception:
            logger.exception("could not keep the turn decision")

    async def _run_turn(
        self,
        *,
        session_id: str,
        message: str,
        locale: str = "en",
        scope_item_id: str | None = None,
        scope_label: str | None = None,
        developer: bool = False,
        attachments: Sequence[Attachment] = (),
        review: bool = False,
        operator: bool = False,
        operator_name: str = "",
        on_segments: Callable[[int], None] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        session = self.store.get_session(session_id)
        if session is None:
            # The trip was deleted while this message waited for its turn. Nothing is written for it, and no
            # trip is made in its place: one made here has no owner, so nobody could open or delete it.
            yield TurnEvent(
                event="error", data={"code": "session_not_found", "message": TRIP_GONE, "retryable": False}
            )
            return
        request_id = new_id("req")
        tracer = start_trace(self.settings, request_id)
        developer_token = bind_developer_view(developer)
        route_overflow = 0
        tracer.metadata["message"] = message
        tracer.metadata["session_id"] = session_id
        if self.tracer_proxy is not None:
            self.tracer_proxy.bind(tracer)
        screen = screen_turn(message, self.settings)
        if screen.flagged:
            for category in screen.categories:
                self.store.log_injection(category, screen.max_severity.value, channel="user_message")
        user_message = Message(
            session_id=session_id,
            role="user",
            content=message if operator else mask_pii(message, marks=True),
            meta={
                "locale": locale,
                "flagged": screen.flagged,
                "categories": screen.categories,
                **({"severity": screen.max_severity.value, "blocked": screen.blocked} if screen.flagged else {}),
                **({"scope": scope_label} if scope_label else {}),
                **({"attachments": [file.name[:200] for file in attachments]} if attachments else {}),
                **({"operator": True, "agent_name": operator_name[:80]} if operator else {}),
            },
        )
        self.store.add_message(user_message)

        if screen.blocked:
            with _early_exit(tracer, developer_token, "blocked"):
                tracer.metadata["reason"] = "security_blocklist"
                tracer.metadata["intent"] = "unclear"
                _store_path(
                    tracer,
                    None,
                    RouteDecision(intent=Intent.UNCLEAR, confidence=1, reason_code="security_blocklist"),
                )
                self.store.add_message(
                    Message(session_id=session_id, role="assistant", content=SAFE_REPLY, intent=Intent.UNCLEAR)
                )
                self._keep_decision(session_id, user_message.created_at, tracer)
                yield TurnEvent(
                    event="route",
                    data={
                        "intent": "unclear",
                        "confidence": 1.0,
                        "need_clarification": False,
                        "blocked": True,
                        "reason_code": "security_blocklist",
                    },
                )
                yield TurnEvent(event="token", data={"text": SAFE_REPLY})
            yield TurnEvent(
                event="done",
                data={"request_id": request_id, "intent": "unclear", "session_id": session_id, "blocked": True},
            )
            return

        from_file = plans_from_share(message) and bool(attachments)
        answering_form = is_clarify_answer(message)
        if (
            (from_file or answering_form)
            and not operator
            and self.store.is_held(session_id)
            and not (self.store.held_agent(session_id) or "").strip()
        ):
            # An automatic handoff from an earlier failed edit must not block the form or a new trip from a file.
            self.store.release_chat(session_id)
        if not from_file and not answering_form and not operator:
            policy = self.store.escalation_policy()
            parts = policy.get("parts") if isinstance(policy.get("parts"), dict) else {}
            hand = parts.get("hand") if isinstance(parts.get("hand"), dict) else {}
            stuck_part = parts.get("stuck") if isinstance(parts.get("stuck"), dict) else {}
            reason = should_escalate(message, 0, policy)
            if not reason:
                # Three unchanged tries, or the same question three times. A dates form is not that.
                stuck = is_stuck(
                    self.store.list_messages(session_id, limit=40),
                    int(stuck_part.get("after") or 3),
                )
                reason = queue_reason(message, hot=False, stuck=stuck)
            named = (self.store.held_agent(session_id) or "").strip()
            if self.store.is_held(session_id) and not named and not reason:
                # Nobody took the chat, and this line is not another handoff. The model keeps it.
                self.store.release_chat(session_id)
            elif reason and not self.store.is_held(session_id):
                # Tell the desk once. Do not end the turn: the model still answers this message,
                # and it keeps answering after a person has the chat.
                self.store.hold_chat(session_id, "")
                tracer.metadata.setdefault("flow_notes", []).append(
                    f"Handoff word check matched {reason}. The model still answers this message."
                )
                note_message = str((hand.get("message") if hand else "") or policy.get("message") or "").strip()
                note = note_message or "I'll pass this chat to a person. They will reply here."
                tracer.metadata["escalate"] = reason
                self.store.add_message(
                    Message(
                        session_id=session_id,
                        role="assistant",
                        content=note,
                        intent=Intent.ASK,
                        meta={"escalated": True, "escalate": reason},
                    )
                )

        # Identity and small-talk questions are static facts about the assistant:
        # answer them here, with no model call, no web search and no latency.
        identity = identity_reply(message)
        if identity:
            with _early_exit(tracer, developer_token, "ok"):
                tracer.metadata["reason"] = "identity"
                tracer.metadata["intent"] = "ask"
                _store_path(
                    tracer,
                    None,
                    RouteDecision(intent=Intent.ASK, confidence=1, reason_code="identity"),
                )
                self.store.add_message(
                    Message(session_id=session_id, role="assistant", content=identity, intent=Intent.ASK)
                )
                self._keep_decision(session_id, user_message.created_at, tracer)
                yield TurnEvent(
                    event="route",
                    data={
                        "intent": "ask",
                        "confidence": 1.0,
                        "threshold": 0.0,
                        "need_clarification": False,
                        "reason_code": "identity",
                        "related_to_plan": False,
                    },
                )
                for chunk in _chunks(identity):
                    yield TurnEvent(event="token", data={"text": chunk})
                self.store.touch_session(session_id)
            yield TurnEvent(
                event="done",
                data={"request_id": request_id, "intent": "ask", "session_id": session_id},
            )
            return

        context = self.store.get_context(session_id) or TripContext(session_id=session_id, language=locale)
        if context.finalized:
            frozen = "This plan is final. Open My trips to view it, or start a new plan to change anything."
            with _early_exit(tracer, developer_token, "ok"):
                tracer.metadata["reason"] = "finalized"
                tracer.metadata["intent"] = "ask"
                _store_path(
                    tracer,
                    None,
                    RouteDecision(intent=Intent.ASK, confidence=1, reason_code="finalized"),
                )
                self.store.add_message(
                    Message(session_id=session_id, role="assistant", content=frozen, intent=Intent.ASK)
                )
                self._keep_decision(session_id, user_message.created_at, tracer)
                yield TurnEvent(event="token", data={"text": frozen})
                self.store.touch_session(session_id)
            yield TurnEvent(
                event="done",
                data={"request_id": request_id, "intent": "ask", "session_id": session_id, "finalized": True},
            )
            return
        as_read = context.model_copy(deep=True)
        context.language = locale or context.language
        plan = self.store.get_plan(session_id)
        intent: Intent = Intent.UNCLEAR
        # How the trace of this turn ends. A handler below sets it before it ends the turn, so a turn that is
        # closed or cancelled while it reports a failure still has the failure on record.
        status = "ok"
        # The error event of a failed turn. It is sent from the end of this method, not from the handler: nothing
        # is yielded while an exception is being handled, so closing the turn at the error ends it like anywhere else.
        failure: TurnEvent | None = None
        # The live turn, one typed object (robustness step 1). Created once the history is read, below.
        state: TurnState | None = None
        try:
            # Display language, refresh and Chinese input are decided first: before
            # the selected stop, the understanding step and any model or tool.
            display = language_route(message, has_plan=plan is not None)
            if display is not None:
                _store_path(tracer, None, display)
                intent = display.intent
                async for event in self._display_turn(
                    decision=display.model_copy(update={"missing_slots": context.missing_slots()}),
                    plan=plan,
                    context=context,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                ):
                    yield event
                self.store.touch_session(session_id)
                return

            # The stored turns, this one last, read once: the router and every step that acts on the turn see the
            # recent conversation from them (the steps through `conversation_block`, without this message).
            history = self.store.list_messages(session_id, limit=conversation_turns_of(self.settings) + 1)
            state = TurnState.begin(
                session_id=session_id,
                request_id=request_id,
                message=message,
                context=context,
                history=history,
                plan=plan,
            )
            # A recent checkpoint of this same message, against this same plan, is a turn that was cut off
            # (deadline, disconnect, crash). Finish it instead of doing the work again.
            checkpoint = self._load_checkpoint(session_id)
            if (
                checkpoint is not None
                and checkpoint.decision is not None
                and checkpoint.can_finish
                and checkpoint.finishes(message, plan.version if plan is not None else 0)
            ):
                async for event in self._resume_turn(
                    checkpoint=checkpoint,
                    state=state,
                    plan=plan,
                    context=context,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                ):
                    yield event
                state.finish()
                self._clear_checkpoints(session_id)
                return
            if checkpoint is not None:
                # A checkpoint for another message, another plan or an old turn says nothing about this one.
                self._clear_checkpoints(session_id)
            # A short reply to an open clarification fills that slot. "Hong Kong" after
            # "where are you travelling from?" is the origin, not a new destination.
            clarify_slots = _pending_clarify(history)
            answering_origin = _fill_origin_answer(context, clarify_slots, message)
            # The message may answer the question an edit asked on the turn before. A question is not an answer, and
            # the page's "generate again" is the page's, not an answer.
            pending = (
                _pending_edit_question(history)
                if plan is not None and not looks_like_question(message) and not is_replan_message(message)
                else None
            )
            # An answer to "Where do you live?" that is a place ("Mong Kok", "Hong Kong", "I live in Osaka"), or a short
            # yes, no or doubt, goes back to that edit whatever a router would make of it. A request of its own ("plan
            # a trip to Osaka", "make day 2 lighter") is understood and routed as any message is, and the question
            # is dropped.
            captured = pending is not None and (
                (pending.fact in HOME_FACTS and answers_the_home_question(message))
                or _answers_pending_question(pending, message, plan)
            )
            answering = pending if captured else None
            # A link in the message, or an attached PDF or picture, is read first: what it says about a trip
            # fills the form's empty fields, and the message is then understood and routed without the link.
            understood = message
            intake = None
            if shares_something(message, attachments, self.settings):
                intake = await self._intake(
                    message, attachments, context, tracer, request_id, replace=plans_from_share(message)
                )
                if intake is not None:
                    yield TurnEvent(event="intake", data=intake.event())
                    understood = without_links(message) or message
                    tracer.metadata.setdefault("flow_notes", []).append(_intake_line(intake))
            place_check = PlaceGrounding()
            confirmed_places: set[str] = set()
            locked_places: dict[str, str] = {}
            for pending_place in _pending_places(history):
                if _confirms_pending(message, pending_place):
                    setattr(context, pending_place["slot"], pending_place["name"])
                    confirmed_places.add(pending_place["name"].casefold())
                    locked_places[pending_place["slot"]] = pending_place["name"]
            with tracer.span("understand"):
                try:
                    context = await understand(
                        understood, context, self.gateway, history=history, settings=self.settings, has_plan=plan is not None
                    )
                    route_overflow = context.route_overflow
                except CanaryLeak:
                    raise
                except Exception:
                    pass
                if answering_origin:
                    context.destination = as_read.destination
                    context.start_date = as_read.start_date
                    context.end_date = as_read.end_date
                    context.days = as_read.days
                if captured:
                    # A place in an answer to our own question is where the traveller lives, not where the trip goes
                    # or starts: the trip keeps its destination, origin and dates; a wish in it ("we're vegetarian")
                    # is kept.
                    for name in ("destination", "origin", "start_date", "end_date", "days"):
                        setattr(context, name, getattr(as_read, name))
                for slot, name in locked_places.items():
                    # The traveller accepted this gazetteer hit. Understanding must not replace it with another city
                    # that happens to appear in the address, such as London inside a Westminster street address.
                    setattr(context, slot, name)
                place_check = (
                    PlaceGrounding()
                    if context.route_overflow
                    else await ground_new_places(
                        as_read, context, self.plan_path.registry.geocoder, confirmed_places
                    )
                )
                _note_understand(tracer, as_read, context, place_check, understood)
                context = self._save_changes(as_read, context)
                # Preferences are written only after the traveller finalises the plan and marks it useful.

            routed: RouteDecision | None = None
            if captured:
                decision = RouteDecision(
                    intent=Intent.MODIFY,
                    confidence=0.95,
                    need_clarification=False,
                    reason_code="edit_answer",
                    missing_slots=[],
                    related_to_plan=True,
                    rationale="answer to the question the previous edit asked",
                )
            else:
                gate = str(getattr(self.settings, "router_gate", "jev") or "jev")
                route_model = str(self.settings.decision_model or "jev-latest") if gate == "jev" else gate
                yield _step("routing", "routing", route_model, status="running")
                route_started = time.perf_counter()
                with tracer.span("route"):
                    decision = await self.router.route(
                        message=understood,
                        context=context,
                        plan_summary=plan.summary_for_prompt() if plan else None,
                        history=history,
                    )
                routed = decision
                yield _step(
                    "routing",
                    "routing",
                    _route_label(decision, route_model),
                    status="done",
                    seconds=round(time.perf_counter() - route_started, 1),
                )
            scoped_action = (
                scope_item_id
                and plan is not None
                and re.search(
                    r"\b(remove|delete|drop|swap|replace|change|make|add|include|move)\b",
                    message.lower(),
                )
            )
            scoped_question = (
                scope_item_id
                and plan is not None
                and looks_like_question(message)
                and not re.search(MODIFY_VERBS, message.lower())
                and re.search(r"\b(this|that|it|here|selected)\b", message.lower())
            )
            route_change = rule_route_change(message, plan) if plan is not None else None
            split_stay = rule_split_stay(message, plan) if plan is not None and len(plan.segments) > 1 else None
            if intake is not None and plans_from_share(message) and (intake.filled or intake.places):
                missing = context.missing_slots()
                decision = decision.model_copy(
                    update={
                        "intent": Intent.PLAN,
                        "confidence": max(decision.confidence, 0.95),
                        "need_clarification": bool(missing),
                        "reason_code": "missing_" + "_".join(missing[:2]) if missing else "plan_from_file",
                        "missing_slots": missing,
                        "affected_parts": [],
                    }
                )
            elif split_stay is not None or (
                route_change is not None and (len(plan.segments) > 1 or route_change.op == "add")
            ):
                # "add Osaka", "remove Kyoto", "3 nights in Osaka", "another hotel for the last 2 nights" on a trip:
                # the route or the stays change (the dates stay), whatever else the sentence looks like to a router.
                decision = decision.model_copy(
                    update={
                        "intent": Intent.MODIFY,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": False,
                        "reason_code": "route_change",
                        "affected_parts": [],
                    }
                )
            elif (
                plan is not None
                and is_replan_message(message)
                and route_differs(plan, context)
                and shares_a_city(plan, context)
            ):
                # "Generate again" after the cities of the form were changed, with at least one of the plan's
                # cities still in it: the route changes, on the dates the form holds. Naming none of them is another trip.
                decision = decision.model_copy(
                    update={
                        "intent": Intent.MODIFY,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": False,
                        "reason_code": "route_change",
                        "affected_parts": [],
                    }
                )
            elif plan is not None and replans_new_destination(message, context, plan.summary_for_prompt()):
                # "Generate again" from the page, after the traveller named another place in the trip form: that is
                # a new trip (a plan for one city is not moved to another), whatever a model made of the sentence.
                # A new trip has its gate: what the form lacks (the departure city, the party, the budget, the dates)
                # is asked for, as for any new trip, and not planned without.
                missing = context.missing_slots()
                decision = decision.model_copy(
                    update={
                        "intent": Intent.PLAN,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": bool(missing),
                        "reason_code": "missing_" + "_".join(missing[:2]) if missing else "replan_destination",
                        "missing_slots": missing,
                        "affected_parts": [],
                    }
                )
            elif plan is not None and replans_new_dates(message, context, plan.summary_for_prompt()):
                # "Generate again" from the page, after the traveller put other dates in the trip form: the
                # trip is moved, whatever a model made of the sentence (a new trip would drop the locked stops).
                decision = decision.model_copy(
                    update={
                        "intent": Intent.MODIFY,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": False,
                        "reason_code": "replan_dates",
                        "affected_parts": ["dates"],
                    }
                )
            elif plan is not None and plan_shape(message) and not re.search(
                r"\b(this|that stop|selected)\b", message.lower()
            ):
                decision = decision.model_copy(
                    update={
                        "intent": Intent.MODIFY,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": False,
                        "reason_code": "plan_shape",
                        "affected_parts": ["attraction"],
                    }
                )
            elif scoped_action:
                # The selected stop is a decisive constraint. Terse commands
                # such as "remove this" should not fall below the confidence
                # gate and ask which item was meant.
                decision = decision.model_copy(
                    update={
                        "intent": Intent.MODIFY,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": False,
                        "reason_code": "scoped_item_action",
                    }
                )
            elif scoped_question:
                decision = decision.model_copy(
                    update={
                        "intent": Intent.ASK,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": False,
                        "reason_code": "scoped_item_question",
                    }
                )
            if (
                not captured
                and pending is not None
                and pending.fact not in HOME_FACTS
                and decision.reason_code not in {"replan_destination", "replan_dates"}
                and (decision.need_clarification or decision.intent in {Intent.UNCLEAR, Intent.MODIFY})
            ):
                # Another fact an edit asked for ("which stop?"): an answer the router reads as an edit, or is unsure
                # of, completes that edit.
                answering = pending
                decision = decision.model_copy(
                    update={
                        "intent": Intent.MODIFY,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": False,
                        "reason_code": "edit_answer",
                        "missing_slots": [],
                    }
                )
            if (
                plan is not None
                and not context.missing_slots()
                and (decision.need_clarification or decision.intent == Intent.UNCLEAR)
                and not screen.flagged
                and not_about_the_trip(message)
            ):
                # After RouteOrchestrator, and after an open edit question has had
                # its chance: an insult or a greeting is not about the selected stop.
                decision = decision.model_copy(
                    update={
                        "intent": Intent.ASK,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": False,
                        "reason_code": "not_about_the_trip",
                        "missing_slots": [],
                    }
                )
            failed_place = place_check.first()
            if failed_place is not None and not (plan is not None and keeps_the_planner(message)):
                # A name the gazetteer does not know, could not check, or matched a different place, is not planned.
                # A request to change the current plan ("something else", "I don't want to eat this") is not a new city.
                status, slot, _name = failed_place
                found = place_check.suggestion_for(slot) if status == "suggest_place" else ""
                decision = RouteDecision(
                    intent=Intent.UNCLEAR,
                    confidence=0.95,
                    need_clarification=True,
                    reason_code=status,
                    rationale=place_reply(place_check),
                    missing_slots=[slot],
                    related_to_plan=plan is not None,
                    slots={"place_slot": slot, "place_name": found} if found else {},
                )
            if (
                plan is not None
                and keeps_the_planner(message)
                and decision.intent in {Intent.UNCLEAR, Intent.ASK}
            ):
                about_food = bool(re.search(r"\b(eat|meal|lunch|dinner|breakfast|food|restaurant)\b", message, re.IGNORECASE))
                decision = decision.model_copy(
                    update={
                        "intent": Intent.MODIFY,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": False,
                        "reason_code": "meal_swap" if about_food else "item_swap",
                        "missing_slots": [],
                        "affected_parts": ["meal"] if about_food else (decision.affected_parts or ["attraction"]),
                    }
                )
            # "ok other suggestion for dim sum restaruant?": JEV often returns modify at 0.76 and the
            # threshold would clarify forever. A cuisine + suggestion/another is already an edit.
            if (
                plan is not None
                and decision.intent == Intent.MODIFY
                and decision.need_clarification
                and str(decision.reason_code).startswith("below_threshold")
                and cuisine_slot(message)
                and re.search(r"\b(?:another|other|suggest(?:ion)?s?|replace|swap|change)\b", message, re.IGNORECASE)
            ):
                decision = decision.model_copy(
                    update={
                        "need_clarification": False,
                        "confidence": max(decision.confidence, 0.9),
                        "reason_code": "meal_suggestion",
                        "missing_slots": [],
                        "affected_parts": ["meal"],
                    }
                )
            # The clarification gate (robustness step 5): a bare "yes"/"maybe" after a clarify that asked
            # what to do is asked about again, not planned from. The router stays the authority otherwise.
            decision = tighten_after_clarification(decision, message=message, history=history)
            intent = decision.intent
            if plan is not None and intent != Intent.PLAN:
                context = self._put_back_the_place(as_read, context)
            _store_path(tracer, routed, decision)
            tracer.metadata.update(
                {
                    "intent": decision.intent.value,
                    "confidence": decision.confidence,
                    "reason": decision.reason_code,
                    "routes": "no_plan" if plan is None else f"plan_v{plan.version}",
                }
            )
            # Checkpoint point 1: the route decision is made (Jev, the writer or the rules) and recorded.
            if state is not None:
                state.record_route(decision)
                self._checkpoint(state, "routed")
            yield TurnEvent(event="route", data=_route_data(decision, self.settings))

            if decision.need_clarification or decision.intent == Intent.UNCLEAR:
                async for event in self._clarify(
                    message=message,
                    context=context,
                    decision=decision,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                    history=history,
                    plan=plan,
                    scope_item_id=scope_item_id,
                    suggestions=place_check.suggestions,
                    state=state,
                ):
                    yield event
                self.store.touch_session(session_id)
                return

            if plan is not None and decision.intent in (Intent.ASK, Intent.MODIFY):
                # Proposal DB-13: an outdated forecast, fare or hotel price is fetched
                # again by its own agent before the turn reads the plan.
                refreshed = await self._refresh_outdated(plan, context, tracer, request_id)
                if refreshed is not None:
                    plan = refreshed
                    if decision.intent == Intent.ASK:
                        yield TurnEvent(event="plan", data={"plan": plan.model_dump(mode="json"), "diff": []})

            if decision.intent == Intent.PLAN:
                # A budget that cannot pay for the trip, or a start date that has gone by, is asked about before any
                # agent runs: planning it would end in a trip with no hotel.
                asked = (
                    too_many_cities_question(min(TRIP_STOPS_MAX, self.settings.max_cities), route_overflow)
                    if route_overflow
                    else trip_too_large_question(context, self.settings)
                    or route_question(context)
                    or budget_question(context, floor=self._budget_floor(context.currency), today=date.today())
                )
                if asked is None and not route_overflow:
                    # A whole country or region for four days or more: which cities, before any agent runs.
                    asked = await whole_place_question(self.plan_path.registry, context)
                if asked is not None:
                    tracer.metadata["reason"] = asked.reason_code
                    for event in self._ask_before_planning(asked, context=context, session_id=session_id):
                        yield event
                    self.store.touch_session(session_id)
                    return

            if on_segments is not None:
                on_segments(segment_count(plan=plan, context=context))
            # A closed turn closes the path it is running here, in this task, so the path stops.
            if decision.intent == Intent.PLAN:
                planner = str(getattr(self.settings, "llm_model", "") or "writer")
                yield _step("planning", "planning", planner, status="running")
                plan_started = time.perf_counter()
                path = self._plan(
                    context=context,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                    previous=plan,
                    state=state,
                )
                async with aclosing(path) as events:
                    async for event in events:
                        yield event
                yield _step(
                    "planning",
                    "planning",
                    planner,
                    status="done",
                    seconds=round(time.perf_counter() - plan_started, 1),
                )
            elif decision.intent == Intent.MODIFY:
                path = self._modify(
                    plan=plan,
                    context=context,
                    message=message,
                    decision=decision,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                    scope_item_id=scope_item_id,
                    history=history,
                    answering=answering,
                    review=review,
                    state=state,
                )
                async with aclosing(path) as events:
                    async for event in events:
                        yield event
            else:
                async for event in self._ask(
                    message=message,
                    plan=plan,
                    context=context,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                    scope_item_id=scope_item_id,
                    history=history,
                ):
                    yield event
        except GeneratorExit:
            # The consumer closed the turn: nobody is left to receive the closing events.
            status = "closed"
            raise
        except asyncio.CancelledError:
            # The deadline or a disconnect cut the turn off: nobody is left to receive them either.
            status = "cancelled"
            raise
        except CanaryLeak:
            status = "blocked"
            self.store.log_injection("canary_leak", "high", channel="model_output")
            message_text = "I cannot return that answer. Ask the question again in your own words."
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=message_text, intent=intent)
            )
            failure = TurnEvent(
                event="error",
                data={"code": "canary_leak", "message": message_text, "retryable": False},
            )
        except PlanInvalidError as exc:
            status = "error"
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=str(exc), intent=intent)
            )
            failure = TurnEvent(event="error", data={"code": exc.code, "message": str(exc), "retryable": exc.retryable})
        except PlanConflict as exc:
            status = "error"
            _note_failure(tracer, exc)
            message_text = user_facing_error(exc)
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=message_text, intent=intent)
            )
            failure = TurnEvent(
                event="error",
                data={"code": "plan_conflict", "message": message_text, "retryable": True},
            )
        except PathfinderError as exc:
            status = "error"
            _note_failure(tracer, exc)
            message_text = user_facing_error(exc)
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=message_text, intent=intent)
            )
            failure = TurnEvent(event="error", data={**exc.as_dict(), "message": message_text})
        except Exception as exc:
            status = "error"
            _note_failure(tracer, exc)
            message_text = user_facing_error(exc)
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=message_text, intent=intent)
            )
            failure = TurnEvent(
                event="error",
                data={"code": "internal_error", "message": message_text, "retryable": True},
            )
        finally:
            reset_developer_view(developer_token)
            tracer.finish(status=status)
            self._keep_decision(session_id, user_message.created_at, tracer)
            if state is not None:
                if status in ("ok", "blocked") and failure is None:
                    # The turn finished: its checkpoint has nothing left to resume and goes away.
                    state.finish()
                    if status == "ok":
                        self._clear_checkpoints(session_id)
                elif failure is not None:
                    state.record_error(str((failure.data or {}).get("message") or status))
            if status not in ("closed", "cancelled"):
                if failure is not None:
                    yield failure
                yield TurnEvent(
                    event="trace",
                    data={
                        "request_id": request_id,
                        "spans": [
                            {
                                "name": span.name,
                                # `duration_ms` is derived from the span timestamps in
                                # Span.to_dict(); it was never an attr, so reading it from
                                # attrs shipped null and the UI showed "— ms".
                                "duration_ms": span.to_dict()["duration_ms"],
                                "status": span.attrs.get("status"),
                            }
                            for span in tracer.spans
                        ][:40],
                        "generations": [
                            {
                                "purpose": generation["purpose"],
                                "model": generation["model"],
                                "prompt_tokens": generation["prompt_tokens"],
                                "completion_tokens": generation["completion_tokens"],
                                "latency_ms": generation["latency_ms"],
                                **(
                                    {
                                        "reasoning": generation.get("reasoning") or "",
                                        "output_preview": (generation.get("output_preview") or "")[:4000],
                                        "user_preview": (generation.get("user_preview") or "")[:8000],
                                    }
                                    if developer
                                    else {}
                                ),
                            }
                            for generation in tracer.generations
                        ],
                        "why": _why_lines(tracer),
                        "flow": list(tracer.metadata.get("flow") or []),
                        "path": tracer.metadata.get("path") or None,
                        **(
                            {
                                "reason": tracer.metadata.get("reason", ""),
                                "message": tracer.metadata.get("message", ""),
                                "searches": tracer.searches,
                                "tools": tracer.tools,
                            }
                            if developer
                            else {}
                        ),
                        "tool_calls": len([span for span in tracer.spans if span.name.startswith("tool.")]),
                        "steps": list(tracer.metadata.get("turn_steps") or []),
                    },
                )
                yield TurnEvent(
                    event="done",
                    data={
                        "request_id": request_id,
                        "intent": intent.value,
                        "session_id": session_id,
                    },
                )

    async def _display_turn(
        self, *, decision: RouteDecision, plan, context: TripContext, session_id, tracer, request_id
    ) -> AsyncIterator[TurnEvent]:
        """set_language, unsupported, refresh and Chinese input: fixed replies, no planner.

        set_language saves only the context's ``plan_locale`` (never a plan
        version); refresh runs the freshness check the reopen and turn hooks use.
        """
        yield TurnEvent(event="route", data=_route_data(decision, self.settings))
        locale = context.plan_locale
        if decision.intent == Intent.SET_LANGUAGE:
            target = decision.slots.get("language", "en")
            changed = target != context.plan_locale
            context.plan_locale = target  # type: ignore[assignment]
            self.store.save_context(context)
            yield TurnEvent(event="locale", data={"plan_locale": target})
            reply = set_language_reply(target, changed=changed)
        elif decision.intent == Intent.UNSUPPORTED:
            reply = unsupported_reply(decision.slots.get("language_name", "that language"), locale)
        elif decision.intent == Intent.REFRESH:
            refreshed: list[str] = []
            outdated: list[str] = []
            missing: list[str] = []
            if plan is not None:
                try:
                    outcome = await self.freshness.check(
                        plan, context=context, tracer=tracer, request_id=request_id, requested=True
                    )
                except Exception:  # a failed refresh keeps the earlier data
                    logger.exception("refreshing an outdated plan failed")
                    outcome = None
                if outcome is not None:
                    refreshed, outdated, missing = outcome.refreshed, outcome.outdated, outcome.missing
                    if refreshed:
                        yield TurnEvent(event="plan", data={"plan": outcome.plan.model_dump(mode="json"), "diff": []})
            reply = refresh_reply(
                locale,
                has_plan=plan is not None,
                refreshed=refreshed,
                outdated=outdated,
                labels=FRESHNESS_LABELS,
                missing=missing,
            )
        else:
            reply = chinese_input_reply(locale)
        self.store.add_message(
            Message(session_id=session_id, role="assistant", content=reply, intent=decision.intent)
        )
        for chunk in _chunks(reply):
            yield TurnEvent(event="token", data={"text": chunk})

    async def _intake(
        self,
        message: str,
        attachments: Sequence[Attachment],
        context: TripContext,
        tracer,
        request_id: str,
        replace: bool = False,
    ) -> IntakeResult | None:
        """Read what the message shares into ``context``. None when the step itself failed (the turn goes on)."""
        toolbox = Toolbox(
            path="intake",
            tracer=tracer,
            store=self.store,
            request_id=request_id,
            max_calls=self.settings.intake_max_urls or None,
        )
        try:
            with tracer.span("intake") as span:
                result = await run_intake(
                    message=message,
                    attachments=attachments,
                    context=context,
                    gateway=self.gateway,
                    reader=getattr(self.plan_path.registry, "documents", None),
                    toolbox=toolbox,
                    settings=self.settings,
                    replace=replace,
                )
                span.attrs.update(
                    {
                        "sources": len(result.sources),
                        "read": sum(1 for source in result.sources if source.status == "read"),
                        "filled": result.filled,
                        "places": len(result.places),
                    }
                )
        except CanaryLeak:
            raise
        except Exception:
            logger.exception("intake failed open")
            return None
        tracer.metadata["intake"] = result.event()
        return result

    async def _refresh_outdated(self, plan, context, tracer, request_id):
        try:
            outcome = await self.freshness.check(plan, context=context, tracer=tracer, request_id=request_id)
        except Exception:  # a failed refresh never stops the turn
            logger.exception("refreshing an outdated plan failed")
            return None
        return outcome.plan if outcome is not None else None

    def _save_changes(self, as_read: TripContext, changed: TripContext) -> TripContext:
        """Save what this turn changed in the trip's form on top of the form as it is stored now, so
        a field saved while the turn was working (the traveller's own edit of the form) is kept.
        ``as_read`` is the form as the turn read it, before it changed anything."""
        current = self.store.get_context(changed.session_id)
        if current is not None:
            for name in TripContext.model_fields:
                before, after = getattr(as_read, name), getattr(changed, name)
                if name == "session_id" or after == before:
                    continue
                if isinstance(after, list):  # a turn only adds preferences and constraints
                    kept = getattr(current, name)
                    after = [*kept, *(item for item in after if item not in before and item not in kept)]
                setattr(current, name, after)
            # Not stored, so it is carried over by hand: the planning that follows this message reads it.
            current.set_best_order(changed.best_order)
            changed = current
        self.store.save_context(changed)
        return changed

    def _put_back_the_place(self, as_read: TripContext, read: TripContext) -> TripContext:
        """A question or an edit does not move a trip that has a plan to the place its sentence names or seems to name:
        the form's destination, and the dates and length worked out from it, go back to what the form held before the
        message. A field the traveller saved meanwhile (it no longer holds what this message left) is not touched."""
        current = self.store.get_context(read.session_id)
        if current is None:
            return read
        for name in ("destination", "stops", "start_date", "end_date", "days"):
            if getattr(current, name) == getattr(read, name):
                setattr(current, name, getattr(as_read, name))
        self.store.save_context(current)
        return current

    def _memory_owner(self, session_id: str) -> str:
        """Whose saved trips the planner may recall: the session's owner only."""
        session = self.store.get_session(session_id)
        return session.owner if session else ""

    async def _plan(
        self,
        *,
        context,
        session_id,
        tracer,
        request_id,
        previous=None,
        state: TurnState | None = None,
        given: dict[str, Any] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """The plan path. ``given`` is the evidence a resumed turn already holds (a checkpoint): it is
        merged with what the agents return instead of being fetched again."""
        if state is not None:
            state.start("planning")
        matches = self.memory.recall(context, owner=self._memory_owner(session_id))
        # A new trip for the place the saved plan is for keeps what the traveller protected in it: the stay they
        # accepted and their locked or confirmed stops. A trip to another place has nothing to carry.
        carries = previous is not None and _same_place(previous.destination.name, context.destination)
        stay = previous.first_stay if carries else None
        if stay is not None and not (stay.confirmed or stay.locked):
            stay = None
        if matches:
            tracer.metadata["memory_matches"] = [round(score, 3) for score, _ in matches]
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        def emit(event_name: str, data: dict[str, Any]) -> None:
            queue.put_nowait((event_name, data))

        def on_results(results: dict[str, Any]) -> None:
            """Checkpoint point 2: the agents have finished, before the planner merges their evidence."""
            if state is None:
                return
            state.record_agents(results)
            self._checkpoint(state, "agents")

        toolbox = Toolbox(
            path="plan",
            tracer=tracer,
            store=self.store,
            request_id=request_id,
            max_calls=tool_cap_for(self.settings, segment_count(context=context)) or None,
        )
        yield TurnEvent(event="progress", data={"phase": "start", "message": "Planning the trip.", "progress": 5})
        live = bind_live_emit(emit)
        task = asyncio.create_task(
            self.plan_path.run(
                context=context,
                session_id=session_id,
                toolbox=toolbox,
                tracer=tracer,
                emit=emit,
                memory_summaries=self.memory.memory_summaries(matches),
                version=1,
                keep_hotel=stay,
                given=given,
                on_results=on_results,
            )
        )
        try:
            async for event in _drain(queue, task):
                yield event
            plan = await task
        finally:
            await _settle(task)  # first: a path that is not stopped keeps working whatever else goes wrong here
            reset_live_emit(live)
        if carries:
            plan = await self.modify_path.carry_protected(previous, plan, context=context, toolbox=toolbox)
        if carries and plan.assessment is not None:
            plan.assessment = reassess(plan, context, self.settings)
        await sync_session_transfers(plan, registry=self.plan_path.registry, store=self.store, session_id=session_id)
        yield TurnEvent(
            event="progress",
            data={"phase": "review", "message": "Checking whether the draft is reasonable.", "progress": 92},
        )
        draft_permissions: list[dict[str, Any]] = []
        try:
            draft_reply = await review_and_reply(
                self.settings,
                self.gateway,
                message="Review this first draft.",
                history=(),
                plan=plan,
                facts=plan.summary or "Draft plan.",
                route_model=str(getattr(self.settings, "decision_model", "") or ""),
                search=_duration_search(self, toolbox),
                phrase=False,
                permissions=draft_permissions,
            )
        except Exception:
            logger.exception("first-draft review failed open")
            draft_reply = plan.summary or ""
        # Checkpoint point 3: the plan is built, checked and phrased, and the turn is about to persist it.
        if state is not None:
            state.record_plan(plan)
            state.reply = (draft_reply or plan.summary or "").strip()
            state.pending = list(draft_permissions)
            state.start("validating")
            self._checkpoint(state, "planned")
        async for event in self._persist_plan(
            plan=plan,
            body=draft_reply,
            permissions=draft_permissions,
            context=context,
            session_id=session_id,
        ):
            yield event

    async def _persist_plan(
        self,
        *,
        plan: TripPlan,
        body: str | None,
        permissions: list[dict[str, Any]],
        context: TripContext,
        session_id: str,
        replay: bool = False,
    ) -> AsyncIterator[TurnEvent]:
        """Write a finished plan version, its reply and its events. Shared by the plan path and a resume:
        the turn that was cut off at the "planned" checkpoint calls exactly this, so it writes one plan."""
        try:
            self.store.save_plan(plan, plan.change_summary or "Initial plan")
        except PlanConflict:
            if not replay:
                raise
            # A resumed turn may have been cut off after its own save: the version is already there,
            # and this turn is finishing the same plan, not racing another turn for it.
            logger.info("the resumed plan version %s is already stored", plan.version)
        if context.best_order and len(plan.segments) > 1:
            # The cities were put in their best order: the form follows the plan, so a later turn does not put them back.
            stored = self.store.get_context(session_id)
            if stored is not None and sync_route(stored, plan):
                self.store.save_context(stored)
        # What the assessment could not settle is asked with the plan; the answer is an edit like any other.
        question = plan.assessment.question if plan.assessment is not None else ""
        body = (body or plan.summary or "").strip()
        if question and question not in body:
            body = f"{body}\n\n{question}"
        plan_meta: dict[str, Any] = {}
        if question:
            plan_meta["assessment_question"] = True
        if permissions:
            plan_meta["proposal"] = {"changes": permissions, "base_version": plan.version}
        reply = self._already_told(session_id, body) if replay else None
        if reply is None:
            reply = Message(
                session_id=session_id,
                role="assistant",
                content=body,
                intent=Intent.PLAN,
                meta=plan_meta,
            )
            self.store.add_message(reply)
        if permissions:
            yield TurnEvent(
                event="proposal",
                data={"message_id": reply.id, "proposal": plan_meta["proposal"]},
            )
        yield TurnEvent(event="plan", data={"plan": plan.model_dump(mode="json"), "diff": []})
        self.store.touch_session(session_id)

    def _already_told(self, session_id: str, content: str) -> Message | None:
        """The stored turn that already says this, or None: a resumed turn must not repeat a reply.

        The row itself is returned so a proposal on it still names the message the traveller ticks.
        """
        text = (content or "").strip()
        if not text:
            return None
        try:
            rows = self.store.list_messages(session_id, limit=3)
        except Exception:
            return None
        return next(
            (row for row in rows if row.role == "assistant" and (row.content or "").strip() == text),
            None,
        )

    async def edit_from_desk(
        self,
        session_id: str,
        message: str,
        *,
        agent_name: str,
        scope_item_id: str | None = None,
    ) -> str:
        """Apply one change with the edit-itinerary skill. The desk already chose an edit, so this does not re-route the words."""
        from app.domain.errors import PlanInvalidError

        async with self._lock_for(session_id, None):
            plan = self.store.get_plan(session_id)
            context = self.store.get_context(session_id)
            if plan is None or context is None:
                raise PlanInvalidError("There is no itinerary to modify yet.")
            decision = RouteDecision(
                intent=Intent.MODIFY,
                confidence=1.0,
                reason_code="desk_edit",
                rationale="A person on the desk asked the edit skill to change this plan.",
            )
            request_id = new_id("req")
            tracer = start_trace(self.settings, request_id)
            prose = ""
            try:
                async for event in self._modify(
                    plan=plan,
                    context=context,
                    message=message,
                    decision=decision,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                    scope_item_id=scope_item_id,
                    history=self.store.list_messages(session_id, limit=conversation_turns_of(self.settings) + 1),
                    review=False,
                    reply_meta={"live_agent": True, "agent_name": agent_name, "desk_edit": True},
                ):
                    if event.event == "token":
                        prose = str((event.data or {}).get("text") or prose)
                tracer.finish(status="ok")
            except Exception:
                tracer.finish(status="error")
                raise
            return prose

    async def _modify(
        self,
        *,
        plan,
        context,
        message,
        decision,
        session_id,
        tracer,
        request_id,
        scope_item_id: str | None = None,
        history: Sequence[Message] = (),
        answering: EditQuestion | None = None,
        review: bool = False,
        reply_meta: dict[str, Any] | None = None,
        state: TurnState | None = None,
    ) -> AsyncIterator[TurnEvent]:
        if plan is None:
            raise PlanInvalidError("There is no itinerary to modify yet.")
        if state is not None:
            state.start("modifying")
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        def emit(event_name: str, data: dict[str, Any]) -> None:
            queue.put_nowait((event_name, data))

        toolbox = Toolbox(
            path="modify",
            tracer=tracer,
            store=self.store,
            request_id=request_id,
            max_calls=tool_cap_for(self.settings, segment_count(plan=plan, context=context)) or None,
        )
        writer_name = str(getattr(self.settings, "llm_model", "") or "writer")
        edit_started = time.perf_counter()
        yield _step("editing", "editing", writer_name, status="running")
        yield TurnEvent(event="progress", data={"phase": "start", "message": "Applying the smallest change.", "progress": 5})
        live = bind_live_emit(emit)
        task = asyncio.create_task(
            self.modify_path.run(
                plan=plan,
                context=context,
                message=message,
                route_parts=decision.affected_parts,
                toolbox=toolbox,
                tracer=tracer,
                emit=emit,
                memory_summaries=self.memory.memory_summaries(
                    self.memory.recall(context, owner=self._memory_owner(session_id))
                ),
                scope_item_id=scope_item_id,
                history=history,
                answering=answering,
            )
        )
        try:
            async for event in _drain(queue, task):
                yield event
            result = await task
        finally:
            await _settle(task)
            reset_live_emit(live)
        yield _step(
            "editing",
            "editing",
            writer_name,
            status="done",
            seconds=round(time.perf_counter() - edit_started, 1),
        )
        asked = getattr(result, "asks", None)
        if asked is not None:
            # The edit needs a fact first: one question, with the clarify card, and nothing in the plan changes.
            tracer.metadata["reason"] = "edit_needs_fact"
            for event in self._ask_for_fact(asked, session_id=session_id, reply_meta=reply_meta):
                yield event
            self.store.touch_session(session_id)
            return
        updated, diff = result
        # The airport transfers follow the flights and stays the edit left; what was worked out again is said.
        transfer_said = await sync_session_transfers(
            updated, registry=self.plan_path.registry, store=self.store, session_id=session_id, before=plan
        )
        present = {item.id for _, item in updated.iter_items()}
        prior_notes = set(plan.data_quality.notes)
        fresh_left_out = [
            note
            for note in updated.data_quality.notes
            if note not in prior_notes and "was left out of" in note
        ]
        diff, lost_reasons = explain_lost_adds(diff, present, fresh_left_out)
        if lost_reasons and re.search(r"\bmorning\b", message, re.IGNORECASE) and any(
            "reach your stay" in reason for reason in lost_reasons
        ):
            lost_reasons.append("That is after the morning, so a morning stop on that day cannot be added.")
        if lost_reasons:
            transfer_said = [*transfer_said, *lost_reasons]
        if not diff:
            # The only change was a stop the transfer removed. Do not call that a new version.
            updated.version = plan.version
            if lost_reasons:
                updated.change_summary = " ".join(lost_reasons)
        if transfer_said and diff:
            updated.change_summary = " ".join([updated.change_summary, *transfer_said]).strip()
        # The trip form follows the route the edit made (the cities, their nights, the first as the destination).
        # It is written only when the edit is: an edit that waits to be accepted carries it in the proposal.
        form = context.model_copy(deep=True)
        form_changed = (len(plan.segments) > 1 or len(updated.segments) > 1) and sync_route(form, updated)
        # The scope the edit really used is what the account of the decision says, not the words of the message.
        tracer.metadata.update(
            {
                "edit_scope": getattr(result, "scope", "plan"),
                "edit_day": getattr(result, "day", None),
                "edit_selected": bool(getattr(result, "selected", False)),
            }
        )
        # What the edit could not do (nothing cheaper came back) is the reply to it, not a fact about the plan's
        # data: it is told below and not kept as a data gap.
        outcomes = [gap for gap in updated.data_quality.gaps if is_edit_outcome(gap)]
        told = [gap.message for gap in outcomes if gap not in plan.data_quality.gaps]
        updated.data_quality.gaps = [gap for gap in updated.data_quality.gaps if gap not in outcomes]
        # An edit that changed nothing rewrites the version it read; the reason is the reply, and the version's
        # own summary (what that version was) is left as it is.
        unchanged = updated.version == plan.version
        if updated.assessment is not None and not unchanged:
            # The assessment follows the edit, from the plan's own checks (no further research here).
            updated.assessment = reassess(updated, form, self.settings)
        offers = list(getattr(result, "offers", None) or [])
        pending = offers if review and not diff else []
        if review and diff and not is_replan_message(message):
            changes = proposal_changes(plan, updated, summary=_describe_diff(diff, updated) or updated.change_summary)
            # Every change waits for a tick or a cross, including one stop.
            # An add is a green block, a removal is red, and a new clock is yellow.
            # The form's own "Re-plan" button is the traveller applying the details, so that plan is written now.
            pending = list(changes)
        elif diff:
            # Several stop changes (a long visit plus the attractions it would replace) wait
            # one tick each. Accepting one leaves the others as they are.
            changes = proposal_changes(plan, updated, summary=_describe_diff(diff, updated) or updated.change_summary)
            stop_changes = [
                change
                for change in changes
                if str(change.get("id") or "").split("-", 1)[0] in {"add", "drop", "time", "move", "rename", "replace"}
            ]
            if stop_changes and (getattr(result, "hold", False) or len(stop_changes) >= 2):
                pending = stop_changes
        summary = (
            "This edit is ready and is not written yet."
            if pending and not offers
            else updated.change_summary
        )
        record = decision_record(message, summary, pending, held=bool(pending)) if review else None
        if record is not None:
            tracer.metadata["edit_steps"] = record["steps"]
        if pending:
            # The web asked to wait. The edited plan stays in the reply until a change is ticked.
            held = {
                "changes": pending,
                "base_version": plan.version,
                **({"context": form.model_dump(mode="json")} if form_changed else {}),
            }
            prose = (
                f"{updated.change_summary} Tick one suggestion to apply it, or cross it to leave the plan as it is."
                if offers
                else f"{summary} Nothing is written until you accept a change."
            )
            reply = Message(
                session_id=session_id,
                role="assistant",
                content=prose,
                intent=Intent.MODIFY,
                meta={"proposal": held, "log": record, "diff": [], **(reply_meta or {})},
            )
            self.store.add_message(reply)
            yield TurnEvent(event="token", data={"text": prose})
            yield TurnEvent(
                event="proposal",
                data={"message_id": reply.id, "proposal": held, "log": record},
            )
            yield TurnEvent(event="plan", data={"plan": plan.model_dump(mode="json"), "diff": []})
            self.store.touch_session(session_id)
            return
        if form_changed:
            self.store.save_context(form)
        self.store.save_plan(updated, None if unchanged else updated.change_summary, replace=unchanged)
        description = _describe_diff(diff, updated)
        if diff:
            # A trip that moved takes its stay, flights and forecast along: not everything else is unchanged.
            rest = "" if any(entry.get("kind") == "dates" for entry in diff) else " Everything else is unchanged."
            if getattr(result, "scope", "") == "route":
                # A route change says which nights moved; the stops it kept need no list.
                description = updated.change_summary
                rest = ""
            facts = " ".join(
                [
                    f"Updated to version {updated.version}. " + description + rest,
                    *told,
                    *[s for s in transfer_said if s not in description],
                ]
            )
        else:
            # Nothing changed. Do not claim a version: say why, using the reason
            # the edit recorded (a data gap, an unmatched stop, a duplicate).
            facts = updated.change_summary or description or "Nothing in the plan matched that request."
        # The review checks the result, lengthens a visit the table says is too short, then the writer phrases it.
        # A removal that would make room is returned for the traveller to accept. It is not written here.
        edit_permissions: list[dict[str, Any]] = []
        review_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        async def _review() -> str:
            return await review_and_reply(
                self.settings,
                self.gateway,
                message=message,
                history=history,
                plan=updated,
                facts=facts,
                route_model=str(getattr(self.settings, "decision_model", "") or ""),
                search=_duration_search(self, toolbox),
                on_step=lambda data: (note_turn_step(data), review_queue.put_nowait(("step", data))),
                permissions=edit_permissions,
            )

        review_task = asyncio.create_task(_review())
        try:
            async for event in _drain(review_queue, review_task):
                yield event
            prose = await review_task
        finally:
            await _settle(review_task)
        # Checkpoint point 3: the edit is validated and phrased, and about to be persisted.
        if state is not None:
            state.record_plan(updated)
            state.reply = prose
            state.pending = list(edit_permissions)
            state.start("validating")
            self._checkpoint(state, "ready")
        async for event in self._emit_modify_result(
            updated=updated,
            diff=diff,
            prose=prose,
            facts=facts,
            record=record,
            permissions=edit_permissions,
            reply_meta=reply_meta,
            session_id=session_id,
        ):
            yield event

    async def _emit_modify_result(
        self,
        *,
        updated: TripPlan,
        diff: list[dict[str, Any]],
        prose: str,
        facts: str,
        record: dict[str, Any] | None,
        permissions: list[dict[str, Any]],
        reply_meta: dict[str, Any] | None,
        session_id: str,
        replay: bool = False,
    ) -> AsyncIterator[TurnEvent]:
        """Write the edited plan, its reply and its events. Shared by the modify path and a resume, so a
        turn that was cut off at the "ready" checkpoint writes one version and one reply."""
        try:
            self.store.save_plan(updated, None, replace=True)
        except Exception:
            logger.exception("saving the reviewed clocks failed")
        flag = stuck_flag(self.store.list_messages(session_id, limit=40), prose)
        meta = {
            "diff": diff,
            **({"stuck_flag": flag} if flag else {}),
            **({"log": record} if record is not None else {}),
            **({"edit_facts": facts} if prose.strip() != facts.strip() else {}),
            **(
                {"proposal": {"changes": permissions, "base_version": updated.version}}
                if permissions
                else {}
            ),
            **(reply_meta or {}),
        }
        reply = self._already_told(session_id, prose) if replay else None
        if reply is None:
            reply = Message(
                session_id=session_id,
                role="assistant",
                content=prose,
                intent=Intent.MODIFY,
                meta=meta,
            )
            self.store.add_message(reply)
        if flag >= 3 and not self.store.is_held(session_id):
            policy = self.store.escalation_policy()
            parts = policy.get("parts") if isinstance(policy.get("parts"), dict) else {}
            stuck_part = parts.get("stuck") if isinstance(parts.get("stuck"), dict) else {}
            hand = parts.get("hand") if isinstance(parts.get("hand"), dict) else {}
            limit = int(stuck_part.get("after") or 3)
            if flag >= limit:
                self.store.hold_chat(session_id, "")
                note = str((hand.get("message") if hand else "") or policy.get("message") or "").strip()
                note = note or "I'll pass this chat to a person. They will reply here."
                self.store.add_message(
                    Message(
                        session_id=session_id,
                        role="assistant",
                        content=note,
                        intent=Intent.ASK,
                        meta={"escalated": True, "escalate": "stuck", "stuck_flag": flag},
                    )
                )
                prose = f"{prose}\n\n{note}"
        yield TurnEvent(event="token", data={"text": prose})
        if record is not None or permissions:
            yield TurnEvent(
                event="proposal",
                data={
                    "message_id": reply.id,
                    "proposal": {"changes": permissions, "base_version": updated.version},
                    **({"log": record} if record is not None else {}),
                },
            )
        yield TurnEvent(
            event="plan",
            data={"plan": updated.model_dump(mode="json"), "diff": diff},
        )
        self.store.touch_session(session_id)

    async def _ask(
        self,
        *,
        message,
        plan,
        context,
        session_id,
        tracer,
        request_id,
        scope_item_id: str | None = None,
        history: Sequence[Message] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        if not_about_the_trip(message):
            # A greeting or a question about the assistant is not a trip question.
            # Answer here, so the turn does not wait on a full plan rewrite.
            place = (getattr(context, "destination", None) or "").strip()
            answer = (
                f"I'm Pathfinder, the planner for this {place} trip. Ask me about the plan, or tell me what to change."
                if place
                else "I'm Pathfinder, the trip planner. Tell me where and when you want to go."
            )
            tracer.metadata["related_to_plan"] = False
            tracer.metadata["answer_source"] = "chat"
            self.store.add_message(
                Message(
                    session_id=session_id,
                    role="assistant",
                    content=answer,
                    intent=Intent.ASK,
                    meta={"source": "chat", "related_to_plan": False},
                )
            )
            yield TurnEvent(event="token", data={"text": answer})
            self.store.touch_session(session_id)
            return
        toolbox = Toolbox(
            path="ask",
            tracer=tracer,
            store=self.store,
            request_id=request_id,
            max_calls=tool_cap_for(self.settings, segment_count(plan=plan)) or None,
        )
        answer, related, source = await self.ask_path.answer(
            question=message,
            plan=plan,
            context=context,
            toolbox=toolbox,
            emit=None,
            history=list(history) if history is not None else self.store.list_messages(session_id, limit=conversation_turns_of(self.settings) + 1),
            scope_item_id=scope_item_id,
        )
        tracer.metadata["related_to_plan"] = related
        tracer.metadata["answer_source"] = source or "none"
        if plan is not None and not related and source != "refused" and "does not contain" not in answer:
            answer += "\n\nI can add this to the itinerary if you want."
        self.store.add_message(
            Message(
                session_id=session_id,
                role="assistant",
                content=answer,
                intent=Intent.ASK,
                meta={"source": source, "related_to_plan": related},
            )
        )
        for chunk in _chunks(answer):
            yield TurnEvent(event="token", data={"text": chunk})
        self.store.touch_session(session_id)

    async def _clarify(
        self,
        *,
        message,
        context,
        decision,
        session_id,
        tracer,
        request_id,
        history: Sequence[Message] = (),
        plan=None,
        scope_item_id: str | None = None,
        suggestions: list[tuple[str, str, str]] | None = None,
        state: TurnState | None = None,
    ) -> AsyncIterator[TurnEvent]:
        # With a plan, an unclear message is asked about that plan and the stop selected on it.
        question, slots = await self.clarifier.ask(
            message=message, context=context, decision=decision, history=history, plan=plan, scope_item_id=scope_item_id
        )
        if state is not None:
            # The clarification gate (robustness step 5): one question, held on the turn, asked before any plan.
            state.record_clarification(question, slots)
        meta: dict[str, Any] = {"clarify": True, "slots": slots}
        if plan is not None and not slots:
            # The question is about what to change ("Would you like a sushi restaurant near X, Y, or Z?"): the
            # answer on the next turn completes the message that was asked about, so it is kept the way an
            # edit's own question keeps its request.
            meta["edit_question"] = True
            meta["fact"] = "intent"
            meta["request"] = message[:600]
        if suggestions:
            meta["place_suggestions"] = [
                {"slot": slot, "asked": asked, "name": found} for slot, asked, found in suggestions
            ]
        self.store.add_message(
            Message(
                session_id=session_id,
                role="assistant",
                content=question,
                intent=Intent.UNCLEAR,
                meta=meta,
            )
        )
        yield TurnEvent(
            event="clarify",
            data={
                "question": question,
                "slots": slots,
                "labels": [SLOT_LABELS.get(slot, slot) for slot in slots],
                "options": _slot_options(slots, context, self._budget_floor(context.currency)),
                # With no missing slots the question is about *intent*, so offer
                # real messages instead of a Continue button that re-sends the
                # same unclear request.
                "choices": [] if plan is not None and not slots else _clarify_choices(context, slots),
                "reason_code": decision.reason_code,
                **(
                    {
                        "suggestions": [
                            {"slot": slot, "asked": asked, "name": found}
                            for slot, asked, found in suggestions
                        ]
                    }
                    if suggestions
                    else {}
                ),
            },
        )
        yield TurnEvent(event="token", data={"text": question})

    def _ask_for_fact(
        self, asked: EditQuestion, *, session_id: str, reply_meta: dict[str, Any] | None = None
    ) -> Iterator[TurnEvent]:
        """An edit's question for a missing fact, asked as the clarify step asks: a stored turn and a clarify card. The
        stored turn keeps the edit it belongs to, so the answer on the next turn completes that edit."""
        self.store.add_message(
            Message(
                session_id=session_id,
                role="assistant",
                content=asked.question,
                intent=Intent.UNCLEAR,
                meta={"edit_question": True, "fact": asked.fact, "request": asked.request[:600], **(reply_meta or {})},
            )
        )
        yield TurnEvent(
            event="clarify",
            data={
                "question": asked.question,
                "slots": [],
                "labels": [],
                "options": {},
                "choices": [],
                "reason_code": "edit_needs_fact",
            },
        )
        yield TurnEvent(event="token", data={"text": asked.question})

    def _budget_floor(self, currency: str) -> float:
        """The least a budget should allow per traveller per night, in ``currency``."""
        return floor_per_person_night(self.settings, getattr(self.plan_path.planner, "fx", None), currency)

    def _ask_before_planning(
        self, asked: PrePlanQuestion, *, context: TripContext, session_id: str
    ) -> Iterator[TurnEvent]:
        """The check before planning asks its question as the clarify step does: a stored turn and a clarify card."""
        self.store.add_message(
            Message(session_id=session_id, role="assistant", content=asked.question, intent=Intent.UNCLEAR)
        )
        yield TurnEvent(
            event="clarify",
            data={
                "question": asked.question,
                "slots": asked.slots,
                "labels": [SLOT_LABELS.get(slot, slot) for slot in asked.slots],
                "options": asked.options,
                "choices": [],
                "reason_code": asked.reason_code,
            },
        )
        yield TurnEvent(event="token", data={"text": asked.question})


def _same_place(first: str, second: str) -> bool:
    """The saved plan and the new trip are for one place."""
    return same_destination(first, second)


def explain_lost_adds(
    diff: list[dict[str, Any]], present_ids: set[str], fresh_notes: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """An add the airport transfer then removed is not 'nothing changed'.

    The diff was taken before the transfer. The stop is gone, so the reply uses the
    note that says when the traveller can actually be there.
    """
    lost = [
        entry
        for entry in diff
        if entry.get("kind") == "added" and entry.get("item_id") not in present_ids
    ]
    if not lost:
        return diff, []
    kept = [entry for entry in diff if entry not in lost]
    reasons = list(fresh_notes)
    if not reasons:
        reasons = [
            f"{entry.get('name') or 'The stop'} was found, then left out because of the flight, so it is not on the day."
            for entry in lost
        ]
    return kept, reasons


def _describe_diff(diff: list[dict[str, Any]], plan) -> str:
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    extras: list[str] = []
    hotel: str = ""
    moved: list[str] = []
    # "for the new dates" only when the dates moved: a budget or party change keeps them.
    for_new_dates = " for the new dates" if any(entry.get("kind") == "dates" for entry in diff) else ""
    for entry in diff:
        kind = entry.get("kind")
        if kind in {"added", "changed", "removed"}:
            item_id = entry.get("item_id")
            found = plan.find_item(item_id) if item_id else None
            if kind == "added" and found:
                day, item = found
                day_index = plan.days.index(day) + 1
                added.append(f"{item.name} on Day {day_index} at {item.start}")
            elif kind == "changed":
                before = str(entry.get("from") or "").strip()
                after = str(entry.get("name") or (found[1].name if found else "an item"))
                if before and before != after:
                    changed.append(f"{before} with {after}")
                else:
                    changed.append(after)
            elif kind == "removed":
                removed.append(str(entry.get("name") or "an item"))
        elif kind == "dates":
            moved.append(f"The trip dates changed from {entry.get('from')} to {entry.get('to')}.")
        elif kind == "stay" and not any(other.get("kind") == "dates" for other in diff):
            # On a plain move the stay follows the trip, and the sentence above says so.
            moved.append(f"The stay at {entry.get('name')} now runs {entry.get('to')}.")
        elif kind == "party":
            moved.append(
                f"The party changed from {entry.get('from')} to {entry.get('to')} traveller"
                f"{'' if entry.get('to') == 1 else 's'}."
            )
        elif kind == "origin":
            moved.append(
                f"The departure city changed from {entry.get('from') or 'none'} to {entry.get('to') or 'none'}."
            )
        elif kind == "budget":
            moved.append(f"The budget changed from {entry.get('from')} to {entry.get('to')}.")
        elif kind == "hotel_price":
            moved.append(
                f"The nightly rate at {entry.get('name')} changed from {entry.get('from')} to {entry.get('to')}."
            )
        elif kind == "weather":
            moved.append(
                "The forecast was updated."
                if any(day.weather for day in plan.days)
                else "The forecast was removed: it was for the earlier dates."
            )
        elif kind == "hotel":
            was, now = entry.get("from"), entry.get("to")
            if was and not now:
                why = (
                    "a one-day trip has no overnight stay"
                    if len(plan.days) <= 1
                    else f"no hotel{for_new_dates} came back"
                )
                hotel = f"The hotel {was} was removed: {why}."
            elif now and not was:
                hotel = f"A hotel was added: {now}."
            else:
                hotel = f"Hotel changed from {was} to {now}."
                if plan.hotels and plan.first_stay.name == now and plan.first_stay.price_per_night is None:
                    hotel += " Its price was not returned, so the total does not include the stay."
        elif kind == "flight":
            was, now = entry.get("from"), entry.get("to")
            if was and not now:
                extras.append(f"The flight {was} was removed: no flight{for_new_dates} came back.")
            elif now and not was:
                extras.append(f"A flight was added: {now}.")
            else:
                extras.append(f"Flight changed from {was} to {now}.")
        elif kind == "cost":
            try:
                before = float(entry.get("from") or 0)
                after = float(entry.get("to") or 0)
                # Each total in its own currency: a trip priced in USD that the form moved to HKD did not get dearer.
                was = entry.get("from_currency") or entry.get("currency", "")
                extras.append(
                    f"Total cost changed from {was} {before:,.0f} to "
                    f"{entry.get('currency', '')} {after:,.0f} ({entry.get('status', 'unknown')})."
                )
            except (TypeError, ValueError):
                extras.append("The cost breakdown changed.")
    parts: list[str] = list(moved)
    if added:
        parts.append("Added " + "; ".join(added) + ".")
    if changed:
        parts.append("Replaced " + "; ".join(changed) + ".")
    if removed:
        parts.append("Removed " + ", ".join(removed) + ".")
    if hotel:
        parts.append(hotel)
    parts.extend(extras)
    return " ".join(parts) if parts else "No itinerary items needed to change."


async def _settle(task: asyncio.Task) -> None:
    """Cancel a path task the turn stopped waiting for (deadline, disconnect) and let it unwind.

    A no-op when the task already finished. A path that has not stopped after the grace period is
    left to end on its own, so it cannot keep the trip's lock; what it returns is never used.
    """
    if not task.done():
        task.cancel()
        await asyncio.wait({task}, timeout=SETTLE_GRACE_SECONDS)
        if not task.done():
            logger.warning(
                "a path did not stop within %.0f s of cancellation; the turn ends without it", SETTLE_GRACE_SECONDS
            )
            task.add_done_callback(_mark_seen)
            return
    _mark_seen(task)


def _mark_seen(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()  # mark any failure as seen; the turn already reported or abandoned it


async def _drain(
    queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    task: asyncio.Task,
) -> AsyncIterator[TurnEvent]:
    while True:
        if task.done() and queue.empty():
            break
        try:
            event_name, data = await asyncio.wait_for(queue.get(), timeout=0.15)
        except asyncio.TimeoutError:
            continue
        yield TurnEvent(event=event_name, data=data)


_AFFIRMS = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|ok|okay|sure|correct|that one|that(?:'s| is) the one)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _stated_slots(message: str) -> dict[str, str]:
    """Slot values in a clarify reply such as ``destination: Kyoto``."""
    text = (message or "").strip()
    if "—" in text:
        text = text.split("—", 1)[1]
    found: dict[str, str] = {}
    for part in text.split(";"):
        if ":" not in part:
            continue
        slot, value = part.split(":", 1)
        key = slot.strip().lower().replace(" ", "_")
        if key:
            found[key] = value.strip().strip(".")
    return found


def _confirms_pending(message: str, pending: dict[str, str]) -> bool:
    """A yes, or a reply that repeats the suggested place for that slot."""
    if _AFFIRMS.match(message or ""):
        return True
    stated = _stated_slots(message).get(pending.get("slot", ""), "")
    name = pending.get("name", "").strip()
    return bool(stated and name) and stated.casefold() == name.casefold()


def _pending_places(history: Sequence[Message]) -> list[dict[str, str]]:
    """The places the previous turn asked the traveller to confirm."""
    rows = list(history)
    if rows and rows[-1].role == "user":
        rows = rows[:-1]
    if not rows or rows[-1].role != "assistant":
        return []
    raw = (rows[-1].meta or {}).get("place_suggestions")
    if not isinstance(raw, list):
        return []
    found: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        slot = str(item.get("slot") or "")
        name = str(item.get("name") or "").strip()
        if slot in {"destination", "origin"} and name:
            found.append({"slot": slot, "name": name})
    return found


def _pending_clarify(history: Sequence[Message]) -> list[str]:
    """Slots the previous assistant turn asked for. The current user message is ignored."""
    rows = list(history)
    if rows and rows[-1].role == "user":
        rows = rows[:-1]
    if not rows or rows[-1].role != "assistant":
        return []
    meta = rows[-1].meta or {}
    slots = meta.get("slots")
    if meta.get("clarify") and isinstance(slots, list):
        return [str(slot) for slot in slots]
    return []


def _fill_origin_answer(context: TripContext, slots: list[str], message: str) -> bool:
    """A short city name answers an open origin question and does not name a new trip.

    A correction ("no, I mean Gundam") is not a departure city. Only a city the
    assistant already knows is stored, so an attraction name cannot become the origin.
    """
    from app.application.understanding import _known_place

    if "origin" not in slots or context.origin.strip() or not context.destination.strip():
        return False
    text = message.strip()
    if not text or len(text) > 40 or looks_like_question(text):
        return False
    if re.search(r"\b(plan|itinerary|days?|nights?|budget|mean|no+|not|actually)\b", text, re.IGNORECASE):
        return False
    name = re.sub(r"^(?:from|i(?:'m| am) from)\s+", "", text, flags=re.IGNORECASE).strip(" .")
    known = _known_place(name) if name else None
    if not known:
        return False
    context.origin = known
    return True


def _answers_pending_question(pending: EditQuestion, message: str, plan: Any) -> bool:
    """True when a short reply names an option the pending question already offered, or a stop on the plan."""
    text = " ".join(message.casefold().strip(" \t?.!,").split())
    if len(text) < 3 or len(text) > 60 or _imperative_edit(message):
        return False
    if text in pending.question.casefold():
        return True
    if plan is None:
        return False
    return any(
        item.name.strip() and re.search(rf"\b{re.escape(item.name.casefold())}\b", text) is not None
        for day in plan.days
        for item in day.items
    )


def _pending_edit_question(history: Sequence[Message]) -> EditQuestion | None:
    """The question an edit asked on the turn before this one, when this message is the next turn, else None."""
    rows = list(history)
    if rows and rows[-1].role == "user":
        rows = rows[:-1]
    if not rows or rows[-1].role != "assistant" or not (rows[-1].meta or {}).get("edit_question"):
        return None
    meta = rows[-1].meta
    return EditQuestion(rows[-1].content, fact=str(meta.get("fact") or ""), request=str(meta.get("request") or ""))


def _answers_pending_question(pending: EditQuestion, message: str, plan: TripPlan | None) -> bool:
    """True when the message picks an option the pending question named, or names a stop of the plan: "Kiss
    Bridge" after "Would you like a sushi restaurant near Sunworld, Sunset Town, or Kiss Bridge?". The router
    reads such a short answer as a chat of its own, so it is matched here, before routing. A longer message, or
    one that asks for a change, is a request of its own and routes as one; the pending question is then dropped,
    as it always was."""
    text = " ".join(message.casefold().strip(" \t?.!,").split())
    if len(text) < 3 or len(text) > 60 or _imperative_edit(message):
        return False
    if text in pending.question.casefold():
        return True
    if plan is None:
        return False
    return any(
        item.name.strip() and re.search(rf"\b{re.escape(item.name.casefold())}\b", text) is not None
        for day in plan.days
        for item in day.items
    )


def _scope_line(metadata: dict[str, Any]) -> str:
    """What the edit could change, from the scope it really used; "" when no edit ran or nothing narrows it."""
    scope = metadata.get("edit_scope")
    selected = bool(metadata.get("edit_selected"))
    day = metadata.get("edit_day")
    if scope == "stop":
        if selected:
            return "A stop was selected, so only that stop could change."
        return "The message named one stop, so only that stop could change."
    if scope == "day" and day:
        if selected:
            return f"A stop on Day {day} was selected and the message asked for more stops, so only Day {day} could change."
        return f"The message asked for more stops on Day {day}, so only Day {day} could change."
    if scope == "plan" and metadata.get("reason") == "scoped_item_action":
        return "A stop was selected, but the request was about the whole plan, so the edit was not limited to that stop."
    return ""


def _decision_has_text(decision: Any) -> bool:
    """True when a saved decision still has the why-line or a model call to show."""
    if not isinstance(decision, dict):
        return False
    if any(str(line).strip() for line in decision.get("why") or []):
        return True
    for call in decision.get("generations") or []:
        if not isinstance(call, dict):
            continue
        if any(str(call.get(key) or "").strip() for key in ("purpose", "reasoning", "output_preview", "user_preview")):
            return True
    return False


def restore_message_decisions(store: Any, trace_dir: Path, messages: list[Message]) -> list[Message]:
    """Fill replies whose decision was never kept, from this trip's trace files.

    An empty decision is treated as missing. Writing one would hide the trace for good, and a
    reload would have only the "no separate reasoning" line.
    """
    if not messages or not any(item.role == "assistant" and not _decision_has_text((item.meta or {}).get("decision")) for item in messages):
        return messages
    if not trace_dir.is_dir():
        return messages
    session_id = messages[0].session_id
    traces: list[tuple[str, str, dict[str, Any]]] = []
    for path in trace_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta = record.get("metadata") or {}
        if meta.get("session_id") != session_id:
            continue
        tracer = SimpleNamespace(
            metadata=meta,
            searches=record.get("searches") or [],
            generations=record.get("generations") or [],
        )
        traces.append(
            (
                str(record.get("ended_at") or record.get("started_at") or ""),
                str(meta.get("message") or ""),
                _decision_payload(tracer),
            )
        )
    traces.sort(key=lambda item: item[0])
    restored = list(messages)
    cursor = 0
    index = 0
    while index < len(restored):
        if restored[index].role != "user":
            index += 1
            continue
        asked = restored[index].content
        reply = index + 1
        while reply < len(restored) and restored[reply].role == "user":
            reply += 1
        if reply >= len(restored) or restored[reply].role != "assistant":
            index = reply
            continue
        found = next((cursor_index for cursor_index in range(cursor, len(traces)) if traces[cursor_index][1] == asked), None)
        if found is not None and not _decision_has_text((restored[reply].meta or {}).get("decision")):
            meta = {**restored[reply].meta, "decision": traces[found][2]}
            store.set_message_meta(restored[reply].id, meta)
            restored[reply] = restored[reply].model_copy(update={"meta": meta})
        if found is not None:
            cursor = found + 1
        index = reply + 1
    return restored


def _decision_payload(tracer: Any) -> dict[str, Any]:
    """What an admin can open under the reply. Kept on the message, not only in the live stream."""
    generations = []
    for generation in getattr(tracer, "generations", []) or []:
        generations.append(
            {
                "purpose": str(generation.get("purpose") or ""),
                "model": str(generation.get("model") or ""),
                "latency_ms": int(generation.get("latency_ms") or 0),
                "reasoning": str(generation.get("reasoning") or ""),
                "output_preview": str(generation.get("output_preview") or "")[:4000],
                "user_preview": str(generation.get("user_preview") or "")[:8000],
            }
        )
    meta = getattr(tracer, "metadata", {}) or {}
    return {
        "why": _why_lines(tracer),
        "flow": list(meta.get("flow") or []),
        "path": meta.get("path") or None,
        "generations": generations,
        "steps": list(meta.get("turn_steps") or []),
    }


_PATH_REASONS = {
    "plan_shape": "The message asked to reshape the whole plan, so this was an edit, not a new trip.",
    "replan_dates": "The trip details held other dates than the plan, so the trip was moved, not planned again.",
    "replan_destination": "The trip details named another place than the plan is for, so a new trip was planned.",
    "scoped_item_action": "A stop was selected, so only that stop could change.",
    "scoped_item_question": "The message was a question about the selected stop, so nothing was edited.",
    "not_about_the_trip": "The message is not about the trip, so it was answered in chat and nothing was edited.",
    "question_before_intake": "The traveller asked a question while some trip details are still missing, so the question was answered and the trip was not planned.",
    "security_blocklist": "The message was blocked before any planning.",
    "identity": "This was a question about the assistant, so no trip was planned.",
    "finalized": "This plan is final, so nothing was planned or edited.",
    "edit_needs_fact": "The change needs a fact the plan does not have, so it was asked for and nothing was edited.",
    "edit_answer": "The message answered the question the previous edit asked, so that edit went ahead.",
    "plan_from_file": "The message asked to plan from the shared page or file, so this was a new trip.",
    "route_change": "The message named a city on the route, so the route was edited.",
    "meal_swap": "A food word check treated this as an edit of a meal.",
    "item_swap": "A word check treated this as an edit of a stop.",
    "meal_suggestion": "A food word check kept this as an edit even though the router was unsure.",
}


def _chooser(decision: RouteDecision) -> str:
    """Who picked this decision: a word check, Jev, or the writer model."""
    rationale = decision.rationale or ""
    code = decision.reason_code or ""
    if rationale == LANGUAGE_RULES or code in {"set_language", "unsupported_language", "refresh", "chinese_input"}:
        return "a language word check, before any model"
    if code == "question_before_intake" or "remaining details" in rationale:
        return "a word check, before Jev"
    if rationale == RULES_FIRST:
        return "word rules (Jev was not asked)"
    if rationale == "rule-based fallback":
        return "word rules (no model answered)"
    if code.startswith("below_threshold_"):
        who = code.removeprefix("below_threshold_")
        name = "Jev" if who == "jev" else f"the {who} model"
        return f"{name}, under the confidence bar"
    if rationale == "system one decision" or code.startswith("jev_"):
        return "Jev"
    if code in _PATH_REASONS:
        return "a word check"
    if rationale:
        return "the writer model"
    return "a word check"


def _route_label(decision: RouteDecision, gate_model: str) -> str:
    """The name shown on the routing step. Rules that skip Jev are not labelled as Jev."""
    who = _chooser(decision)
    if who.startswith("word rules") or who.startswith("a word check") or who.startswith("a language"):
        return "rules"
    if who.startswith("the writer"):
        return "writer"
    return gate_model


def _intake_line(intake: Any) -> str:
    """What the link step did, before the message was routed."""
    parts: list[str] = []
    for source in getattr(intake, "sources", []) or []:
        if getattr(source, "status", "") == "read":
            parts.append(f"read the {getattr(source, 'kind', 'page')}")
        else:
            parts.append(getattr(source, "note", "") or "the link could not be read")
    places = ", ".join(str(place) for place in (getattr(intake, "places", []) or [])[:6])
    sentence = "Link intake: " + ("; ".join(parts[:4]) or "nothing was opened")
    if places:
        return sentence + f". Places kept as must-see: {places}."
    return sentence + ". No place name was kept from the page."


def _understand_line(meta: dict[str, Any]) -> str:
    for step in meta.get("turn_steps") or []:
        if not isinstance(step, dict) or step.get("id") != "understand":
            continue
        note = str(step.get("note") or "").strip()
        model = str(step.get("model") or "rules")
        if model == "rules":
            return f"Understand: Python word rules only. The understand model was not asked. {note}".strip()
        return f"Understand: the model {model} filled what the rules left empty. {note}".strip()
    return ""


def _explain_path(
    routed: RouteDecision | None, final: RouteDecision, notes: list[str]
) -> tuple[dict[str, Any], list[str]]:
    changed = bool(
        routed is not None and (routed.intent != final.intent or routed.reason_code != final.reason_code)
    )
    path = {
        "intent": final.intent.value,
        "reason": final.reason_code,
        "confidence": round(float(final.confidence or 0), 3),
        "chooser": "a word check" if changed else _chooser(routed or final),
        "router": _chooser(routed) if routed is not None else "",
        "router_intent": (routed or final).intent.value,
        "router_reason": (routed or final).reason_code,
        "changed_by": final.reason_code if changed else "",
    }
    flow = [line for line in notes if line]
    if routed is None:
        flow.append(f"Path {final.intent.value}: {_chooser(final)} ({final.reason_code}).")
        sentence = _PATH_REASONS.get(final.reason_code)
        if sentence:
            flow.append(sentence)
    else:
        flow.append(
            f"Router: {_chooser(routed)} chose {routed.intent.value} "
            f"({routed.reason_code}, confidence {routed.confidence:.2f})."
        )
        if changed:
            sentence = _PATH_REASONS.get(final.reason_code)
            flow.append(
                sentence
                or f"A later word check changed the path to {final.intent.value} ({final.reason_code})."
            )
    return path, flow


def _store_path(tracer: Any, routed: RouteDecision | None, final: RouteDecision) -> None:
    """Remember who chose the path, and the checks that ran before it, for the download log."""
    meta = getattr(tracer, "metadata", None)
    if not isinstance(meta, dict):
        return
    notes = [str(line).strip() for line in meta.get("flow_notes") or [] if str(line).strip()]
    understand = _understand_line(meta)
    if understand:
        notes.append(understand)
    path, flow = _explain_path(routed, final, notes)
    meta["path"] = path
    meta["flow"] = flow
    meta["intent"] = final.intent.value
    meta["reason"] = final.reason_code
    meta["confidence"] = final.confidence


def _why_lines(tracer: Any) -> list[str]:
    """A traveller-facing account of the decision. Not the model's private draft."""
    meta = getattr(tracer, "metadata", {}) or {}
    reason = str(meta.get("reason") or "")
    intent = str(meta.get("intent") or "")
    scoped = _scope_line(meta)
    lines: list[str] = []
    for line in meta.get("flow") or []:
        text = str(line).strip()
        if text and text not in lines:
            lines.append(text)
    explained = bool(lines)
    if scoped and scoped not in lines:
        lines.append(scoped)
    if not explained:
        if scoped:
            pass
        elif reason in _PATH_REASONS:
            lines.append(_PATH_REASONS[reason])
        elif intent:
            lines.append(f"Routed as {intent}" + (f" ({reason})." if reason else "."))
    meta = getattr(tracer, "metadata", {}) or {}
    if "related_to_plan" in meta:
        source = str(meta.get("answer_source") or "the conversation")
        if meta.get("related_to_plan"):
            lines.append(f"The reply was written from the plan, using {source}. Nothing was edited.")
        else:
            lines.append(
                f"The reply was written as chat, not from a stop on the plan, using {source}. Nothing was edited."
            )
    for line in (getattr(tracer, "metadata", {}) or {}).get("review_lines") or []:
        text = str(line).strip()
        if text and text not in lines:
            lines.append(text)
    for search in getattr(tracer, "searches", []) or []:
        results = search.get("results") or []
        kept = [row.get("title") or row.get("url") for row in results if row.get("kept") is True and (row.get("title") or row.get("url"))]
        dropped = [row.get("title") or row.get("url") for row in results if row.get("kept") is False and (row.get("title") or row.get("url"))]
        sentence = f"Searched \"{search.get('query', '')}\" via {search.get('provider')}."
        if kept:
            sentence += " Kept " + ", ".join(str(item) for item in kept[:4]) + "."
        if dropped:
            sentence += " Dropped " + ", ".join(str(item) for item in dropped[:4]) + " because the page was not about this city or this question."
        lines.append(sentence)
    for line in (getattr(tracer, "metadata", {}) or {}).get("edit_steps") or []:
        text = str(line).strip()
        if text and text not in lines:
            lines.append(text)
    return lines[:20]


def _note_understand(tracer: Any, before: Any, after: Any, grounding: PlaceGrounding, message: str = "") -> None:
    """Record what Understand read, before routing, so the log lists it first."""
    generations = getattr(tracer, "generations", []) or []
    call = next(
        (item for item in reversed(generations) if str(item.get("purpose") or "") == "understand"),
        None,
    )
    latency = int((call or {}).get("latency_ms") or 0)
    note_turn_step(
        {
            "id": "understand",
            "phase": "understand",
            "model": str((call or {}).get("model") or "rules"),
            "status": "done",
            "seconds": round(latency / 1000, 1) if latency else None,
            "note": understand_note(before, after, grounding, message),
        }
    )


def _step(step_id: str, phase: str, model: str, *, status: str, seconds: float | None = None) -> TurnEvent:
    data: dict[str, Any] = {"id": step_id, "phase": phase, "model": model, "status": status}
    if seconds is not None:
        data["seconds"] = seconds
    note_turn_step(data)
    return TurnEvent(event="step", data=data)


def _route_data(decision: RouteDecision, settings: Settings) -> dict[str, Any]:
    return {
        "intent": decision.intent.value,
        "confidence": round(decision.confidence, 3),
        "threshold": settings.router_threshold,
        "need_clarification": decision.need_clarification,
        "reason_code": decision.reason_code,
        "affected_parts": decision.affected_parts,
        "missing_slots": decision.missing_slots,
        "related_to_plan": decision.related_to_plan,
        **({"slots": decision.slots} if decision.slots else {}),
    }


def _clarify_choices(context, slots: list[str]) -> list[dict[str, str]]:
    """Concrete next steps when the clarify is about intent, not missing data."""
    if slots:
        return []
    destination = (getattr(context, "destination", "") or "").strip()
    origin = (getattr(context, "origin", "") or "").strip()
    if destination and origin:
        plan_message = f"Plan a {getattr(context, 'days', None) or 3}-day trip to {destination} from {origin}."
    elif destination:
        plan_message = f"Plan a trip to {destination} with the details I already gave."
    else:
        plan_message = "Plan a 3-day trip to Kyoto from Hong Kong for 2 people, budget 400000 JPY."
    return [
        {"label": "Plan my trip", "message": plan_message},
        {"label": "Make it cheaper", "message": "Make it cheaper."},
        {"label": "What is on day 1?", "message": "What is on day 1?"},
        {"label": "What changed?", "message": "What changed so far?"},
    ]


def _slot_options(slots: list[str], context: TripContext, floor: float) -> dict[str, list[str]]:
    """The clarify card's choices per slot. The budget's are sized to the trip's nights and travellers."""
    options: dict[str, list[str]] = {}
    for slot in slots:
        if slot == "party_size":
            options[slot] = ["1", "2", "3", "4"]
        elif slot in {"dates", "start_date", "end_date"}:
            options[slot] = ["This weekend", "Next month", "In three months"]
        elif slot == "budget":
            options[slot] = budget_options(context, floor)
        elif slot == "destination":
            options[slot] = ["Kyoto", "Tokyo", "Hong Kong"]
        elif slot == "origin":
            options[slot] = ["Hong Kong", "Singapore", "London"]
    return options


def _chunks(text: str, size: int = 4) -> list[str]:
    words = text.split(" ")
    chunks: list[str] = []
    for index in range(0, len(words), size):
        chunk = " ".join(words[index : index + size])
        chunks.append(chunk + (" " if index + size < len(words) else ""))
    return chunks or [text]
