"""Turn orchestrator: one request takes one path (Appendix A).

    message -> blocklist screen (flag only) -> System 1 router + gate
      plan    -> retrieve saved trips (cutoff) -> 4 agents in parallel (time budget) -> planner
      modify  -> extract change -> only the affected agents -> check and merge
      ask     -> one model call -> answer from plan, else exactly one tool call
      unclear -> clarification drafted, turn returns to the user

The orchestrator also owns the session operations the console needs (create, update context,
confirm items, mark a plan useful) so the HTTP layer stays a thin adapter.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from functools import partial

from backend.agents.ask import AskPath
from backend.agents.clarify import ClarifyPath
from backend.agents.focus import check_focus
from backend.agents.merge import apply_change, set_confirmation
from backend.agents.modify import ModifyPath
from backend.agents.planner import Planner, PlanInvalidError
from backend.agents.preplanning import build_agents
from backend.agents.route_order import NearestNeighbourOrderer, RouteOrderer
from backend.agents.router import System1Router
from backend.agents.runtime.deps import RuntimeDeps
from backend.agents.runtime.parallel import run_with_budget
from backend.memory.background import BackgroundWriter, SaveUsefulPlanJob
from backend.memory.preferences import merge_preferences
from backend.memory.retrieval import SavedTripRetriever
from backend.memory.session import SessionNotFound, SessionStore, append_turn
from backend.schemas.agents import AgentResult, AgentTask, PlannerInput, SavedTripHint
from backend.schemas.common import ALL_AGENTS, AgentName, PathName, Route, SectionStatus, StrictModel
from backend.schemas.memory import ConversationTurn, SessionState
from backend.schemas.observability import TraceContext
from backend.schemas.routing import GateReason, GateResult, PlanFocus
from backend.schemas.security import SecurityEventKind
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import TripPlan
from backend.schemas.turn import (
    Clarification,
    QuickAnswer,
    TurnError,
    TurnErrorCode,
    TurnEvent,
    TurnEventType,
    TurnResult,
)
from backend.security.blocklist import screen_message
from backend.tools.staleness import mark_stale_sections

logger = logging.getLogger(__name__)

EventSink = Callable[[TurnEvent], Awaitable[None]]

PLAN_INVALID_MESSAGE = (
    "I couldn't produce a valid plan this time. Please try again, or add more detail "
    "(for example exact dates, party size and budget)."
)


class TurnMode(StrEnum):
    ROUTED = "routed"  # normal: the router picks the path
    FULL_REPLAN = "full_replan"  # evaluation baseline: always the full plan pipeline


class OrchestratorOptions(StrictModel):
    """`always_full_pipeline` is the ablation the system-efficiency metric compares against
    (Appendix C: "the ablation that always runs all four agents")."""

    always_full_pipeline: bool = False


class TurnOrchestrator:
    def __init__(
        self,
        *,
        deps: RuntimeDeps,
        sessions: SessionStore,
        retriever: SavedTripRetriever,
        writer: BackgroundWriter,
        options: OrchestratorOptions | None = None,
        orderer: RouteOrderer | None = None,
    ) -> None:
        self.deps = deps
        self.sessions = sessions
        self.retriever = retriever
        self.writer = writer
        self.options = options or OrchestratorOptions()
        self.orderer = orderer or NearestNeighbourOrderer()
        self.agents = build_agents(deps)
        self.router = System1Router(deps)
        self.planner = Planner(deps, self.orderer)
        self.modify = ModifyPath(deps, self.agents, self.orderer)
        self.ask = AskPath(deps)
        self.clarify = ClarifyPath(deps)
        self._locks: dict[str, asyncio.Lock] = {}

    # ---- session operations ----------------------------------------------------------------

    async def create_session(self, context: TripContext | None = None) -> SessionState:
        state = await self.sessions.create(context)
        if context is not None:
            state.preferences = merge_preferences(state.preferences, context=context)
            await self.sessions.save(state)
        return state

    async def get_session(self, session_id: str) -> SessionState:
        """The session, with plan sections older than their limit marked STALE (for refresh)."""
        state = await self.sessions.get(session_id)
        if state is None:
            raise SessionNotFound(session_id)
        if state.plan is not None:
            state.plan = mark_stale_sections(state.plan, self.deps.settings)
        return state

    async def update_context(self, session_id: str, context: TripContext) -> SessionState:
        async with self._lock(session_id):
            state = await self.get_session(session_id)
            state.context = context
            state.preferences = merge_preferences(state.preferences, context=context)
            await self.sessions.save(state)
            return state

    async def confirm(
        self,
        session_id: str,
        *,
        item_ids: Sequence[str] = (),
        ticket_ids: Sequence[str] = (),
        hotel: bool | None = None,
        confirmed: bool = True,
    ) -> TripPlan:
        async with self._lock(session_id):
            state = await self.get_session(session_id)
            if state.plan is None:
                raise LookupError("session has no plan")
            state.plan = set_confirmation(
                state.plan, item_ids=item_ids, ticket_ids=ticket_ids, hotel=hotel, confirmed=confirmed
            )
            await self.sessions.save(state)
            return state.plan

    async def mark_useful(self, session_id: str, *, useful: bool, rating: int) -> bool:
        """Proposal: "A plan you mark useful is saved (for future use and for other client with
        similar requirements)." Saving runs on the background writer; returns True if queued."""
        async with self._lock(session_id):
            state = await self.get_session(session_id)
            if state.plan is None:
                raise LookupError("session has no plan")
            if not useful:
                return False
            state.preferences = merge_preferences(state.preferences, liked_plan=state.plan)
            await self.sessions.save(state)
            self.writer.submit(
                SaveUsefulPlanJob(
                    plan=state.plan,
                    rating=rating,
                    profile_id=state.session_id,
                    preferences=state.preferences,
                )
            )
            return True

    # ---- turns ---------------------------------------------------------------------------------

    async def handle_turn(
        self,
        session_id: str,
        message: str,
        *,
        emit: EventSink | None = None,
        mode: TurnMode = TurnMode.ROUTED,
        focus: PlanFocus | None = None,
    ) -> TurnResult:
        """Run one turn. A `focus` must name a part of the current plan (PlanRequestError
        otherwise, raised before the turn starts, so nothing is traced or saved)."""
        async with self._lock(session_id):
            state = await self.get_session(session_id)
            if focus is not None:
                check_focus(state.plan, focus)
            trace = self.deps.observability.start_trace(name=f"turn:{mode.value}", session_id=session_id)
            if focus is not None:
                self.deps.observability.record_event(
                    trace, "focus", {"kind": focus.kind.value, "id": focus.id}
                )
            try:
                result = await self._turn(state, message, trace, emit, mode, focus)
            except Exception:
                logger.exception("turn failed")
                self.deps.observability.end_trace(trace, output="internal error")
                raise
            if emit:
                await emit(TurnEvent(type=TurnEventType.DONE, trace_id=trace.trace_id, result=result))
            return result

    async def _turn(
        self,
        state: SessionState,
        message: str,
        trace: TraceContext,
        emit: EventSink | None,
        mode: TurnMode,
        focus: PlanFocus | None = None,
    ) -> TurnResult:
        obs = self.deps.observability

        async def send(event: TurnEvent) -> None:
            if emit:
                await emit(event)

        screen = screen_message(message)
        if screen.flagged:
            for category in screen.categories:
                self.deps.audit.record(SecurityEventKind.INPUT_FLAGGED, category=category, path=PathName.ROUTER)
            obs.record_event(trace, "input_flagged", {"categories": ",".join(c.value for c in screen.categories)})

        if mode is TurnMode.FULL_REPLAN:
            gate = GateResult(route=Route.PLAN, reason=GateReason.ACCEPTED)
        else:
            gate = await self.router.route(state, message, trace=trace, focus=focus)
        await send(TurnEvent(type=TurnEventType.ROUTE, trace_id=trace.trace_id, route=gate.route))

        plan: TripPlan | None = state.plan
        plan_changed = False
        answer: QuickAnswer | None = None
        clarification: Clarification | None = None
        error: TurnError | None = None
        agents_run: list[AgentName] = []
        notes: list[str] = []

        full_pipeline = mode is TurnMode.FULL_REPLAN or (
            self.options.always_full_pipeline and gate.route is not Route.UNCLEAR
        )

        if gate.route is Route.UNCLEAR:
            clarification = await self.clarify.run(state, message, gate, trace=trace)
            reply = clarification.text
            await send(TurnEvent(type=TurnEventType.CLARIFICATION, trace_id=trace.trace_id, message=reply))
        elif full_pipeline:
            wants_change = mode is TurnMode.FULL_REPLAN or gate.route is Route.MODIFY
            if state.plan is not None and wants_change:
                # Baseline: the same edit, applied by re-running the whole pipeline.
                change = await self.modify.extract_change(state, message, trace=trace, path=PathName.PLAN)
                state.context = apply_change(state.context, change)
            new_plan, error, agents_run = await self._plan_path(state, trace, send)
            if new_plan is not None:
                plan, plan_changed = new_plan, True
                state.plan = new_plan
            if gate.route is Route.ASK and error is None:
                answer = await self.ask.run(state, message, trace=trace)
            reply = answer.text if answer else self._plan_reply(plan, error)
        elif gate.route is Route.PLAN:
            new_plan, error, agents_run = await self._plan_path(state, trace, send)
            if new_plan is not None:
                plan, plan_changed = new_plan, True
            reply = self._plan_reply(plan if new_plan else None, error)
        elif gate.route is Route.MODIFY:
            outcome = await self.modify.run(
                state,
                message,
                gate.decision,
                trace=trace,
                on_start=self._agent_hook(trace, send, TurnEventType.AGENT_STARTED),
                on_finish=self._agent_hook(trace, send, TurnEventType.AGENT_FINISHED),
            )
            plan, plan_changed = outcome.plan, True
            state.context = outcome.context
            agents_run = outcome.agents_run
            notes = outcome.notes
            reply = self._modify_reply(outcome.plan, agents_run, notes)
        else:
            answer = await self.ask.run(state, message, trace=trace, focus=focus)
            reply = answer.text

        if error is not None:
            await send(TurnEvent(type=TurnEventType.ERROR, trace_id=trace.trace_id, message=error.message))
        elif plan_changed:
            await send(TurnEvent(type=TurnEventType.PLAN, trace_id=trace.trace_id, message=reply))
        elif answer is not None:
            await send(TurnEvent(type=TurnEventType.ANSWER, trace_id=trace.trace_id, message=reply))

        state.plan = plan
        state.turn_count += 1
        append_turn(state, ConversationTurn(role="user", content=message, route=gate.route))
        append_turn(state, ConversationTurn(role="assistant", content=reply, route=gate.route))
        state.preferences = merge_preferences(state.preferences, context=state.context)
        await self.sessions.save(state)

        obs.end_trace(trace, output=reply)
        metrics = obs.metrics(trace.trace_id)
        metrics.agents_run = agents_run or metrics.agents_run
        return TurnResult(
            session_id=state.session_id,
            trace_id=trace.trace_id,
            route=gate.route,
            gate=gate,
            reply=reply,
            plan=plan,
            plan_changed=plan_changed,
            answer=answer,
            clarification=clarification,
            error=error,
            input_flagged=screen.flagged,
            agents_run=agents_run,
            metrics=metrics,
        )

    async def _plan_path(
        self, state: SessionState, trace: TraceContext, send: EventSink
    ) -> tuple[TripPlan | None, TurnError | None, list[AgentName]]:
        settings = self.deps.settings
        hints: list[SavedTripHint] = []
        try:
            saved = await self.retriever.retrieve(state.context, state.preferences)
            hints = [SavedTripHint.from_similar(s) for s in saved]
        except Exception:  # memory is an aid; its failure must not block planning
            logger.warning("saved-trip retrieval failed", exc_info=True)
        self.deps.observability.record_event(
            trace, "saved_trips_retrieved", {"count": len(hints), "cutoff": settings.similarity_cutoff}
        )

        task = AgentTask(context=state.context, preferences=state.preferences)
        budget = settings.preplanning_time_budget_s
        deadline = asyncio.get_running_loop().time() + budget
        jobs = {
            agent: partial(self.agents[agent].run, task, trace=trace, path=PathName.PLAN, deadline=deadline)
            for agent in ALL_AGENTS
        }
        results = await run_with_budget(
            jobs,
            budget_s=budget,
            on_start=self._agent_hook(trace, send, TurnEventType.AGENT_STARTED),
            on_finish=self._agent_hook(trace, send, TurnEventType.AGENT_FINISHED),
        )
        planner_input = PlannerInput(
            plan_id=state.plan.plan_id if state.plan else uuid.uuid4().hex,
            context=state.context,
            preferences=state.preferences,
            results=list(results.values()),
            saved_trips=hints,
            previous_plan=state.plan,
        )
        try:
            plan = await self.planner.plan(planner_input, trace=trace)
        except PlanInvalidError:
            return None, TurnError(code=TurnErrorCode.PLAN_INVALID, message=PLAN_INVALID_MESSAGE), list(results)
        return plan, None, list(results)

    @staticmethod
    def _agent_hook(
        trace: TraceContext, send: EventSink, kind: TurnEventType
    ) -> Callable[[AgentName, AgentResult | None], Awaitable[None]]:
        async def hook(agent: AgentName, result: AgentResult | None) -> None:
            await send(
                TurnEvent(
                    type=kind,
                    trace_id=trace.trace_id,
                    agent=agent,
                    status=result.status if result else None,
                    message=result.reason if result else None,
                )
            )

        return hook

    @staticmethod
    def _plan_reply(plan: TripPlan | None, error: TurnError | None) -> str:
        if error is not None:
            return error.message
        if plan is None:
            return PLAN_INVALID_MESSAGE
        parts = [f"Here is your {len(plan.days)}-day plan for {plan.destination}."]
        missing = [s for s in plan.sections if s.status is not SectionStatus.OK]
        if missing:
            parts.append(
                "Unavailable: " + "; ".join(f"{s.agent.value} ({s.reason or s.status.value})" for s in missing) + "."
            )
        if plan.violations:
            parts.append(f"{len(plan.violations)} check(s) need attention.")
        if plan.saved_trip_refs:
            parts.append(f"It reuses {len(plan.saved_trip_refs)} similar saved trip(s).")
        return " ".join(parts)

    @staticmethod
    def _modify_reply(plan: TripPlan, agents_run: Sequence[AgentName], notes: Sequence[str]) -> str:
        if not agents_run:
            return "Nothing in the plan needed to change."
        parts = [f"Updated your plan (re-checked: {', '.join(a.value for a in agents_run)})."]
        resolved = [d for d in plan.disruptions if d.resolved and d.resolution]
        if resolved:
            parts.append(" ".join(f"{d.detail}: {d.resolution}." for d in resolved[-3:]))
        parts += list(notes)
        if plan.violations:
            parts.append(f"{len(plan.violations)} check(s) need attention.")
        return " ".join(parts)

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())
