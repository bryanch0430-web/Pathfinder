"""One user turn, one path. The orchestrator is the only place that knows the
full sequence: screen -> understand -> route -> gate -> plan/modify/ask/clarify.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, AsyncIterator

from app.application.asking import AskPath
from app.application.clarification import Clarifier, SLOT_LABELS
from app.application.llm_gateway import LLMGateway
from app.application.memory import MemoryService
from app.application.modification import ModifyPath
from app.application.planning import PlanPath
from app.application.router import System1Router
from app.application.toolbox import Toolbox
from app.application.understanding import understand
from app.config import Settings
from app.domain.errors import CanaryLeak, PathfinderError, PlanInvalidError
from app.domain.models import (
    Intent,
    Message,
    RouteDecision,
    TripContext,
    TurnEvent,
    new_id,
    utcnow,
)
from app.infrastructure.observability import start_trace
from app.infrastructure.store import SQLiteStore
from app.security import SAFE_REPLY, screen_message


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

    async def handle_turn(
        self,
        *,
        session_id: str,
        message: str,
        locale: str = "en",
        scope_item_id: str | None = None,
        scope_label: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        request_id = new_id("req")
        tracer = start_trace(self.settings, request_id)
        if self.tracer_proxy is not None:
            self.tracer_proxy.bind(tracer)
        session = self.store.get_session(session_id)
        if session is None:
            session = self.store.create_session("")
        session_id = session.id
        screen = screen_message(message, self.settings)
        if screen.flagged:
            for category in screen.categories:
                self.store.log_injection(category, screen.max_severity.value)
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

        context = self.store.get_context(session_id) or TripContext(session_id=session_id, language=locale)
        context.language = locale or context.language
        plan = self.store.get_plan(session_id)
        intent: Intent = Intent.UNCLEAR
        try:
            with tracer.span("understand"):
                try:
                    context = await understand(message, context, self.gateway)
                except CanaryLeak:
                    raise
                except Exception:
                    pass
                self.store.save_context(context)
                if context.preferences:
                    self.memory.remember_preferences(session.client_token, context)

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
                scope_item_id
                and plan is not None
                and re.search(r"\b(about|this|it|selected|detail|details|info|tell)\b", message.lower())
            )
            if scoped_action:
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
            elif scoped_question and decision.intent != Intent.MODIFY:
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

            if decision.intent == Intent.PLAN:
                async for event in self._plan(
                    context=context,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                ):
                    yield event
            elif decision.intent == Intent.MODIFY:
                async for event in self._modify(
                    plan=plan,
                    context=context,
                    message=message,
                    decision=decision,
                    session_id=session_id,
                    tracer=tracer,
                    request_id=request_id,
                    scope_item_id=scope_item_id,
                ):
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
        except CanaryLeak:
            self.store.log_injection("canary_leak", "high")
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
            yield TurnEvent(event="error", data={"code": exc.code, "message": str(exc), "retryable": True})
            tracer.finish(status="error")
        except PathfinderError as exc:
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=str(exc), intent=intent)
            )
            yield TurnEvent(event="error", data=exc.as_dict())
            tracer.finish(status="error")
        except Exception as exc:
            message_text = "Something went wrong while handling this turn. You can retry."
            self.store.add_message(
                Message(session_id=session_id, role="assistant", content=message_text, intent=intent)
            )
            yield TurnEvent(
                event="error",
                data={"code": "internal_error", "message": message_text, "detail": str(exc)[:200], "retryable": True},
            )
            tracer.finish(status="error")
        finally:
            tracer.finish(status="ok")
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
                        }
                        for generation in tracer.generations
                    ],
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

    async def _plan(self, *, context, session_id, tracer, request_id) -> AsyncIterator[TurnEvent]:
        matches = self.memory.recall(context)
        if matches:
            tracer.metadata["memory_matches"] = [round(score, 3) for score, _ in matches]
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        def emit(event_name: str, data: dict[str, Any]) -> None:
            queue.put_nowait((event_name, data))

        toolbox = Toolbox(path="plan", tracer=tracer, store=self.store, request_id=request_id)
        yield TurnEvent(event="progress", data={"phase": "start", "message": "Planning the trip.", "progress": 5})
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
        async for event in _drain(queue, task):
            yield event
        plan = await task
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

        toolbox = Toolbox(path="modify", tracer=tracer, store=self.store, request_id=request_id)
        yield TurnEvent(event="progress", data={"phase": "start", "message": "Applying the smallest change.", "progress": 5})
        task = asyncio.create_task(
            self.modify_path.run(
                plan=plan,
                context=context,
                message=message,
                route_parts=decision.affected_parts,
                toolbox=toolbox,
                tracer=tracer,
                emit=emit,
                memory_summaries=self.memory.memory_summaries(self.memory.recall(context)),
                scope_item_id=scope_item_id,
            )
        )
        async for event in _drain(queue, task):
            yield event
        updated, diff = await task
        self.store.save_plan(updated, updated.change_summary)
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
        toolbox = Toolbox(path="ask", tracer=tracer, store=self.store, request_id=request_id)
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
                changed.append(str(entry.get("name") or (found[1].name if found else "an item")))
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
        parts.append("Updated " + ", ".join(changed) + ".")
    if removed:
        parts.append("Removed " + ", ".join(removed) + ".")
    if hotel:
        parts.append(hotel)
    parts.extend(extras)
    return " ".join(parts) if parts else "No itinerary items needed to change."


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
