"""One traveller turn, from message to plan, as a stream of typed events.

The sequence is the product:

    message -> intake -> understand -> one path
      plan    -> agents (evidence) -> compose -> validate -> repair -> persist -> plan
      modify  -> agents (scoped)   -> edit ops -> validate -> proposal
      ask     -> retrieve -> answer
      unclear -> clarify

Every model call is traced; every fact in a plan comes from evidence; every failure
that is not repaired becomes a visible gap.  The API layer only wraps these events
as SSE frames.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, AsyncIterator

from app.config import Settings
from app.core.dates import parse_date
from app.core.llm import LLMClient, LLMError
from app.core.prompts import context_brief, dialogue, plan_text, retrieved, skill
from app.core.store import Store
from app.core.trace import TurnTrace
from app.domain.models import (
    BufferedAttachment,
    ChatMessage,
    ChatRequest,
    ClarifyPayload,
    IntakePayload,
    IntakeSource,
    PlanItem,
    RoutePayload,
    TripContext,
    TripPlan,
    now_iso,
)
from app.evidence.providers import Providers
from app.plan.agents import gather
from app.plan.builder import Problem, compose, refresh_derived
from app.plan.editor import diff_plans, propose_edit
from app.plan.validator import audit_constraints, check_structure, problems_from_verify
from app.security.pii import Redaction, mask_pii
from app.understand.pipeline import dates_usable, missing_for_plan, understand
from app.understand.schemas import IntakeFacts

MAX_CONTEXT_TURNS = 10


@dataclass
class TurnEvent:
    event: str
    data: dict[str, Any]


class Dependencies:
    """What a turn needs from the running app."""

    def __init__(self, *, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store


async def run_turn(deps: Dependencies, request: ChatRequest, *, session_id: str, user_message: str) -> AsyncIterator[TurnEvent]:
    settings = deps.settings
    store = deps.store
    request_id = uuid.uuid4().hex
    trace = TurnTrace(request_id=request_id)
    llm = LLMClient(settings, trace=trace)
    providers = Providers(settings, trace, serp_keys=await store.get_kv("serpapi:keys") or [])
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        emitted.append((event, data))

    try:
        async for item in _turn(
            deps=deps,
            request=request,
            session_id=session_id,
            message=user_message,
            trace=trace,
            llm=llm,
            providers=providers,
            emit=emit,
        ):
            yield item
    finally:
        await llm.aclose()
        await providers.aclose()


async def _turn(
    *,
    deps: Dependencies,
    request: ChatRequest,
    session_id: str,
    message: str,
    trace: TurnTrace,
    llm: LLMClient,
    providers: Providers,
    emit,
) -> AsyncIterator[TurnEvent]:
    settings = deps.settings
    store = deps.store
    queue: asyncio.Queue[TurnEvent | None] = asyncio.Queue()

    async def push(event: str, data: dict[str, Any]) -> None:
        queue.put_nowait(TurnEvent(event, data))

    async def producer() -> None:
        try:
            await _produce(
                deps=deps,
                request=request,
                session_id=session_id,
                message=message,
                trace=trace,
                llm=llm,
                providers=providers,
                push=push,
            )
        except LLMError as exc:
            await push("trace", trace.as_dict())
            await push(
                "error",
                {
                    "code": "model_unavailable",
                    "message": "I could not reach the planning model. Please try again.",
                    "detail": str(exc)[:400],
                    "retryable": exc.retryable,
                },
            )
        except Exception as exc:  # noqa: BLE001 - the stream must always end cleanly
            import traceback
            traceback.print_exc()
            await push("trace", trace.as_dict())
            await push(
                "error",
                {
                    "code": "turn_failed",
                    "message": "Something went wrong while planning. Please try again.",
                    "detail": str(exc)[:400],
                    "retryable": True,
                },
            )
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(producer())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
        await task
    finally:
        if not task.done():
            task.cancel()


async def _produce(
    *,
    deps: Dependencies,
    request: ChatRequest,
    session_id: str,
    message: str,
    trace: TurnTrace,
    llm: LLMClient,
    providers: Providers,
    push,
) -> None:
    settings = deps.settings
    store = deps.store
    trace.flow.append("turn:start")
    runtime = await store.get_kv("runtime") or {}
    redaction = Redaction.from_runtime(runtime)
    # First layer: the model, the stored chat and the trace only ever see the masked text.
    message = mask_pii(message or "", redaction)
    request = request.model_copy(update={"message": message})
    await store.touch_session(session_id, planner_note="Reading your message")
    context = await _load_context(store, session_id, settings, request.locale)
    if request.identity:
        from app.application.memory import merged_context_memory

        profile = await store.get_profile(request.identity)
        context = merged_context_memory(context, profile)
        await store.save_context(session_id, context.model_dump())
    plan = await _load_plan(store, session_id)
    history = await store.recent_messages(session_id, limit=MAX_CONTEXT_TURNS)
    chat_history = [
        ChatMessage(**{**row, "content": mask_pii(row.get("content") or "", redaction)}) for row in history
    ]

    user_row = ChatMessage(id=uuid.uuid4().hex, role="user", content=message, created_at=now_iso())
    await store.add_message(session_id, user_row.model_dump())
    session_row = await store.get_session(session_id)
    if session_row and session_row.get("held"):
        agent = session_row.get("agent_name") or "A travel designer"
        note = session_row.get("note") or ""
        reply = f"{agent} is with you now and will answer here." + (f" {note}" if note else "")
        await _stream_text(push, reply)
        await _save_assistant(store, session_id, reply, intent="ask", meta={"live_agent": True, "agent_name": agent})
        await _finish(push, trace, session_id=session_id, intent="ask")
        return
    await push("progress", {"phase": "understand", "message": "Reading your message.", "progress": 4})

    skill_overrides = await _skill_overrides(store)
    scenario = await store.get_kv(f"scenario:{session_id}")

    # -- intake (only when something was attached or linked) -----------
    intake_payload, intake_json, images = await _intake(
        settings=settings,
        llm=llm,
        request=request,
        message=message,
        trace=trace,
        redaction=redaction,
    )
    if intake_payload is not None:
        await push("intake", intake_payload.model_dump())
        context = _apply_intake(context, intake_json)

    if llm.available is False:
        await _finish_plain(
            push,
            store,
            session_id,
            "The planning model is not configured on this server, so I cannot plan right now. "
            "An operator can add a key in Settings → Model keys.",
            trace,
            request_id=trace.request_id,
            intent="unclear",
        )
        return

    # -- understand ----------------------------------------------------
    await store.touch_session(session_id, planner_note="Working out what you want")
    understanding = await understand(
        llm=llm,
        settings=settings,
        context=context,
        plan=plan,
        messages=chat_history,
        message=message,
        scope_label=request.scope_label,
        scope_item_id=request.scope_item_id,
        locale=request.locale,
        skill_overrides=skill_overrides,
    )
    context = understanding.context
    route = understanding.route
    trace.path = {
        "intent": route.intent,
        "confidence": route.confidence,
        "reason": understanding.parsed.reason,
        "chooser": "turn-analyst",
        "router": llm.model,
    }
    trace.why.append(
        f"Read as {route.intent} ({route.confidence:.0%} confident): {understanding.parsed.reason or route.reason_code}."
    )
    await store.save_context(session_id, context.model_dump())
    await push("route", route.model_dump())
    await push("progress", {"phase": "route", "message": "Deciding the path.", "progress": 8})
    if understanding.parsed.assumptions:
        trace.why.extend(f"Assumed: {line}" for line in understanding.parsed.assumptions[:3])
    if understanding.parsed.injection_suspected:
        await _count_threat(store, "instruction_override")
        trace.say("The message tried to give the assistant new instructions; it was treated as data.")
    if route.intent == "modify" and plan is None:
        route = route.model_copy(update={"intent": "plan", "reason_code": "no_plan_to_edit"})
        await push("route", route.model_dump())
    if understanding.parsed.self_harm:
        reply = _safety_reply()
        await _stream_text(push, reply)
        await _save_assistant(store, session_id, reply, intent="ask", meta={"safety": "self_harm"})
        await _finish(push, trace, session_id=session_id, intent="ask")
        return
    policy = await store.get_kv("escalation") or {}
    handoff_reason = await _handoff_reason(store, session_id, understanding.parsed, policy)
    if handoff_reason:
        await _handoff(push, store, session_id, trace, handoff_reason)
        return
    if context.finalized and route.intent in {"plan", "modify"}:
        await _finish_plain(
            push,
            store,
            session_id,
            "This trip is finalised, so it can be viewed but not changed. Start a new trip if you want to plan another.",
            trace,
            request_id=trace.request_id,
            intent=route.intent,
        )
        return

    # -- clarify -------------------------------------------------------
    if route.need_clarification or (route.intent == "plan" and missing_for_plan(context)):
        if await _bump_stuck(store, session_id, policy) >= int(policy.get("stuck_after") or 3) and policy.get("stuck_on", True):
            await _handoff(push, store, session_id, trace, "stuck")
            return
        clarify = understanding.clarify or _fallback_clarify(context, route)
        # The card carries the question and its choices; the chat line stays short so the
        # same sentence is not printed twice.
        reply = "I need one detail before I can plan."
        await push("clarify", clarify.model_dump())
        await _stream_text(push, reply)
        await _save_assistant(
            store,
            session_id,
            reply,
            intent=route.intent,
            confidence=route.confidence,
            meta={
                "clarify": True,
                "slots": clarify.slots,
                "place_suggestions": [item.model_dump() for item in clarify.suggestions],
                "clarify_payload": clarify.model_dump(),
            },
        )
        await _finish(push, trace, session_id=session_id, intent=route.intent)
        return

    # -- dispatch ------------------------------------------------------
    if route.intent == "plan":
        await _plan_turn(
            deps=deps,
            request=request,
            session_id=session_id,
            context=context,
            previous=plan,
            trace=trace,
            llm=llm,
            providers=providers,
            push=push,
            skill_overrides=skill_overrides,
            scenario=scenario,
            change_summary="Planned the trip.",
            research_places=understanding.parsed.research_places,
            search_queries=understanding.parsed.search_queries,
        )
        return
    if route.intent == "modify" and plan is not None:
        await _edit_turn(
            deps=deps,
            request=request,
            session_id=session_id,
            context=context,
            plan=plan,
            trace=trace,
            llm=llm,
            providers=providers,
            push=push,
            skill_overrides=skill_overrides,
            scenario=scenario,
            research_places=understanding.parsed.research_places,
            search_queries=understanding.parsed.search_queries,
        )
        return
    await _ask_turn(
        deps=deps,
        request=request,
        session_id=session_id,
        context=context,
        plan=plan,
        trace=trace,
        llm=llm,
        providers=providers,
        push=push,
        skill_overrides=skill_overrides,
        message=message,
        chat_history=chat_history,
        redaction=redaction,
    )


# -- planning path -----------------------------------------------------


async def _plan_turn(
    *,
    deps: Dependencies,
    request: ChatRequest,
    session_id: str,
    context: TripContext,
    previous: TripPlan | None,
    trace: TurnTrace,
    llm: LLMClient,
    providers: Providers,
    push,
    skill_overrides: dict[str, str],
    scenario: dict[str, Any] | None,
    change_summary: str,
    research_places: list[str] | None = None,
    search_queries: list[str] | None = None,
):
    settings = deps.settings
    store = deps.store
    await store.touch_session(session_id, planner_note="Finding places")
    await push("progress", {"phase": "gather", "message": "Finding places, weather and travel.", "progress": 14})
    gathered = await gather(
        providers=providers,
        settings=settings,
        context=context,
        trace=trace,
        emit=push,
        scenario=scenario,
    )
    bundle = gathered.bundle
    await _add_plan_research(
        providers=providers,
        context=context,
        bundle=bundle,
        names=research_places or [],
        queries=search_queries or [],
        trace=trace,
    )
    await push("progress", {"phase": "gather", "message": f"Found {len(bundle.items)} sourced options.", "progress": 42})

    previous_problems: list[Problem] = []
    plan: TripPlan | None = None
    draft = None
    for attempt in range(max(1, settings.max_repairs + 1)):
        await push(
            "progress",
            {
                "phase": "compose",
                "message": "Building the itinerary." if attempt == 0 else "Adjusting the itinerary to fix a problem.",
                "progress": 48 + attempt * 8,
            },
        )
        started = time.perf_counter()
        composed_fallback = False
        try:
            try:
                plan, draft = await compose(
                    llm=llm,
                    settings=settings,
                    context=context,
                    bundle=bundle,
                    disruptions=gathered.disruptions,
                    notes=gathered.notes,
                    previous_problems=previous_problems,
                    skill_overrides=skill_overrides,
                    trace=trace,
                    avoid_transit_days=gathered.avoid_transit_days,
                )
            except LLMError:
                # A queued or cut-off generation is worth one fresh attempt before degrading.
                trace.say("The composer did not answer; retrying once.")
                await push("progress", {"phase": "compose", "message": "Retrying the itinerary.", "progress": 52})
                plan, draft = await compose(
                    llm=llm,
                    settings=settings,
                    context=context,
                    bundle=bundle,
                    disruptions=gathered.disruptions,
                    notes=gathered.notes,
                    previous_problems=previous_problems,
                    skill_overrides=skill_overrides,
                    trace=trace,
                    avoid_transit_days=gathered.avoid_transit_days,
                )
        except LLMError as exc:
            from app.plan.builder import fallback_plan, sync_constraints

            trace.say("The composer did not answer; falling back to the sourced shortlist.")
            plan = fallback_plan(
                context=context,
                bundle=bundle,
                disruptions=gathered.disruptions,
                weather_by_date={day.date: day for day in bundle.weather},
                start=parse_date(context.start_date) or date.today() + timedelta(days=30),
                end=parse_date(context.end_date) or date.today() + timedelta(days=32),
                settings=settings,
                reason=str(exc),
            )
            draft = None
            composed_fallback = True
            sync_constraints(plan, context)
            problems = []
            break
        except ValueError:
            clarify = _fallback_clarify(context, None)
            await push("clarify", clarify.model_dump())
            await _stream_text(push, clarify.question)
            await _save_assistant(store, session_id, clarify.question, intent="plan", meta={"clarify": clarify.model_dump()})
            await _finish(push, trace, session_id=session_id, intent="plan")
            return
        problems = check_structure(plan, context)
        if trace:
            trace.why.append(f"Planning attempt {attempt + 1} produced {plan.item_count()} stops ({time.perf_counter() - started:.1f}s).")
        if not problems:
            break
        trace.say("Problems found: " + "; ".join(problem.message for problem in problems[:3]))
        previous_problems = problems
        if attempt >= settings.max_repairs:
            break

    if plan is None:
        raise RuntimeError('the planner returned no plan')
    await push("progress", {"phase": "verify", "message": "Checking the constraints.", "progress": 84})
    verification = await audit_constraints(llm=llm, plan=plan, context=context, trace=trace, skill_overrides=skill_overrides)
    problems = problems_from_verify(verification)
    if problems and settings.max_repairs:
        previous_problems = [*previous_problems, *problems]
        await push("progress", {"phase": "repair", "message": "Fixing a constraint.", "progress": 88})
        try:
            repaired, draft2 = await compose(
                llm=llm,
                settings=settings,
                context=context,
                bundle=bundle,
                disruptions=gathered.disruptions,
                notes=gathered.notes,
                previous_problems=previous_problems,
                skill_overrides=skill_overrides,
                trace=trace,
                avoid_transit_days=gathered.avoid_transit_days,
            )
            await audit_constraints(llm=llm, plan=repaired, context=context, trace=trace, skill_overrides=skill_overrides)
            if repaired.item_count() >= plan.item_count() or not problems:
                plan, draft = repaired, draft2
        except Exception as exc:  # noqa: BLE001 - keep the first plan if the repair fails
            trace.say(f"The repair attempt did not finish: {exc}")

    plan = await _persist_plan(store, session_id, context, plan, previous, change_summary)
    reply = _plan_reply(plan, gathered.bundle.gaps)
    await _save_assistant(
        store,
        session_id,
        reply,
        intent="plan",
        confidence=plan.assessment.confidence if plan.assessment else None,
        meta={"plan_version": plan.version, "summary": plan.summary},
    )
    await push("plan", {"plan": plan.model_dump(mode="json"), "diff": diff_plans(previous, plan)})
    await _stream_text(push, reply)
    await _finish(push, trace, session_id=session_id, intent="plan", plan=plan)


# -- editing path ------------------------------------------------------


async def _edit_turn(
    *,
    deps: Dependencies,
    request: ChatRequest,
    session_id: str,
    context: TripContext,
    plan: TripPlan,
    trace: TurnTrace,
    llm: LLMClient,
    providers: Providers,
    push,
    skill_overrides: dict[str, str],
    scenario: dict[str, Any] | None,
    research_places: list[str] | None = None,
    search_queries: list[str] | None = None,
):
    settings = deps.settings
    store = deps.store
    policy = await store.get_kv("escalation") or {}
    await store.touch_session(session_id, planner_note="Changing the plan")
    await push("progress", {"phase": "gather", "message": "Checking options for the change.", "progress": 20})
    gathered = await gather(
        providers=providers,
        settings=settings,
        context=context,
        trace=trace,
        emit=push,
        scenario=scenario,
    )
    await push("progress", {"phase": "edit", "message": "Working out the smallest change.", "progress": 55})
    outcome = await propose_edit(
        llm=llm,
        providers=providers,
        settings=settings,
        context=context,
        plan=plan,
        bundle=gathered.bundle,
        message=request.message,
        scope_label=request.scope_label,
        scope_item_id=request.scope_item_id,
        trace=trace,
        skill_overrides=skill_overrides,
    )
    if outcome.rebuild:
        trace.say(f"The edit needs a replan: {outcome.rebuild_reason}.")
        await store.save_context(session_id, outcome.context.model_dump())
        await _plan_turn(
            deps=deps,
            request=request,
            session_id=session_id,
            context=outcome.context,
            previous=plan,
            trace=trace,
            llm=llm,
            providers=providers,
            push=push,
            skill_overrides=skill_overrides,
            scenario=scenario,
            change_summary=outcome.draft.summary or "Replanned the trip.",
            research_places=research_places,
            search_queries=search_queries,
        )
        return
    if not outcome.changed:
        if await _bump_stuck(store, session_id, policy) >= int(policy.get("stuck_after") or 3) and policy.get("stuck_on", True):
            await _handoff(push, store, session_id, trace, "stuck")
            return
        reason = outcome.log[-1] if outcome.log else ""
        reply = reason or "I could not make that change from sourced places, so the plan is unchanged."
        if outcome.research_failed:
            reply += (
                '\n\nIf you still want it, say "add it anyway" and I will add it as an '
                "unconfirmed stop, clearly marked in the plan."
            )
        await push("token", {"text": reply})
        await _save_assistant(store, session_id, reply, intent="modify", meta={"log": {"steps": outcome.log}})
        await _finish(push, trace, session_id=session_id, intent="modify")
        return

    old_plan = plan
    await _reset_stuck(store, session_id)
    if not request.review:
        applied = await _persist_plan(
            store, session_id, outcome.context, outcome.plan, old_plan, outcome.draft.summary or "Edited the trip."
        )
        reply = _edit_reply(applied, outcome.draft.summary, outcome.log)
        await _save_assistant(store, session_id, reply, intent="modify", meta={"plan_version": applied.version})
        await push("plan", {"plan": applied.model_dump(mode="json"), "diff": diff_plans(old_plan, applied)})
        await _stream_text(push, reply)
        await _finish(push, trace, session_id=session_id, intent="modify", plan=applied)
        return
    new_plan = await _persist_plan(store, session_id, outcome.context, outcome.plan, old_plan, outcome.draft.summary or "Edited the trip.", draft=True)
    change = {
        "id": "edit-1",
        "label": outcome.draft.summary[:180] or "Apply this change",
        "detail": outcome.draft.detail[:240],
        "status": "pending",
        "plan": new_plan.model_dump(mode="json"),
    }
    proposal = {"changes": [change]}
    reply = _edit_reply(new_plan, outcome.draft.summary, outcome.log)
    message_row = await _save_assistant(
        store,
        session_id,
        reply,
        intent="modify",
        meta={"proposal": proposal, "log": {"steps": outcome.log}},
    )
    await push("proposal", {"message_id": message_row["id"], "proposal": proposal, "log": {"steps": outcome.log}})
    await _stream_text(push, reply)
    trace.why.append("The change is a proposal: it is applied only when its tick is accepted.")
    await _finish(push, trace, session_id=session_id, intent="modify")


# -- ask path ----------------------------------------------------------


async def _ask_turn(
    *,
    deps: Dependencies,
    request: ChatRequest,
    session_id: str,
    context: TripContext,
    plan: TripPlan | None,
    trace: TurnTrace,
    llm: LLMClient,
    providers: Providers,
    push,
    skill_overrides: dict[str, str],
    message: str,
    chat_history: list[ChatMessage],
    redaction: Redaction | None = None,
):
    store = deps.store
    queries = await _search_queries(llm, message, plan)
    results = []
    for query in queries[:2]:
        results.extend(await providers.search(query, limit=4))
    if queries:
        trace.why.append("Looked up: " + "; ".join(queries[:2]))
    payload = {
        "trip": context_brief(context),
        "plan": plan_text(plan, with_sources=True),
        "recent_turns": dialogue(chat_history),
        "sources": retrieved(results),
        "cost": plan.cost.model_dump() if plan else None,
        "question": message,
    }
    system = skill("ask", skill_overrides)
    await push("progress", {"phase": "answer", "message": "Answering.", "progress": 60})
    collected: list[str] = []
    async for chunk in llm.stream_text(
        purpose="ask",
        system=system,
        user=json.dumps(payload, ensure_ascii=False, indent=1),
        max_tokens=1400,
    ):
        collected.append(chunk)
        await push("token", {"text": chunk})
    reply = mask_pii(_guard_output("".join(collected), deps.settings), redaction)
    await _save_assistant(store, session_id, reply, intent="ask", meta={"sources": [row.get("url", "") for row in results[:4]]})
    await _reset_stuck(store, session_id)
    await _finish(push, trace, session_id=session_id, intent="ask")


async def _search_queries(llm: LLMClient, message: str, plan: TripPlan | None) -> list[str]:
    """The analyst already decided whether facts are needed; this asks the model for a tight query."""
    from app.understand.schemas import Draft

    class Queries(Draft):
        queries: list[str] = []

    try:
        result = await llm.json(
            purpose="search_query",
            system=(
                "Write 0-2 web search queries that would answer the traveller's question about their trip. "
                "If the question can be answered from the itinerary alone, return an empty list. "
                'Reply as JSON: {"queries": ["..."]}'
            ),
            user=json.dumps({"question": message, "destination": plan.destination.name if plan else ""}),
            schema=Queries,
            max_tokens=200,
        )
        return [query for query in result.queries if query.strip()][:2]
    except LLMError:
        return []


# -- helpers -----------------------------------------------------------


async def _add_plan_research(
    *,
    providers: Providers,
    context: TripContext,
    bundle: Any,
    names: list[str],
    queries: list[str],
    trace: TurnTrace,
) -> None:
    """Look up the named places (and described wishes) a generic guide may not hold."""
    from app.domain.models import SourceRef, now_iso
    from app.evidence.models import Evidence

    city = context.destination or ""
    for name in [item for item in names if item.strip()][:4]:
        hits = await providers.lookup_place(name, city)
        if not hits:
            continue
        evidence = hits[0]
        evidence.ref = f"pl-{len(bundle.items) + 1}"
        bundle.add([evidence], "research")
        trace.say(f"Looked up {name}: {evidence.name}.")
    for query in [item for item in queries if item.strip()][:2]:
        results = await providers.search(query, limit=3)
        for row in results[:2]:
            title = str(row.get("title") or "").strip()
            url = str(row.get("url") or "").strip()
            if not title or not url:
                continue
            clean_title = title.split(" - ")[0].strip() or title
            bundle.items.append(
                Evidence(
                    ref=f"web-{len(bundle.items) + 1}",
                    kind="attraction",
                    name=clean_title[:160],
                    address=city,
                    summary=str(row.get("snippet") or "")[:400],
                    source_refs=[SourceRef(title=title[:160], url=url, source="Web", fetched_at=now_iso())],
                    data_status="stale",
                    fetched_at=now_iso(),
                    tags=["web result"],
                    suggested_minutes=90,
                )
            )
        trace.say(f"Looked up “{query}”: {len(results)} results.")


async def _load_context(store: Store, session_id: str, settings: Settings, locale: str) -> TripContext:
    row = await store.load_context(session_id)
    if row:
        return TripContext(**row)
    return TripContext(
        session_id=session_id,
        currency=settings.default_currency,
        language=locale or "en",
        plan_locale=locale if locale in {"en", "zh-Hant", "zh-Hans"} else "en",
    )


async def _load_plan(store: Store, session_id: str) -> TripPlan | None:
    row = await store.load_plan(session_id)
    return TripPlan(**row) if row else None


async def _persist_plan(
    store: Store,
    session_id: str,
    context: TripContext,
    plan: TripPlan,
    previous: TripPlan | None,
    change_summary: str,
    *,
    draft: bool = False,
) -> TripPlan:
    version = (previous.version + 1) if previous else 1
    plan.version = version
    plan.session_id = session_id
    plan.change_summary = change_summary[:300]
    plan.status = "draft"
    if not draft:
        plan.created_at = previous.created_at if previous else plan.created_at
    plan.updated_at = now_iso()
    if not draft:
        await store.save_plan(plan.model_dump(mode="json"))
    return plan


async def _save_assistant(
    store: Store,
    session_id: str,
    content: str,
    *,
    intent: str | None = None,
    confidence: float | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = ChatMessage(
        id=uuid.uuid4().hex,
        role="assistant",
        content=content,
        intent=intent,  # type: ignore[arg-type]
        confidence=confidence,
        created_at=now_iso(),
        meta=meta or {},
    ).model_dump()
    await store.add_message(session_id, row)
    return row


async def _stream_text(push, text: str) -> None:
    text = text or ""
    for start in range(0, len(text), 120):
        await push("token", {"text": text[start : start + 120]})
        await asyncio.sleep(0)


async def _finish(push, trace: TurnTrace, *, session_id: str, intent: str, plan: TripPlan | None = None) -> None:
    await push("trace", trace.as_dict())
    payload: dict[str, Any] = {"request_id": trace.request_id, "intent": intent, "session_id": session_id}
    if plan is not None:
        payload["plan_version"] = plan.version
    await push("done", payload)


async def _finish_plain(push, store: Store, session_id: str, text: str, trace: TurnTrace, *, request_id: str, intent: str) -> None:
    await _stream_text(push, text)
    await _save_assistant(store, session_id, text, intent=intent)
    await push("trace", trace.as_dict())
    await push("done", {"request_id": request_id, "intent": intent, "session_id": session_id})


LEAK_MARKERS = (
    "Skill: turn analyst",
    "Skill: traveller answer",
    "Skill: itinerary composer",
    "You are the understanding layer",
    "You are the planning layer",
    "You are the same assistant that built the plan",
    "{{threshold}}",
)


def _guard_output(text: str, settings: Settings) -> str:
    """Never let a model reply carry the instructions it was given."""
    canary = settings.canary_token
    leaked = bool(canary and canary in text) or any(marker in text for marker in LEAK_MARKERS)
    if leaked:
        return (
            "I cannot share that. I can help with the trip itself — the itinerary, places, "
            "prices, weather or transport."
        )
    return text


def _plan_reply(plan: TripPlan, gaps) -> str:
    lines = [plan.summary or f"Here is a plan for {plan.destination.name}."]
    for index, day in enumerate(plan.days, start=1):
        names = ", ".join(item.name for item in day.items[:4])
        if names:
            lines.append(f"Day {index} ({day.date}): {names}.")
    if plan.hotels:
        lines.append(f"Stay: {plan.hotels[0].name}.")
    if plan.cost and plan.cost.total:
        budget = f" of {plan.cost.budget:.0f}" if plan.cost.budget else ""
        lines.append(f"Estimated total: {plan.cost.total:.0f} {plan.cost.currency}{budget}.")
    if plan.data_quality.gaps:
        lines.append("Not confirmed by a source: " + "; ".join(gap.message for gap in plan.data_quality.gaps[:3]) + ".")
    return "\n\n".join(lines[:8])


def _edit_reply(plan: TripPlan, summary: str, log: list[str]) -> str:
    first = summary or "I have prepared that change."
    extra = [line for line in log if line][:2]
    return "\n\n".join([first, *extra])


def _safety_reply() -> str:
    """A human-first answer for a message about self-harm; never a travel reply."""
    return (
        "I'm really sorry you're feeling this way. I'm not able to provide emergency help, "
        "but I want you to reach a person who can, now.\n\n"
        "In Hong Kong:\n"
        "- Emergency: 999 (if you are in immediate danger)\n"
        "- The Samaritan Befrienders Hong Kong: 2389 2222 — 24 hours\n"
        "- The Samaritans: 2896 0000 — 24 hours, multilingual\n"
        "- Suicide Prevention Services: 2382 0000 — 24 hours\n"
        "- Hospital Authority Mental Health 24-hour Hotline: 2466 7350\n\n"
        "Outside Hong Kong, findahelpline.com lists a free, confidential line for your country.\n\n"
        "Please call one of them, or tell someone nearby — a friend, family member, or someone at "
        "your university — that you are having a hard time and need them with you. "
        "Are you safe right now?"
    )


def _fallback_clarify(context: TripContext, route: RoutePayload | None) -> ClarifyPayload:
    if not context.destination:
        question, slots = "Where would you like to go, and roughly when?", ["destination", "start_date"]
    elif not dates_usable(context):
        question, slots = "Which dates should I plan for? A start date and a length is enough.", ["start_date", "days"]
    else:
        question, slots = "Could you say a little more about what you would like?", ["scope"]
    return ClarifyPayload(
        question=question,
        slots=slots,
        labels=[slot.replace("_", " ").title() for slot in slots],
        options={},
        choices=[],
        suggestions=[],
        reason_code=(route.reason_code if route else "missing_slots"),
    )


async def _handoff_reason(
    store: Store, session_id: str, parsed: Any, policy: dict[str, Any]
) -> str:
    """Why this turn should go to a person, or an empty string."""
    if parsed.wants_human:
        return "asked"
    if not policy.get("enabled", True):
        return ""
    threshold = float(policy.get("frustration_at") or 0.75)
    if float(parsed.frustration or 0) >= threshold:
        return "frustration"
    if policy.get("stuck_on", True):
        count = await _stuck_count(store, session_id)
        if count >= int(policy.get("stuck_after") or 3):
            return "stuck"
    return ""


async def _stuck_count(store: Store, session_id: str) -> int:
    try:
        return int(await store.get_kv(f"stuck:{session_id}") or 0)
    except (TypeError, ValueError):
        return 0


async def _bump_stuck(store: Store, session_id: str, policy: dict[str, Any]) -> int:
    if not policy.get("enabled", True) or not policy.get("stuck_on", True):
        return 0
    count = await _stuck_count(store, session_id) + 1
    await store.set_kv(f"stuck:{session_id}", count)
    return count


async def _reset_stuck(store: Store, session_id: str) -> None:
    await store.delete_kv(f"stuck:{session_id}")


async def _handoff(push, store: Store, session_id: str, trace: TurnTrace, reason: str) -> None:
    await store.set_held(session_id, True, "Travel designer queue")
    replies = {
        "asked": "I have asked a travel designer to join this chat. They will reply here and can take over the plan; you can keep typing in the meantime.",
        "frustration": "I am sorry this is not working for you. I have asked a travel designer to join and take over the plan; they will reply here.",
        "stuck": "We have been circling the same point, so I have asked a travel designer to join and take over. They will reply here; you can keep typing.",
    }
    reply = replies.get(reason, replies["asked"])
    trace.say(f"Escalated to a person ({reason}).")
    await _stream_text(push, reply)
    await _save_assistant(store, session_id, reply, intent="ask", meta={"handoff": True, "handoff_reason": reason})
    await _finish(push, trace, session_id=session_id, intent="ask")


async def _count_threat(store: Store, category: str) -> None:
    counters = await store.get_kv("threats") or {}
    counters[category] = int(counters.get(category, 0)) + 1
    await store.set_kv("threats", counters)


async def _skill_overrides(store: Store) -> dict[str, str]:
    value = await store.get_kv("prompts")
    if isinstance(value, dict):
        return {str(key): str(body) for key, body in value.items() if isinstance(body, str) and body.strip()}
    return {}


async def _intake(
    *,
    settings: Settings,
    llm: LLMClient,
    request: ChatRequest,
    message: str,
    trace: TurnTrace,
    redaction: Redaction | None = None,
) -> tuple[IntakePayload | None, IntakeFacts | None, list[str]]:
    attachments = request.attachments or []
    urls = _urls_in(message)
    if not attachments and not urls:
        return None, None, []
    sources: list[IntakeSource] = []
    texts: list[str] = []
    images: list[str] = []
    for attachment in attachments[:4]:
        kind = "pdf" if "pdf" in attachment.media_type else ("image" if attachment.media_type.startswith("image/") else "text")
        if kind == "pdf":
            text = _pdf_text(attachment)
            sources.append(IntakeSource(name=attachment.name, kind="pdf", status="read" if text else "failed", note=""))
            if text:
                texts.append(f"[{attachment.name}]\n{text[:6000]}")
        elif kind == "image":
            sources.append(IntakeSource(name=attachment.name, kind="image", status="read", note=""))
            images.append(f"data:{attachment.media_type};base64,{attachment.data}")
        else:
            try:
                import base64

                text = base64.b64decode(attachment.data).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                text = ""
            sources.append(IntakeSource(name=attachment.name, kind="text", status="read" if text else "failed", note=""))
            if text:
                texts.append(f"[{attachment.name}]\n{text[:6000]}")
    providers = Providers(settings, trace)
    try:
        for url in urls[:3]:
            text = await providers.page_text(url)
            sources.append(IntakeSource(name=url[:120], kind="url", status="read" if text else "failed", note=""))
            if text:
                texts.append(f"[{url}]\n{text[:6000]}")
    finally:
        await providers.aclose()
    if not texts and not images:
        payload = IntakePayload(sources=sources, filled=[], places=[])
        return payload, None, []
    system = skill("intake")
    user = "\n\n".join(texts)[:24000] or "The traveller sent image(s); read them."
    try:
        facts = await llm.json(
            purpose="intake",
            system=system,
            user=user,
            schema=IntakeFacts,
            images=images or None,
            max_tokens=1200,
        )
    except LLMError:
        facts = None
    filled = [
        name
        for name in ("destination", "origin", "start_date", "end_date", "days", "party_size", "budget", "currency")
        if facts and getattr(facts, name, None) not in (None, "")
    ]
    payload = IntakePayload(sources=sources, filled=filled, places=(facts.places if facts else [])[:12])
    return payload, facts, images


def _apply_intake(context: TripContext, facts: IntakeFacts | None) -> TripContext:
    if facts is None:
        return context
    updated = context.model_copy(deep=True)
    for field in ("destination", "origin", "start_date", "end_date", "days", "party_size", "budget", "currency"):
        value = getattr(facts, field, None)
        if value not in (None, ""):
            setattr(updated, field, value)
    if facts.preferences:
        updated.preferences = list(dict.fromkeys([*updated.preferences, *facts.preferences]))[:16]
    if facts.hard_constraints:
        updated.hard_constraints = list(dict.fromkeys([*updated.hard_constraints, *facts.hard_constraints]))[:16]
    return updated


def _urls_in(message: str) -> list[str]:
    found: list[str] = []
    for token in (message or "").split():
        candidate = token.strip("()[]<>,.\"'")
        if candidate.startswith(("http://", "https://")) and candidate not in found:
            found.append(candidate)
    return found[:3]


def _pdf_text(attachment: BufferedAttachment) -> str:
    try:
        import base64
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(base64.b64decode(attachment.data)))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:10])[:8000]
    except Exception:  # noqa: BLE001
        return ""


async def collect_turn(
    deps: Dependencies, request: ChatRequest, *, session_id: str, user_message: str
) -> list[TurnEvent]:
    """Drain a turn into a list (the evaluation runner and the desk use this)."""
    events: list[TurnEvent] = []
    async for event in run_turn(deps, request, session_id=session_id, user_message=user_message):
        events.append(event)
    return events
