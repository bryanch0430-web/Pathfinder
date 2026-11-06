"""One user turn, one path. The orchestrator is the only place that knows the
full sequence: screen -> understand -> route -> gate -> plan/modify/ask/clarify.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import aclosing, asynccontextmanager, contextmanager
from datetime import date
from typing import Any, AsyncIterator, Callable, Iterator, Sequence

import httpx

from app.application.asking import AskPath
from app.application.budget_check import PrePlanQuestion, budget_options, budget_question, floor_per_person_night
from app.application.persona import identity_reply
from app.application.clarification import Clarifier, SLOT_LABELS
from app.application.freshness import LABELS as FRESHNESS_LABELS
from app.application.freshness import FreshnessService
from app.application.language import chinese_input_reply, refresh_reply, set_language_reply, unsupported_reply
from app.application.llm_gateway import LLMGateway
from app.application.memory import MemoryService
from app.application.modification import EditQuestion, ModifyPath, plan_shape
from app.application.planning import PlanPath
from app.application.router import (
    MODIFY_VERBS,
    System1Router,
    language_route,
    looks_like_question,
    replans_new_dates,
    replans_new_destination,
    same_destination,
)
from app.application.toolbox import Toolbox
from app.application.understanding import understand
from app.application.validation import is_edit_outcome
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
from app.security import SAFE_REPLY, screen_turn

logger = logging.getLogger(__name__)

#: What a traveller is told when a data source fails or a turn crashes. Exception text is never
#: shown: it can hold a source's error page, a URL with a key or a file path. It stays in the trace.
SOURCE_FAILED = "A data source failed. Your plan was not changed."
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
        tracer.metadata["message"] = message
        tracer.metadata["session_id"] = session_id
        if self.tracer_proxy is not None:
            self.tracer_proxy.bind(tracer)
        screen = screen_turn(message, self.settings)
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
                    **({"severity": screen.max_severity.value, "blocked": screen.blocked} if screen.flagged else {}),
                    **({"scope": scope_label} if scope_label else {}),
                },
            )
        )

        if screen.blocked:
            with _early_exit(tracer, developer_token, "blocked"):
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
            yield TurnEvent(
                event="done",
                data={"request_id": request_id, "intent": "unclear", "session_id": session_id, "blocked": True},
            )
            return

        # Identity and small-talk questions are static facts about the assistant:
        # answer them here, with no model call, no web search and no latency.
        identity = identity_reply(message)
        if identity:
            with _early_exit(tracer, developer_token, "ok"):
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
            yield TurnEvent(
                event="done",
                data={"request_id": request_id, "intent": "ask", "session_id": session_id},
            )
            return

        context = self.store.get_context(session_id) or TripContext(session_id=session_id, language=locale)
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

            # The stored turns, this one last, read once: the router and every step that acts on the turn see the
            # recent conversation from them (the steps through `conversation_block`, without this message).
            history = self.store.list_messages(session_id, limit=10)
            with tracer.span("understand"):
                try:
                    context = await understand(message, context, self.gateway, history=history)
                except CanaryLeak:
                    raise
                except Exception:
                    pass
                context = self._save_changes(as_read, context)
                if context.preferences:
                    # The owner as it is now: signing in may have moved the trip while the message was understood.
                    self._remember_in_background(self._memory_owner(session_id), context)

            with tracer.span("route"):
                decision = await self.router.route(
                    message=message,
                    context=context,
                    plan_summary=plan.summary_for_prompt() if plan else None,
                    history=history,
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
            if plan is not None and replans_new_destination(message, context, plan.summary_for_prompt()):
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
            # The message may answer the question an edit asked on the turn before ("Where do you live?", then
            # "Mong Kok"): a bare answer the router is unsure of goes back to that edit, which reads it with its request.
            answering = _pending_edit_question(history) if plan is not None else None
            if (
                answering is not None
                and not looks_like_question(message)
                and decision.reason_code not in {"replan_destination", "replan_dates"}
                and (decision.need_clarification or decision.intent in {Intent.UNCLEAR, Intent.MODIFY})
            ):
                decision = decision.model_copy(
                    update={
                        "intent": Intent.MODIFY,
                        "confidence": max(decision.confidence, 0.9),
                        "need_clarification": False,
                        "reason_code": "edit_answer",
                        "missing_slots": [],
                    }
                )
            intent = decision.intent
            if plan is not None and intent != Intent.PLAN:
                context = self._put_back_the_place(as_read, context)
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
                    history=history,
                    plan=plan,
                    scope_item_id=scope_item_id,
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
                asked = budget_question(context, floor=self._budget_floor(context.currency), today=date.today())
                if asked is not None:
                    tracer.metadata["reason"] = asked.reason_code
                    for event in self._ask_before_planning(asked, context=context, session_id=session_id):
                        yield event
                    self.store.touch_session(session_id)
                    return

            # A closed turn closes the path it is running here, in this task, so the path stops.
            if decision.intent == Intent.PLAN:
                path = self._plan(
                    context=context,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                    previous=plan,
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
                    history=history,
                    answering=answering,
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
            changed = current
        self.store.save_context(changed)
        return changed

    def _put_back_the_place(self, as_read: TripContext, read: TripContext) -> TripContext:
        """A question or an edit does not move a trip that has a plan to the place its sentence names or seems to name:
        the form's destination, and the dates and length worked out from it, go back to what the form held before the
        message. A field the traveller saved meanwhile (it no longer holds what this message left) is not touched."""
        current = self.store.get_context(read.session_id)
        if current is None or read.destination == as_read.destination:
            return read
        for name in ("destination", "start_date", "end_date", "days"):
            if getattr(current, name) == getattr(read, name):
                setattr(current, name, getattr(as_read, name))
        self.store.save_context(current)
        return current

    def _memory_owner(self, session_id: str) -> str:
        """Whose saved trips the planner may recall: the session's owner only."""
        session = self.store.get_session(session_id)
        return session.owner if session else ""

    async def _plan(self, *, context, session_id, tracer, request_id, previous=None) -> AsyncIterator[TurnEvent]:
        matches = self.memory.recall(context, owner=self._memory_owner(session_id))
        # A new trip for the place the saved plan is for keeps what the traveller protected in it: the stay they
        # accepted and their locked or confirmed stops. A trip to another place has nothing to carry.
        carries = previous is not None and _same_place(previous.destination.name, context.destination)
        stay = previous.hotels[0] if carries and previous.hotels else None
        if stay is not None and not (stay.confirmed or stay.locked):
            stay = None
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
                keep_hotel=stay,
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
        history: Sequence[Message] = (),
        answering: EditQuestion | None = None,
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
        asked = getattr(result, "asks", None)
        if asked is not None:
            # The edit needs a fact first: one question, with the clarify card, and nothing in the plan changes.
            tracer.metadata["reason"] = "edit_needs_fact"
            for event in self._ask_for_fact(asked, session_id=session_id):
                yield event
            self.store.touch_session(session_id)
            return
        updated, diff = result
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
        self.store.save_plan(updated, None if unchanged else updated.change_summary, replace=unchanged)
        description = _describe_diff(diff, updated)
        if diff:
            # A trip that moved takes its stay, flights and forecast along: not everything else is unchanged.
            rest = "" if any(entry.get("kind") == "dates" for entry in diff) else " Everything else is unchanged."
            prose = " ".join([f"Updated to version {updated.version}. " + description + rest, *told])
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
        history: Sequence[Message] | None = None,
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
            history=list(history) if history is not None else self.store.list_messages(session_id, limit=8),
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
    ) -> AsyncIterator[TurnEvent]:
        # With a plan, an unclear message is asked about that plan and the stop selected on it.
        question, slots = await self.clarifier.ask(
            message=message, context=context, decision=decision, history=history, plan=plan, scope_item_id=scope_item_id
        )
        self.store.add_message(
            Message(session_id=session_id, role="assistant", content=question, intent=Intent.UNCLEAR)
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
                "choices": _clarify_choices(context, slots),
                "reason_code": decision.reason_code,
            },
        )
        yield TurnEvent(event="token", data={"text": question})

    def _ask_for_fact(self, asked: EditQuestion, *, session_id: str) -> Iterator[TurnEvent]:
        """An edit's question for a missing fact, asked as the clarify step asks: a stored turn and a clarify card. The
        stored turn keeps the edit it belongs to, so the answer on the next turn completes that edit."""
        self.store.add_message(
            Message(
                session_id=session_id,
                role="assistant",
                content=asked.question,
                intent=Intent.UNCLEAR,
                meta={"edit_question": True, "fact": asked.fact, "request": asked.request[:600]},
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
                if plan.hotels and plan.hotels[0].name == now and plan.hotels[0].price_per_night is None:
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


def _pending_edit_question(history: Sequence[Message]) -> EditQuestion | None:
    """The question an edit asked on the turn before this one, when this message is the next turn, else None."""
    rows = list(history)
    if rows and rows[-1].role == "user":
        rows = rows[:-1]
    if not rows or rows[-1].role != "assistant" or not (rows[-1].meta or {}).get("edit_question"):
        return None
    meta = rows[-1].meta
    return EditQuestion(rows[-1].content, fact=str(meta.get("fact") or ""), request=str(meta.get("request") or ""))


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


def _why_lines(tracer: Any) -> list[str]:
    """A traveller-facing account of the decision. Not the model's private draft."""
    reason = str(getattr(tracer, "metadata", {}).get("reason") or "")
    intent = str(getattr(tracer, "metadata", {}).get("intent") or "")
    scoped = _scope_line(getattr(tracer, "metadata", {}) or {})
    known = {
        "plan_shape": "The message asked to reshape the whole plan, so this was an edit, not a new trip.",
        "replan_dates": "The trip details held other dates than the plan, so the trip was moved, not planned again.",
        "replan_destination": "The trip details named another place than the plan is for, so a new trip was planned.",
        "scoped_item_action": "A stop was selected, so only that stop could change.",
        "scoped_item_question": "The message was a question about the selected stop, so nothing was edited.",
        "security_blocklist": "The message was blocked before any planning.",
        "identity": "This was a question about the assistant, so no trip was planned.",
        "edit_needs_fact": "The change needs a fact the plan does not have, so it was asked for and nothing was edited.",
        "edit_answer": "The message answered the question the previous edit asked, so that edit went ahead.",
    }
    lines: list[str] = []
    if scoped:
        lines.append(scoped)
    elif reason in known:
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
