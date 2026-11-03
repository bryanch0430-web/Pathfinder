"""One user turn, one path. The orchestrator is the only place that knows the
full sequence: screen -> understand -> route -> gate -> plan/modify/ask/clarify.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import aclosing, asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Callable, Iterator

import httpx

from app.application.asking import AskPath
from app.application.persona import identity_reply
from app.application.clarification import Clarifier, SLOT_LABELS
from app.application.freshness import LABELS as FRESHNESS_LABELS
from app.application.freshness import FreshnessService
from app.application.language import chinese_input_reply, refresh_reply, set_language_reply, unsupported_reply
from app.application.llm_gateway import LLMGateway
from app.application.memory import MemoryService
from app.application.modification import ModifyPath, plan_shape
from app.application.planning import PlanPath
from app.application.router import MODIFY_VERBS, System1Router, language_route, looks_like_question
from app.application.toolbox import Toolbox
from app.application.understanding import understand
from app.config import Settings
from app.domain.errors import (
    CanaryLeak,
    PathfinderError,
    PlanInvalidError,
    ProviderError,
    SecurityBlocked,
    ToolBudgetExceeded,
    operator_text,
)
from app.domain.models import (
    Intent,
    Message,
    RouteDecision,
    TripContext,
    TurnEvent,
    new_id,
    utcnow,
)
from app.infrastructure.observability import bind_developer_view, bind_live_emit, reset_developer_view, reset_live_emit, start_trace
from app.infrastructure.store import PlanConflict, SQLiteStore
from app.security import SAFE_REPLY, screen_message

logger = logging.getLogger(__name__)

#: What a traveller is told when a data source fails or a turn crashes. Exception text is never
#: shown: it can hold a source's error page, a URL with a key or a file path. It stays in the trace.
SOURCE_FAILED = "A data source failed. Your plan was not changed."
TURN_FAILED = "Something went wrong while handling this turn. You can retry."
PLAN_CONFLICT = "Another change to this trip was still running. Please retry."
#: How long a turn waits for a cancelled path to unwind before it goes on without it.
SETTLE_GRACE_SECONDS = 3.0
TOOL_LIMIT = "This request reached its limit of data lookups and was stopped. Please ask for a smaller change."
BLOCKED = "This request was stopped by a safety check."


def user_facing_error(exc: BaseException) -> str:
    """The only error text a traveller is shown for a failed turn."""
    if isinstance(exc, PlanConflict):
        return PLAN_CONFLICT
    # Neither is a source failing: one is our own cap, the other names a tool and a path.
    if isinstance(exc, ToolBudgetExceeded):
        return TOOL_LIMIT
    if isinstance(exc, SecurityBlocked):
        return BLOCKED
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
        self.freshness = freshness or FreshnessService(
            registry=plan_path.registry, planner=plan_path.planner, settings=settings, store=store
        )
        # Reopening a trip must not save a refresh over the work of a turn that is running on it.
        self.freshness.session_busy = self.session_busy

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
                yield
        finally:
            self._session_turns[session_id] -= 1
            if not self._session_turns[session_id]:
                del self._session_turns[session_id]
                del self._session_locks[session_id]
                del self._session_loops[session_id]

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
        on_wait: Callable[[], None] | None = None,
        on_start: Callable[[], None] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """One turn. Turns on the same session run one after another: the second reads the
        plan the first saved, so neither overwrites the other's version. ``on_wait`` is called when
        this turn has to wait for earlier ones, ``on_start`` once it holds the lock and begins."""
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
                )
                async with aclosing(turn) as events:
                    async for event in events:
                        yield event

    async def _run_turn(
        self,
        *,
        session_id: str,
        message: str,
        locale: str = "en",
        scope_item_id: str | None = None,
        scope_label: str | None = None,
        developer: bool = False,
    ) -> AsyncIterator[TurnEvent]:
        request_id = new_id("req")
        tracer = start_trace(self.settings, request_id)
        developer_token = bind_developer_view(developer)
        tracer.metadata["message"] = message
        tracer.metadata["session_id"] = session_id
        if self.tracer_proxy is not None:
            self.tracer_proxy.bind(tracer)
        session = self.store.get_session(session_id)
        if session is None:
            session = self.store.create_session("")
        session_id = session.id
        screen = screen_message(message, self.settings)
        if screen.flagged:
            for category in screen.categories:
                self.store.log_injection(category, screen.max_severity.value, channel="user_message")
        self.store.add_message(
            Message(
                session_id=session_id,
                role="user",
                content=message,
                meta={
                    "locale": locale,
                    "flagged": screen.flagged,
                    "categories": screen.categories,
                    **({"scope": scope_label} if scope_label else {}),
                },
            )
        )

        if screen.blocked:
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=SAFE_REPLY, intent=Intent.UNCLEAR)
            )
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
            tracer.finish(status="blocked")
            yield TurnEvent(
                event="done",
                data={"request_id": request_id, "intent": "unclear", "session_id": session_id, "blocked": True},
            )
            return

        # Identity and small-talk questions are static facts about the assistant:
        # answer them here, with no model call, no web search and no latency.
        identity = identity_reply(message)
        if identity:
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=identity, intent=Intent.ASK)
            )
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
            tracer.finish(status="ok")
            yield TurnEvent(
                event="done",
                data={"request_id": request_id, "intent": "ask", "session_id": session_id},
            )
            return

        context = self.store.get_context(session_id) or TripContext(session_id=session_id, language=locale)
        read = context.model_copy(deep=True)
        context.language = locale or context.language
        plan = self.store.get_plan(session_id)
        intent: Intent = Intent.UNCLEAR
        closed = False
        try:
            # Display language, refresh and Chinese input are decided first: before
            # the selected stop, the understanding step and any model or tool.
            display = language_route(message, has_plan=plan is not None)
            if display is not None:
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

            with tracer.span("understand"):
                try:
                    context = await understand(message, context, self.gateway)
                except CanaryLeak:
                    raise
                except Exception:
                    pass
                context = self._save_changes(read, context)
                if context.preferences:
                    self._remember_in_background(session.owner, context)

            with tracer.span("route"):
                decision = await self.router.route(
                    message=message,
                    context=context,
                    plan_summary=plan.summary_for_prompt() if plan else None,
                    history=self.store.list_messages(session_id, limit=10),
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
                plan is not None
                and looks_like_question(message)
                and not re.search(MODIFY_VERBS, message.lower())
            )
            if plan is not None and plan_shape(message) and not re.search(
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
            intent = decision.intent
            tracer.metadata.update(
                {
                    "intent": decision.intent.value,
                    "confidence": decision.confidence,
                    "reason": decision.reason_code,
                    "routes": "no_plan" if plan is None else f"plan_v{plan.version}",
                }
            )
            yield TurnEvent(event="route", data=_route_data(decision, self.settings))

            if decision.need_clarification or decision.intent == Intent.UNCLEAR:
                async for event in self._clarify(
                    message=message,
                    context=context,
                    decision=decision,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
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

            # A closed turn closes the path it is running here, in this task, so the path stops.
            if decision.intent == Intent.PLAN:
                path = self._plan(
                    context=context,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                )
                async with aclosing(path) as events:
                    async for event in events:
                        yield event
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
                ):
                    yield event
        except GeneratorExit:
            # The consumer closed the turn: nobody is left to receive the closing events.
            closed = True
            raise
        except CanaryLeak:
            self.store.log_injection("canary_leak", "high", channel="model_output")
            message_text = "I cannot return that answer. Ask the question again in your own words."
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=message_text, intent=intent)
            )
            yield TurnEvent(
                event="error",
                data={"code": "canary_leak", "message": message_text, "retryable": False},
            )
            tracer.finish(status="blocked")
        except PlanInvalidError as exc:
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=str(exc), intent=intent)
            )
            yield TurnEvent(event="error", data={"code": exc.code, "message": str(exc), "retryable": exc.retryable})
            tracer.finish(status="error")
        except PlanConflict as exc:
            _note_failure(tracer, exc)
            message_text = user_facing_error(exc)
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=message_text, intent=intent)
            )
            yield TurnEvent(event="error", data={"code": "plan_conflict", "message": message_text, "retryable": True})
            tracer.finish(status="error")
        except PathfinderError as exc:
            _note_failure(tracer, exc)
            message_text = user_facing_error(exc)
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=message_text, intent=intent)
            )
            yield TurnEvent(event="error", data={**exc.as_dict(), "message": message_text})
            tracer.finish(status="error")
        except Exception as exc:
            _note_failure(tracer, exc)
            message_text = user_facing_error(exc)
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=message_text, intent=intent)
            )
            yield TurnEvent(
                event="error",
                data={"code": "internal_error", "message": message_text, "retryable": True},
            )
            tracer.finish(status="error")
        finally:
            reset_developer_view(developer_token)
            tracer.finish(status="ok")
            if not closed:
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
                                        "user_preview": (generation.get("user_preview") or "")[:240],
                                    }
                                    if developer
                                    else {}
                                ),
                            }
                            for generation in tracer.generations
                        ],
                        "why": _why_lines(tracer),
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
            if plan is not None:
                try:
                    outcome = await self.freshness.check(
                        plan, context=context, tracer=tracer, request_id=request_id
                    )
                except Exception:  # a failed refresh keeps the earlier data
                    logger.exception("refreshing an outdated plan failed")
                    outcome = None
                if outcome is not None:
                    refreshed, outdated = outcome.refreshed, outcome.outdated
                    if refreshed:
                        yield TurnEvent(event="plan", data={"plan": outcome.plan.model_dump(mode="json"), "diff": []})
            reply = refresh_reply(
                locale, has_plan=plan is not None, refreshed=refreshed, outdated=outdated, labels=FRESHNESS_LABELS
            )
        else:
            reply = chinese_input_reply(locale)
        self.store.add_message(
            Message(session_id=session_id, role="assistant", content=reply, intent=decision.intent)
        )
        for chunk in _chunks(reply):
            yield TurnEvent(event="token", data={"text": chunk})

    async def _refresh_outdated(self, plan, context, tracer, request_id):
        try:
            outcome = await self.freshness.check(plan, context=context, tracer=tracer, request_id=request_id)
        except Exception:  # a failed refresh never stops the turn
            logger.exception("refreshing an outdated plan failed")
            return None
        return outcome.plan if outcome is not None else None

    def _save_changes(self, read: TripContext, changed: TripContext) -> TripContext:
        """Save what this turn changed in the trip's form on top of the form as it is stored now, so
        a field saved while the turn was working (the traveller's own edit of the form) is kept."""
        current = self.store.get_context(changed.session_id)
        if current is not None:
            for name in TripContext.model_fields:
                before, after = getattr(read, name), getattr(changed, name)
                if name == "session_id" or after == before:
                    continue
                if isinstance(after, list):  # a turn only adds preferences and constraints
                    kept = getattr(current, name)
                    after = [*kept, *(item for item in after if item not in before and item not in kept)]
                setattr(current, name, after)
            changed = current
        self.store.save_context(changed)
        return changed

    def _memory_owner(self, session_id: str) -> str:
        """Whose saved trips the planner may recall: the session's owner only."""
        session = self.store.get_session(session_id)
        return session.owner if session else ""

    async def _plan(self, *, context, session_id, tracer, request_id) -> AsyncIterator[TurnEvent]:
        matches = self.memory.recall(context, owner=self._memory_owner(session_id))
        if matches:
            tracer.metadata["memory_matches"] = [round(score, 3) for score, _ in matches]
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        def emit(event_name: str, data: dict[str, Any]) -> None:
            queue.put_nowait((event_name, data))

        toolbox = Toolbox(
            path="plan",
            tracer=tracer,
            store=self.store,
            request_id=request_id,
            max_calls=self.settings.max_tool_calls_per_turn or None,
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
            )
        )
        try:
            async for event in _drain(queue, task):
                yield event
            plan = await task
        finally:
            reset_live_emit(live)
            await _settle(task)
        self.store.save_plan(plan, plan.change_summary or "Initial plan")
        self.store.add_message(
            Message(session_id=session_id, role="assistant", content=plan.summary, intent=Intent.PLAN)
        )
        yield TurnEvent(event="plan", data={"plan": plan.model_dump(mode="json"), "diff": []})
        self.store.touch_session(session_id)

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
    ) -> AsyncIterator[TurnEvent]:
        if plan is None:
            raise PlanInvalidError("There is no itinerary to modify yet.")
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        def emit(event_name: str, data: dict[str, Any]) -> None:
            queue.put_nowait((event_name, data))

        toolbox = Toolbox(
            path="modify",
            tracer=tracer,
            store=self.store,
            request_id=request_id,
            max_calls=self.settings.max_tool_calls_per_turn or None,
        )
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
            )
        )
        try:
            async for event in _drain(queue, task):
                yield event
            updated, diff = await task
        finally:
            reset_live_emit(live)
            await _settle(task)
        self.store.save_plan(updated, updated.change_summary, replace=updated.version == plan.version)
        description = _describe_diff(diff, updated)
        if diff:
            prose = f"Updated to version {updated.version}. " + description + " Everything else is unchanged."
        else:
            # Nothing changed. Do not claim a version: say why, using the reason
            # the edit recorded (a data gap, an unmatched stop, a duplicate).
            prose = updated.change_summary or description or "Nothing in the plan matched that request."
        self.store.add_message(
            Message(
                session_id=session_id,
                role="assistant",
                content=prose,
                intent=Intent.MODIFY,
                meta={"diff": diff},
            )
        )
        yield TurnEvent(event="token", data={"text": prose})
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
    ) -> AsyncIterator[TurnEvent]:
        toolbox = Toolbox(
            path="ask",
            tracer=tracer,
            store=self.store,
            request_id=request_id,
            max_calls=self.settings.max_tool_calls_per_turn or None,
        )
        answer, related, source = await self.ask_path.answer(
            question=message,
            plan=plan,
            context=context,
            toolbox=toolbox,
            emit=None,
            history=self.store.list_messages(session_id, limit=8),
            scope_item_id=scope_item_id,
        )
        if plan is not None and not related and "does not contain" not in answer:
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

    async def _clarify(self, *, message, context, decision, session_id, tracer, request_id) -> AsyncIterator[TurnEvent]:
        question, slots = await self.clarifier.ask(message=message, context=context, decision=decision)
        self.store.add_message(
            Message(session_id=session_id, role="assistant", content=question, intent=Intent.UNCLEAR)
        )
        yield TurnEvent(
            event="clarify",
            data={
                "question": question,
                "slots": slots,
                "labels": [SLOT_LABELS.get(slot, slot) for slot in slots],
                "options": _slot_options(slots, context.currency),
                # With no missing slots the question is about *intent*, so offer
                # real messages instead of a Continue button that re-sends the
                # same unclear request.
                "choices": _clarify_choices(context, slots),
                "reason_code": decision.reason_code,
            },
        )
        yield TurnEvent(event="token", data={"text": question})


def _describe_diff(diff: list[dict[str, Any]], plan) -> str:
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    extras: list[str] = []
    hotel: str = ""
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
        elif kind == "hotel":
            hotel = f"Hotel changed from {entry.get('from') or 'the previous hotel'} to {entry.get('to') or 'a new hotel'}."
        elif kind == "flight":
            extras.append(f"Flight changed from {entry.get('from') or 'the previous option'} to {entry.get('to') or 'a new option'}.")
        elif kind == "cost":
            try:
                before = float(entry.get("from") or 0)
                after = float(entry.get("to") or 0)
                extras.append(
                    f"Total cost changed from {entry.get('currency', '')} {before:,.0f} to "
                    f"{entry.get('currency', '')} {after:,.0f} ({entry.get('status', 'unknown')})."
                )
            except (TypeError, ValueError):
                extras.append("The cost breakdown changed.")
    parts: list[str] = []
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
            logger.warning("a path did not stop within %.0f s of cancellation; the turn ends without it", SETTLE_GRACE_SECONDS)
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


def _why_lines(tracer: Any) -> list[str]:
    """A traveller-facing account of the decision. Not the model's private draft."""
    reason = str(getattr(tracer, "metadata", {}).get("reason") or "")
    intent = str(getattr(tracer, "metadata", {}).get("intent") or "")
    known = {
        "plan_shape": "The message asked to reshape the whole plan, so this was an edit, not a new trip.",
        "scoped_item_action": "A stop was selected, so only that stop could change.",
        "scoped_item_question": "The message was a question about the selected stop, so nothing was edited.",
        "security_blocklist": "The message was blocked before any planning.",
        "identity": "This was a question about the assistant, so no trip was planned.",
    }
    lines: list[str] = []
    if reason in known:
        lines.append(known[reason])
    elif intent:
        lines.append(f"Routed as {intent}" + (f" ({reason})." if reason else "."))
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
    return lines[:8]


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


def _slot_options(slots: list[str], currency: str) -> dict[str, list[str]]:
    options: dict[str, list[str]] = {}
    for slot in slots:
        if slot == "party_size":
            options[slot] = ["1", "2", "3", "4"]
        elif slot in {"dates", "start_date", "end_date"}:
            options[slot] = ["This weekend", "Next month", "In three months"]
        elif slot == "budget":
            options[slot] = [f"1000 {currency}", f"2500 {currency}", f"5000 {currency}"]
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
