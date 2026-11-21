import asyncio
import logging
import re
import uuid
from datetime import date, timedelta
from typing import Any, Optional
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.agents.trip_planner.graph import trip_planner_graph
from app.services.llm.errors import is_auth_failure, model_unavailable_message
from app.core.auth import RequestIdentity
from app.models.enums import (
    ConversationSessionStatus,
    ConversationStage,
    MessageRole,
    MessageType,
    OnboardingStatus,
    TripStatus,
)
from sqlalchemy import select

from app.models.trip import ConversationMessage, ConversationSession
from app.repositories.conversation import ConversationRepository
from app.repositories.message import TURN_ORDER, MessageRepository
from app.repositories.trip import TripRepository
from app.schemas.trip import (
    ConversationMessageListResponse,
    ConversationMessageResponse,
    ConversationSessionResponse,
    SendMessageRequest,
    TripResponse,
    TripStateResponse,
)
from app.services.enrichment import draft_conditions
from app.services.seasonality import trip_season
from app.services.trip import trip_service

SEASON_WAIT = 2.0  # seconds a fresh draft may wait for season facts once research is done


async def _await_season(task: "asyncio.Task[dict | None]") -> dict | None:
    """Season facts are optional: a slow or failing lookup never holds up or breaks the draft.
    It usually finished during research already; shield() lets a slow one complete and cache."""
    # Retrieve a late failure so it isn't reported as "Task exception was never retrieved".
    task.add_done_callback(lambda done: done.cancelled() or done.exception())
    try:
        return await asyncio.wait_for(asyncio.shield(task), SEASON_WAIT)
    except asyncio.TimeoutError:
        logger.info("Season facts not ready in %.1fs; drafting without them", SEASON_WAIT)
    except Exception as exc:  # optional facts: never fail the draft over them
        logger.warning("Season facts unavailable: %s", exc)
    return None


def _start_date(preferences: dict | None) -> date | None:
    try:
        return date.fromisoformat(str((preferences or {}).get("start_date") or ""))
    except ValueError:
        return None


_EDIT = re.compile(r"\b(add|swap|move|replace|remove|delete|change|include|skip|drop|make|switch|shift|put|"
                   r"extend|shorten|cut|rearrange|reorder|instead)\b", re.I)
# A yes to an offer ("Want me to move it to day 1?"), or "it's final / save it / update the studio".
_AGREE = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok(ay)?|please|do it|do that|go ahead|go for it|sounds good|"
                    r"let'?s do (it|that)|perfect|great|confirm(ed)?|apply|finali[sz]e|lock it in|save it|"
                    r"that'?s (final|it)|update (the )?(studio|plan|itinerary))\b", re.I)
# "5 days", "a 10-day trip", "two weeks": a trip length. "day 5" is a day of the trip, not a length.
_TRIP_LENGTH = re.compile(r"\b(\d+|a|one|two|three|four|five|six|seven|eight|nine|ten)\s*-?\s*(days?|nights?|weeks?)\b"
                          r"|\bfortnight\b|\bmonth\b", re.I)


# Asking for advice ("should I…?", "is there anything you'd shift?") rather than for a change. "Can you…?",
# "what if we…?", "how about…?" and "why not…?" are requests, so they aren't in here.
_ADVICE = re.compile(r"^\W*(should|shall i|is(?! it possible)|are|does|do you think|which|where|when|what(?! if)|how(?! about)|why(?! not))\b",
                     re.I)
_BUDGET_LOCK = re.compile(r"\b(lock|finali[sz]e|confirm)\b.{0,35}\bbudget\b|\bbudget\b.{0,35}\b(lock|finali[sz]e|confirm)\b", re.I)
_BUDGET_UNLOCK = re.compile(r"\bunlock\b.{0,25}\bbudget\b|\bbudget\b.{0,25}\bunlock\b", re.I)
_TRIP_FINALIZE = re.compile(r"\b(finali[sz]e|confirm|lock)\b.{0,35}\b(trip|itinerary|plan)\b|\bfinali[sz]e it\b", re.I)


def _bare_agreement(message: str) -> bool:
    """A yes to the last offer. "Please add Endo Sushi" is a request, not a yes."""
    text = (message or "").strip()
    if not text or _EDIT.search(text) or len(text) > 48:
        return False
    return bool(re.match(
        r"^\W*(yes|yeah|yep|yup|sure|ok(ay)?|please|do it|go ahead|sounds good|confirm(ed)?)"
        r"(?:\W+(?:please|do it|finalize|finalise|confirm|the trip|it))?\W*$",
        text,
        re.I,
    ))


def _follow_up_mode(message: str, intent: str | None, offered: bool) -> str:
    """What a message about an existing one-shot plan does: "revise" rewrites it, "answer" only replies.

    An edit verb edits, whatever the intent label says (the label comes from a small model and often
    calls "can you move X?" a question), unless the message asks for advice: that gets an answer with
    one concrete offer, and a yes applies it. A yes edits only when your last reply offered a change;
    with nothing pending, "finalize it" is answered: every change is already saved.
    """
    last = re.split(r"(?<=[.!])\s+", message.strip())[-1]
    if _EDIT.search(message) and not (last.endswith("?") and _ADVICE.match(last)):
        return "revise"
    if _AGREE.match(message):
        return "revise" if offered else "answer"
    if intent in ("trip_question", "casual_conversation") or message.rstrip().endswith("?"):
        return "answer"
    return "revise"


def _money(amount: float) -> str:
    return f"{amount:,.0f}" if float(amount).is_integer() else f"{amount:,.2f}"


def _selected(request) -> str | None:
    payload = request.payload if isinstance(request.payload, dict) else {}
    value = payload.get("selected_stop")
    return str(value) if value else None


def mask_for_model(message: str, selected: str | None = None) -> str:
    from app.planning.upgrades import mask_for_model as _mask
    return _mask(message, selected)


class ConversationService:
    """Service handling conversation interactions, ownership validation, and message workflow."""

    def __init__(
        self,
        trip_repo: TripRepository = None,
        conversation_repo: ConversationRepository = None,
        message_repo: MessageRepository = None,
    ):
        self.trip_repo = trip_repo or TripRepository()
        self.conversation_repo = conversation_repo or ConversationRepository()
        self.message_repo = message_repo or MessageRepository()

    async def _person_hold(self, db, trip, session, request: SendMessageRequest):
        """Queue or pause a chat before the planner graph runs. None means the graph should run."""
        from app.desk.policy import HANDOFF_TEXT, HELD_TEXT, asks_for_a_person, desk_state, queue
        if request.message_type == MessageType.UI_ACTION:
            return None
        desk = desk_state(trip.planning_preferences)
        text = request.content or ""
        if desk["status"] == "handling":
            reply, reason = HELD_TEXT, desk
        elif desk["status"] == "queued" or asks_for_a_person(text):
            if desk["status"] != "queued":
                desk = queue(desk, "asked_for_a_person")
                trip.planning_preferences = {**(trip.planning_preferences or {}), "desk": desk}
            reply, reason = HANDOFF_TEXT, desk
        else:
            return None
        assistant_msg = await self.message_repo.create_message(
            db, session_id=session.id, role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT, content=reply, payload={"kind": "DESK", "desk": reason},
        )
        await db.commit()
        await db.refresh(trip)
        await db.refresh(session)
        return TripStateResponse(
            trip=TripResponse.model_validate(trip),
            conversation=ConversationSessionResponse.model_validate(session),
            assistant_message=ConversationMessageResponse.model_validate(assistant_msg),
        )

    async def _last_assistant_text(self, db: AsyncSession, trip_id) -> str | None:
        result = await db.execute(
            select(ConversationMessage.content)
            .join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
            .where(ConversationSession.trip_id == trip_id, ConversationMessage.role == MessageRole.ASSISTANT)
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _apply_short_slot(self, db, trip, request: SendMessageRequest) -> None:
        if request.message_type == MessageType.UI_ACTION:
            return
        from app.planning.upgrades import fill_asked_slot
        filled = fill_asked_slot(
            request.content or "", await self._last_assistant_text(db, trip.id),
            destination=trip.destination, origin=trip.origin_text,
        )
        if filled.get("origin"):
            trip.origin_text = filled["origin"]
        if filled.get("destination"):
            trip.destination = filled["destination"]

    async def _suggestion_turn(self, db, trip, session, request: SendMessageRequest):
        """Named restaurants or sights. This answers even when the writer is not called."""
        from app.routing.places import suggestion_kind, suggestion_reply
        from app.services.place_search import search_places
        kind = suggestion_kind(request.content or "")
        destination = (trip.destination or "").strip()
        if not kind or request.message_type == MessageType.UI_ACTION:
            return None
        query = f"{'restaurant' if kind == 'restaurant' else 'tourist attraction'} in {destination}" if destination else ""
        try:
            places = await search_places(query, kind=kind, limit=6, destination=destination) if destination else []
        except Exception:
            places = []
        text = suggestion_reply(destination, kind, places)
        if places:
            trip.planning_preferences = {**(trip.planning_preferences or {}), "suggested_places": places}
        assistant_msg = await self.message_repo.create_message(
            db, session_id=session.id, role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT, content=text,
            payload={"kind": "PLACE_SUGGESTIONS", "place_kind": kind, "places": places},
        )
        await db.commit()
        await db.refresh(trip)
        await db.refresh(session)
        return TripStateResponse(
            trip=TripResponse.model_validate(trip),
            conversation=ConversationSessionResponse.model_validate(session),
            assistant_message=ConversationMessageResponse.model_validate(assistant_msg),
        )

    async def _fact_turn(self, db, trip, session, request: SendMessageRequest):
        """Weather, opening hours, and booking. The plan is not rewritten."""
        from app.routing.facts import (
            asked_calendar_date, asked_day, asks_across_days, bookings_on_plan, day_span, fact_topics,
            focused_snippets, google_listing, listing_answer, place_in_question, weather_answer,
            web_fact_lines, web_facts,
        )
        from app.services.budget import latest_itinerary_payload
        topics = fact_topics(request.content or "")
        if not topics or request.message_type == MessageType.UI_ACTION:
            return None
        payload = await latest_itinerary_payload(db, trip.id) or {}
        items = [item for day in payload.get("day_plan") or [] for item in day.get("items") or []]
        name = place_in_question(request.content or "", _selected(request), items)
        start = _start_date(trip.planning_preferences)
        day_number = asked_day(request.content or "", trip.duration_days) or 1
        when = asked_calendar_date(request.content or "")
        if when and start and trip.duration_days:
            offset = (when - start).days + 1
            if 1 <= offset <= trip.duration_days:
                day_number = offset
        lines = []
        if "weather" in topics:
            lines.append(await weather_answer(trip.destination, start, day_number, trip.currency or "HKD"))
        span = day_span(request.content or "", trip.duration_days)
        if "booking" in topics and asks_across_days(request.content or "") and span and not name:
            lines.append(await bookings_on_plan(payload.get("day_plan") or [], span, trip.destination or ""))
        elif "hours" in topics or "booking" in topics:
            listing = await google_listing(name, trip.destination or "") if name else None
            lines.append(listing_answer(name, listing, when, topics))
            focus = name or (trip.destination or "")
            ask = " ".join(part for part in (
                focus,
                "opening hours" if "hours" in topics else "",
                "reservation" if "booking" in topics else "",
            ) if part)
            lines.append(web_fact_lines(focused_snippets(await web_facts(ask or request.content or ""), name)))
        text = "\n\n".join(line for line in lines if line)
        assistant_msg = await self.message_repo.create_message(
            db, session_id=session.id, role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT, content=text, payload={"kind": "TRIP_FACTS", "topics": sorted(topics)},
        )
        await db.commit()
        await db.refresh(trip)
        await db.refresh(session)
        return TripStateResponse(
            trip=TripResponse.model_validate(trip),
            conversation=ConversationSessionResponse.model_validate(session),
            assistant_message=ConversationMessageResponse.model_validate(assistant_msg),
        )

    async def _trip_finalization_turn(self, db: AsyncSession, trip, request: SendMessageRequest) -> tuple[str, dict] | None:
        """Offer a compact confirmation, then finalize only after explicit agreement."""
        if trip.status == TripStatus.COMPLETED:
            return "This trip is finalized and read-only. You can view or export it, but its itinerary and budget can no longer be edited.", {"kind": "TRIP_FINALIZED"}
        message = (request.content or "").strip()
        action = (request.payload or {}).get("action")
        asked = (action == "REQUEST_FINALIZE_TRIP" or bool(_TRIP_FINALIZE.search(message))
                 or ("budget" not in message.lower() and bool(re.search(r"\bfinali[sz]e\b", message, re.I))))
        confirmed = action == "CONFIRM_FINALIZE_TRIP" or _bare_agreement(message)
        agreed = confirmed or (bool(_AGREE.match(message)) and _bare_agreement(message) and not asked)
        if not asked and not agreed:
            return None
        from app.services.budget import budget_response, latest_itinerary_payload
        payload = await latest_itinerary_payload(db, trip.id) or {}
        offered = False
        if agreed:
            previous = await db.execute(
                select(ConversationMessage).join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
                .where(ConversationSession.trip_id == trip.id, ConversationMessage.role == MessageRole.ASSISTANT)
                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc()).limit(1))
            last = previous.scalar_one_or_none()
            offered = bool(last and (last.payload or {}).get("offer_finalize_trip"))
            if not offered and not asked:
                return None
        if trip.status != TripStatus.PLANNING or not (payload.get("graph") or {}).get("nodes"):
            return "Let's create an itinerary before finalizing the trip.", {"kind": "TRIP_EVENT"}
        if payload.get("copilot") and payload["copilot"].get("status") != "complete":
            return "Finish the guided day plan first, then we can finalize the trip.", {"kind": "TRIP_EVENT"}
        if agreed and offered:
            await trip_service.finalize_trip(db, trip)
            return "Trip finalized. Your itinerary and budget are now read-only; you can view or export the final plan.", {
                "kind": "TRIP_FINALIZED", "changes": [{"status": "done", "text": "Trip finalized"}]}
        budget = await budget_response(db, trip, payload=payload)
        places = list(dict.fromkeys(node.get("name") for node in payload["graph"]["nodes"]
                                    if node.get("kind") == "stop" and node.get("name")))
        start = (trip.planning_preferences or {}).get("start_date")
        try:
            end = (date.fromisoformat(start) + timedelta(days=(trip.duration_days or 1) - 1)).isoformat() if start else None
        except ValueError:
            end = None
        dates = f"{start} to {end}" if end else "Dates not set"
        amount = budget.locked_total if budget.is_locked else budget.projected_total
        cost = f"{amount:,.2f} {budget.currency}" + (" final" if budget.is_locked else " estimated")
        if budget.unpriced_count:
            cost += f"; {budget.unpriced_count} costs still unknown"
        summary = (f"Before I finalize this trip, please review: {trip.duration_days or 0} days; "
                   f"{', '.join(places[:6]) or trip.destination}; {dates}; budget {cost}. "
                   "Finalizing makes the plan and budget read-only. Reply 'yes, finalize' to confirm, or keep editing.")
        return summary, {"kind": "TRIP_EVENT", "offer_finalize_trip": True}

    async def _copilot_context(self, db: AsyncSession, trip, request: SendMessageRequest) -> dict:
        from app.services.budget import budget_response, budget_target, latest_copilot_state, traveler_rates
        copilot = await latest_copilot_state(db, trip.id)
        if copilot:
            # Amounts typed into the budget planner are the truth the agent plans against.
            copilot["rates"].update(await traveler_rates(db, trip.id))
            ledger = await budget_response(db, trip)
            from app.agents.trip_planner.copilot import engine
            raw = engine.baseline(copilot) + sum(engine.day_cost(copilot, day["items"]) for day in copilot["days"])
            copilot["ledger_adjustment"] = round(float(ledger.projected_total) - raw, 2)
            copilot["budget"] = float(ledger.target_amount) if ledger.target_amount is not None else None
            copilot["summary"] = engine.budget_status(copilot)
        focus = (request.payload or {}).get("copilot_day")
        return {"copilot": copilot, "currency": trip.currency, "budget_target": await budget_target(db, trip.id),
                "focus_day": focus if type(focus) is int else None}

    async def _budget_turn(self, db: AsyncSession, trip, request: SendMessageRequest,
                           last_reply: str | None = None) -> tuple[str, dict] | None:
        """Handle budget finalization before either planner rewrites a trip."""
        from app.models.budget import BudgetPlan
        from app.services.budget import budget_response, lock_budget, unlock_budget

        action = (request.payload or {}).get("action")
        message = (request.content or "").strip()
        unlock = action == "UNLOCK_BUDGET" or bool(_BUDGET_UNLOCK.search(message))
        lock = action == "LOCK_BUDGET" or bool(_BUDGET_LOCK.search(message))
        if not (lock or unlock) and _AGREE.match(message) and last_reply and _BUDGET_LOCK.search(last_reply):
            lock = True
        if not (lock or unlock) and _AGREE.match(message):
            offered = await db.execute(
                select(ConversationMessage).join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
                .where(ConversationSession.trip_id == trip.id, ConversationMessage.role == MessageRole.ASSISTANT)
                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc()).limit(1))
            latest = offered.scalar_one_or_none()
            lock = bool((latest.payload or {}).get("offer_lock_budget")) if latest else False
        plan = await db.get(BudgetPlan, trip.id)
        if unlock:
            if not plan or not plan.is_locked:
                return "Your budget is already editable.", {"kind": "BUDGET_EVENT"}
            await unlock_budget(db, trip)
            return "Budget unlocked. You can update costs and the trip again.", {"kind": "BUDGET_EVENT", "changes": [{"status": "done", "text": "Budget unlocked"}]}
        if lock:
            if trip.status == TripStatus.DRAFT:
                return "Let's make a trip plan and add costs before locking the budget.", {"kind": "BUDGET_EVENT"}
            budget = await budget_response(db, trip)
            if budget.is_locked:
                return f"Your budget is already final at {budget.locked_total:,.2f} {budget.currency}.", {"kind": "BUDGET_EVENT"}
            if budget.unpriced_count:
                return (f"There are {budget.unpriced_count} included costs without amounts. Fill them in, use suggestions, or exclude them in Budget, then I can lock it.",
                        {"kind": "BUDGET_EVENT"})
            if not any(item.is_current and item.is_included for item in budget.items):
                return "Add at least one cost in Budget before locking it.", {"kind": "BUDGET_EVENT"}
            budget = await lock_budget(db, trip)
            label = f"Budget locked at {budget.locked_total:,.2f} {budget.currency}"
            return f"{label}. The costs are read-only now; you can unlock them in Budget while the trip is still being planned.", {
                "kind": "BUDGET_EVENT", "changes": [{"status": "done", "text": label}]}
        if plan and plan.is_locked:
            ops = (request.payload or {}).get("ops") or []
            editing_action = action == "COPILOT_OPS" and any(op.get("op") not in ("goto_day", "next_day", "finish") for op in ops if isinstance(op, dict))
            if editing_action or (_EDIT.search(message) and not _ADVICE.match(message)) or re.search(r"\b(budget|cost|price)\b.*\d", message, re.I):
                return "Your budget is final. Unlock it in Budget before changing costs or the itinerary; I can still answer questions.", {"kind": "BUDGET_EVENT"}
        return None

    async def _apply_brief_budget(self, db: AsyncSession, trip, request: SendMessageRequest) -> None:
        """A submitted trip brief's budget and currency go to the budget ledger."""
        payload = request.payload or {}
        if payload.get("action") == "SUBMIT_TRIP_ONBOARDING":
            from app.services.budget import apply_agent_budget
            await apply_agent_budget(db, trip, payload.get("budget_amount"), payload.get("currency"))

    async def _persist_copilot(self, db: AsyncSession, trip, result: dict) -> None:
        copilot = result.get("copilot")
        if copilot:
            from app.services.budget import apply_agent_budget
            await apply_agent_budget(db, trip, copilot.get("budget"), copilot["currency"])

    async def _history(self, db: AsyncSession, trip_id: uuid.UUID) -> dict:
        """What was said before this message, read before it's saved.

        plan: the itinerary text the studio shows, for revising it in place. last_reply: your last
        message when it wasn't that itinerary (an answer or an offer), so "yes" has something to point
        at. recent: the last four messages, oldest first, for resolving "it" in build-with-agent turns.
        """
        result = await db.execute(
            select(ConversationMessage)
            .join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
            .where(ConversationSession.trip_id == trip_id)
            .order_by(ConversationMessage.created_at.desc(), TURN_ORDER.desc())
        )
        rows = [message for message in result.scalars() if message.content]
        plan = None
        day_plan = None
        for message in rows:
            kind = (message.payload or {}).get("kind")
            if kind == "PLAN_CLEARED":
                break
            if message.role == MessageRole.ASSISTANT and kind == "ITINERARY_GRAPH":
                from app.planning.ground import schedule_text
                day_plan = (message.payload or {}).get("day_plan")
                plan = schedule_text(day_plan) if day_plan else message.content
                break
        last = next((message for message in rows if message.role == MessageRole.ASSISTANT), None)
        last_is_plan = bool(last and (last.payload or {}).get("kind") == "ITINERARY_GRAPH")
        from app.security.pii import mask_pii
        return {
            "plan": mask_pii(plan) if plan else None,
            "day_plan": day_plan,
            "last_reply": mask_pii(last.content) if last and not last_is_plan and last.content else None,
            "recent": [{"role": "you" if m.role == MessageRole.ASSISTANT else "traveler",
                        "text": mask_pii(" ".join(m.content.split())[:600])} for m in rows[:4]][::-1],
        }

    async def _stream_routed_answer(self, db: AsyncSession, trip, session, request: SendMessageRequest, decision, outline: str):
        """A confident question, answered from the plan that already exists. The plan is not edited."""
        import json
        from app.agents.trip_planner.nodes.planning_trip_node import ANSWER_SYSTEM_INSTRUCTION
        from app.routing.turn import clarify_text, lookup_block, snapshot_from, stage_label
        from app.services.llm.service import llm_service

        def sse(event: dict) -> str:
            return f"data: {json.dumps(event)}\n\n"

        yield sse({"type": "stage", "label": stage_label(decision)})
        if decision.need_clarification:
            text = clarify_text(decision)
        else:
            notes = await lookup_block(
                "ask",
                snapshot_from(trip.destination, trip.origin_text, trip.duration_days, True,
                              (trip.planning_preferences or {}).get("start_date"), trip.currency),
                request.content or "", decision.affected_parts, [],
            )
            prompt = f"ITINERARY:\n{outline[:12000]}\n\n{notes}\n\nTRAVELER:\n{request.content or ''}"
            try:
                from app.services.admin_settings import active_instruction
                text = await llm_service.generate(
                    prompt=prompt, system_instruction=active_instruction("answer", ANSWER_SYSTEM_INSTRUCTION), temperature=0.4)
            except Exception as exc:
                logger.warning("Routed answer failed: %s", exc)
                text = "I can answer from the plan you already have, and I won't change it unless you ask me to."
        text = (text or "").strip() or "Tell me which part of the trip you want to talk about."
        payload = {"route": decision.public()}
        assistant_msg = await self.message_repo.create_message(
            db, session_id=session.id, role=MessageRole.ASSISTANT, message_type=MessageType.TEXT,
            content=text, payload=payload,
        )
        await db.commit()
        await db.refresh(trip)
        await db.refresh(session)
        yield sse({"type": "token", "delta": text})
        yield sse({"type": "done", "trip": TripResponse.model_validate(trip).model_dump(mode="json"),
                   "conversation": ConversationSessionResponse.model_validate(session).model_dump(mode="json"),
                   "assistant_message": ConversationMessageResponse.model_validate(assistant_msg).model_dump(mode="json")})

    async def _stream_copilot_turn(self, db: AsyncSession, trip, session, request: SendMessageRequest,
                               identity: Optional[RequestIdentity], context: dict, recent: list[dict],
                               route: dict | None = None):
        """One build-with-agent turn: live stages, then the map and day panel, then the reply as it's written."""
        import json
        from app.agents.trip_planner.copilot.graph import (
            RESPOND_INSTRUCTION, ClaimGuard, _fallback_reply, hold_reply, reply_prompt, stream_build_with_agent,
        )
        from app.services.admin_settings import active_instruction
        from app.services.llm.service import llm_service

        def sse(event: dict) -> str:
            return f"data: {json.dumps(event)}\n\n"

        starting = not context["copilot"]
        yield sse({"type": "stage", "label": "Getting to know your trip" if starting else "Thinking about that"})
        state = {
            "user_message": mask_for_model(request.content or "", _selected(request)),
            "ui_action": request.payload if request.message_type == MessageType.UI_ACTION else None,
            "destination": trip.destination, "duration_days": trip.duration_days, "origin": trip.origin_text,
            "planning_preferences": trip.planning_preferences or {}, "places_to_visit": trip.places_to_visit or [],
            "user_name": identity.user_name if identity else None, "recent": recent, **context,
        }
        result: dict = {}
        async for kind, value in stream_build_with_agent(state):
            if kind == "stage":
                yield sse({"type": "stage", "label": value})
            else:
                result = value
        from app.services.budget import align_copilot_budget
        await align_copilot_budget(db, trip, result)
        copilot, graph = result["copilot"], result["itinerary_graph"]
        if copilot.get("status") == "complete" and not copilot.get("trip_finalize_offered"):
            result["offer_finalize_trip"] = True
            copilot["trip_finalize_offered"] = True
        # The map and the day panel reflect the decision before the reply starts.
        yield sse({"type": "graph", "graph": graph, "final": True})
        yield sse({"type": "copilot", "copilot": copilot})

        text = result["assistant_response"]  # set only when replaying a day's held reply
        if text:
            yield sse({"type": "token", "delta": text})
        else:
            # Sentences that claim a change the planner didn't make never reach the traveler.
            guard = ClaimGuard(result["facts"])
            try:
                async for token in llm_service.generate_stream(
                    prompt=reply_prompt(result["facts"]),
                    system_instruction=active_instruction("copilot_reply", RESPOND_INSTRUCTION), temperature=0.6,
                ):
                    if safe := guard.feed(token):
                        text += safe
                        yield sse({"type": "token", "delta": safe})
            except Exception as exc:
                logger.warning("Copilot reply stream failed: %s", exc)
            if tail := guard.flush():
                text += tail
                yield sse({"type": "token", "delta": tail})
            if not text.strip():
                text = _fallback_reply(result["facts"])
                yield sse({"type": "token", "delta": text})
            hold_reply(copilot, text.strip())

        trip.status = TripStatus.PLANNING
        session.status = ConversationSessionStatus.COMPLETED
        session.current_stage = ConversationStage.COMPLETE
        await self._persist_copilot(db, trip, {"copilot": copilot})
        copilot_payload = await self._remember_previous(db, trip.id, self._graph_payload({
            "itinerary_graph": graph, "copilot": copilot, "changes": result.get("changes"),
            "offer_finalize_trip": result.get("offer_finalize_trip"),
            **({"route": route} if route else {}),
        }))
        assistant_msg = await self.message_repo.create_message(
            db, session_id=session.id, role=MessageRole.ASSISTANT, message_type=MessageType.TEXT,
            content=text.strip(),
            payload=copilot_payload,
        )
        await db.commit()
        await db.refresh(trip)
        await db.refresh(session)
        yield sse({"type": "done", "trip": TripResponse.model_validate(trip).model_dump(mode="json"),
                   "conversation": ConversationSessionResponse.model_validate(session).model_dump(mode="json"),
                   "assistant_message": ConversationMessageResponse.model_validate(assistant_msg).model_dump(mode="json")})

    @staticmethod
    def _graph_payload(result: dict) -> dict | None:
        if not result.get("itinerary_graph"):
            return None
        payload = {"kind": "ITINERARY_GRAPH", "graph": result["itinerary_graph"]}
        for key in ("copilot", "day_plan", "changes", "offer_lock_budget", "offer_finalize_trip", "route_reality", "route"):
            if result.get(key):
                payload[key] = result[key]
        return payload

    async def _weather_turn(self, db: AsyncSession, trip, session, request: SendMessageRequest, history: dict):
        """Answer a weather question from Open-Meteo. None lets the planner continue."""
        from app.routing.facts import asked_day, fact_topics, weather_answer
        if request.message_type == MessageType.UI_ACTION or "weather" not in fact_topics(request.content or ""):
            return None
        start = _start_date(trip.planning_preferences)
        day_number = asked_day(request.content or "", trip.duration_days) or 1
        place = trip.destination
        for day in history.get("day_plan") or []:
            if day.get("day") == day_number and day.get("base"):
                place = str(day["base"])
                break
        try:
            text = await weather_answer(place, start, day_number, trip.currency or "HKD")
        except Exception as exc:
            logger.info("Weather lookup skipped: %s", type(exc).__name__)
            return None
        assistant_msg = await self.message_repo.create_message(
            db, session_id=session.id, role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT, content=text, payload={"kind": "TRIP_FACTS", "topics": ["weather"]},
        )
        await db.commit()
        await db.refresh(trip)
        await db.refresh(session)
        await db.refresh(assistant_msg)
        return text, assistant_msg

    async def _swap_turn(self, db: AsyncSession, trip, session, request: SendMessageRequest, history: dict):
        """Write a restaurant (or stay, or sight) swap onto the day list. None lets the planner continue."""
        from app.planning.swap import plan_swap, rename_stop
        from app.services.budget import latest_itinerary_payload
        days = history.get("day_plan")
        if not days or request.message_type == MessageType.UI_ACTION:
            return None
        context = " ".join(
            row.get("text") or "" for row in (history.get("recent") or []) if row.get("role") == "you"
        )
        if history.get("last_reply"):
            context = f"{history['last_reply']} {context}"
        stored = await latest_itinerary_payload(db, trip.id) or {}
        previous = stored.get("previous") if isinstance(stored.get("previous"), dict) else {}
        try:
            outcome = await plan_swap(
                days, request.content or "", _selected(request), context, previous.get("day_plan"),
            )
        except Exception as exc:
            logger.info("Place swap skipped: %s", type(exc).__name__)
            return None
        if outcome is None:
            return None
        text = outcome["text"]
        if not outcome.get("days"):
            payload = {"changes": [{"status": "skipped", "text": text}]}
        else:
            from app.services.budget import sync_budget_for_graph
            current = stored
            graph = current.get("graph") if isinstance(current.get("graph"), dict) else {"version": 8, "nodes": [], "edges": []}
            graph = rename_stop(graph, outcome.get("old") or "", outcome.get("new") or "")
            payload = {
                "kind": "ITINERARY_GRAPH",
                "graph": graph,
                "day_plan": outcome["days"],
                "changes": [{"status": "done", "text": text if text.startswith("Put ") else f"Swapped {outcome.get('old')} for {outcome.get('new')}."}],
            }
            if isinstance(current.get("graph"), dict):
                payload["previous"] = {"graph": current["graph"], "day_plan": current.get("day_plan")}
            await sync_budget_for_graph(db, trip.id, graph)
        assistant_msg = await self.message_repo.create_message(
            db, session_id=session.id, role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT, content=text, payload=payload,
        )
        await db.commit()
        await db.refresh(trip)
        await db.refresh(session)
        await db.refresh(assistant_msg)
        return text, payload, assistant_msg

    async def _remember_previous(self, db: AsyncSession, trip_id: uuid.UUID, payload: dict | None) -> dict | None:
        """Keep one earlier plan on the new message so the studio can step back once."""
        if not isinstance(payload, dict) or payload.get("kind") != "ITINERARY_GRAPH" or payload.get("previous"):
            return payload
        from app.services.budget import latest_itinerary_payload
        current = await latest_itinerary_payload(db, trip_id)
        if not isinstance(current, dict) or not isinstance(current.get("graph"), dict):
            return payload
        if current.get("graph") == payload.get("graph"):
            return payload
        remembered = dict(payload)
        remembered["previous"] = {"graph": current["graph"], "day_plan": current.get("day_plan")}
        return remembered

    async def process_message(
        self,
        db: AsyncSession,
        trip_id: uuid.UUID,
        request: SendMessageRequest,
        identity: Optional[RequestIdentity] = None,
    ) -> TripStateResponse:
        """Process user message via LangGraph agent, enforce ownership, update state, and return updated state."""
        # 1. Fetch trip
        trip = await self.trip_repo.get_by_id(db, trip_id)
        if not trip:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Trip with ID '{trip_id}' not found.",
            )

        # 2. Enforce trip-scoped authorization
        trip_service.verify_trip_ownership(trip, identity)
        if (trip.status != TripStatus.COMPLETED and (request.payload or {}).get("action") == "SUBMIT_TRIP_ONBOARDING"
                and trip.status != TripStatus.DRAFT):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Trip details can only be edited before itinerary generation.")
        from app.security.pii import mask_pii
        request.content = mask_pii(request.content or "")

        # 3. Get or create active session
        session = await self.conversation_repo.get_or_create_active_session(
            db, trip.id
        )

        # 4. Save User message
        await self.message_repo.create_message(
            db,
            session_id=session.id,
            role=MessageRole.USER,
            message_type=request.message_type,
            content=request.content,
            payload=request.payload,
        )

        held = await self._person_hold(db, trip, session, request)
        if held:
            return held
        await self._apply_short_slot(db, trip, request)

        prior = await self._history(db, trip.id) if _AGREE.match((request.content or "").strip()) else {}
        control_turn = await self._trip_finalization_turn(db, trip, request)
        if control_turn is None:
            control_turn = await self._budget_turn(db, trip, request, prior.get("last_reply"))
        if control_turn:
            text, payload = control_turn
            assistant_msg = await self.message_repo.create_message(
                db, session_id=session.id, role=MessageRole.ASSISTANT,
                message_type=MessageType.TEXT, content=text, payload=payload)
            await db.commit()
            await db.refresh(trip)
            await db.refresh(session)
            return TripStateResponse(
                trip=TripResponse.model_validate(trip),
                conversation=ConversationSessionResponse.model_validate(session),
                assistant_message=ConversationMessageResponse.model_validate(assistant_msg))

        # 5. The graph handles form onboarding and subsequent conversation.
        user_name = identity.user_name if identity else None
        graph_state = {
            "trip_id": str(trip.id),
            "user_id": str(trip.user_id) if trip.user_id else None,
            "guest_id": str(trip.guest_id) if trip.guest_id else None,
            "user_name": user_name,
            "user_message": mask_for_model(request.content or "", _selected(request)),
            "destination": trip.destination,
            "places_to_visit": trip.places_to_visit or [],
            "planning_preferences": trip.planning_preferences or {},
            "duration_days": trip.duration_days,
            "origin": trip.origin_text,
            "origin_latitude": trip.origin_latitude,
            "origin_longitude": trip.origin_longitude,
            "onboarding_complete": trip.onboarding_status == OnboardingStatus.COMPLETE,
            "planning_started": trip.status != TripStatus.DRAFT,
            "missing_fields": [],
            "assistant_response": "",
            "ui_action": request.payload if request.message_type == MessageType.UI_ACTION else None,
            **await self._copilot_context(db, trip, request),
        }

        graph_result = await trip_planner_graph.ainvoke(graph_state)
        if graph_result.get("itinerary_graph") and not graph_result.get("day_plan"):
            from app.services.trip_document import _legacy_days

            graph_result["day_plan"] = _legacy_days(graph_result["itinerary_graph"],
                                                    trip.duration_days or 1, trip.destination or "Trip")
        if graph_result.get("itinerary_graph") and graph_result.get("day_plan"):
            from app.services.budget import latest_itinerary_payload
            from app.services.route_reality import assess_itinerary
            previous = await latest_itinerary_payload(db, trip.id) or {}
            graph_result["route_reality"] = await assess_itinerary(
                graph_result["day_plan"], trip.destination or "Trip",
                (trip.planning_preferences or {}).get("travel_mode") or "transit",
                (trip.planning_preferences or {}).get("pace") or "balanced",
                previous.get("route_reality"))
        from app.services.budget import align_copilot_budget
        await align_copilot_budget(db, trip, graph_result)
        await self._persist_copilot(db, trip, graph_result)
        await self._apply_brief_budget(db, trip, request)

        # Update Trip entity with extracted fields from graph
        if graph_result.get("user_name") and identity and not identity.user_name:
            identity.user_name = graph_result["user_name"]
        if graph_result.get("destination"):
            trip.destination = graph_result["destination"]
        if graph_result.get("places_to_visit") is not None:
            trip.places_to_visit = graph_result["places_to_visit"]
        if graph_result.get("planning_preferences") is not None:
            trip.planning_preferences = graph_result["planning_preferences"]
        if graph_result.get("duration_days"):
            trip.duration_days = graph_result["duration_days"]
        if graph_result.get("origin"):
            trip.origin_text = graph_result["origin"]

        # The graph validates form data before planning.
        has_origin = bool(
            (trip.origin_text and str(trip.origin_text).strip())
            or (trip.origin_latitude is not None and trip.origin_longitude is not None)
        )

        # Evaluate completion status (strictly destination + duration + origin)
        if graph_result.get("onboarding_complete") and has_origin:
            trip.onboarding_status = OnboardingStatus.COMPLETE
            planned = bool(graph_result.get("itinerary_graph"))
            trip.status = TripStatus.PLANNING if planned else TripStatus.DRAFT
            session.status = ConversationSessionStatus.COMPLETED if planned else ConversationSessionStatus.ACTIVE
            session.current_stage = ConversationStage.COMPLETE if planned else ConversationStage.REVIEW

            origin_clause = f" from {trip.origin_text}" if trip.origin_text else ""
            assistant_text = (
                graph_result.get("assistant_response")
                or f"Perfect! We have a {trip.duration_days}-day trip to {trip.destination}{origin_clause}. We're ready to start planning!"
            )
        else:
            trip.onboarding_status = OnboardingStatus.IN_PROGRESS
            session.status = ConversationSessionStatus.ACTIVE
            session.current_stage = ConversationStage.TRIP_BASICS
            assistant_text = graph_result.get("assistant_response") or "Tell me the basics of your journey."

        route_warnings = (graph_result.get("route_reality") or {}).get("warnings") or []
        impossible = [warning for warning in route_warnings if warning.get("severity") == "impossible"]
        if impossible:
            assistant_text += "\n\nTravel check: " + " ".join(
                f"Day {warning['day']} needs a different base or day: {warning['message']}."
                for warning in impossible[:3])
            graph_result["changes"] = [*graph_result.get("changes", []), *(
                {"status": "skipped", "text": f"Day {warning['day']} needs review: {warning['message']}"}
                for warning in impossible)]

        if graph_result.get("itinerary_graph") and (not graph_result.get("copilot") or graph_result["copilot"].get("status") == "complete"):
            graph_result["offer_finalize_trip"] = True
        assistant_payload = await self._remember_previous(
            db, trip.id, self._graph_payload(graph_result) or graph_result.get("ui_action"))
        assistant_msg = await self.message_repo.create_message(
            db,
            session_id=session.id,
            role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT,
            content=assistant_text,
            payload=assistant_payload,
        )

        if graph_result.get("itinerary_graph"):
            from app.services.budget import sync_budget_for_graph
            await sync_budget_for_graph(db, trip.id, graph_result["itinerary_graph"])

        # 7. Commit and refresh
        await db.commit()
        await db.refresh(trip)
        await db.refresh(session)

        return TripStateResponse(
            trip=TripResponse.model_validate(trip),
            conversation=ConversationSessionResponse.model_validate(session),
            assistant_message=ConversationMessageResponse.model_validate(assistant_msg),
        )

    async def process_message_stream(
        self,
        db_or_trip_id: Any = None,
        trip_id_or_request: Any = None,
        request_or_identity: Any = None,
        identity: Optional[RequestIdentity] = None,
        db: Optional[AsyncSession] = None,
        trip_id: Optional[uuid.UUID] = None,
        request: Optional[SendMessageRequest] = None,
    ):
        """
        Process user message and yield SSE events in real-time token streaming.
        Supports both self-managed database session (production streaming) and
        caller-injected session (offline tests).
        """
        if isinstance(db_or_trip_id, AsyncSession):
            active_db = db_or_trip_id
            target_trip_id = trip_id_or_request
            target_request = request_or_identity
            target_identity = identity
            use_managed_db = False
        else:
            target_trip_id = trip_id if trip_id is not None else db_or_trip_id
            target_request = request if request is not None else trip_id_or_request
            target_identity = identity if identity is not None else request_or_identity
            active_db = db
            use_managed_db = active_db is None

        if use_managed_db:
            from app.core.database import async_session_maker
            async with async_session_maker() as stream_db:
                source = self._process_message_stream_impl(
                    stream_db, target_trip_id, target_request, target_identity
                )
                async for event in self._watch_run(target_trip_id, source):
                    yield event
        else:
            source = self._process_message_stream_impl(
                active_db, target_trip_id, target_request, target_identity
            )
            async for event in self._watch_run(target_trip_id, source):
                yield event

    async def _watch_run(self, trip_id, source):
        """Tell the desk which step is running, then clear it when the turn ends."""
        import json
        from app.desk.activity import set_planner_run
        set_planner_run(str(trip_id), "Reading the message")
        try:
            async for event in source:
                if isinstance(event, str) and '"type": "stage"' in event:
                    line = next((part[5:].strip() for part in event.splitlines() if part.startswith("data:")), "")
                    try:
                        label = json.loads(line).get("label")
                    except json.JSONDecodeError:
                        label = None
                    if isinstance(label, str) and label:
                        set_planner_run(str(trip_id), label)
                yield event
        finally:
            set_planner_run(str(trip_id), None)

    async def _process_message_stream_impl(
        self,
        db: AsyncSession,
        trip_id: uuid.UUID,
        request: SendMessageRequest,
        identity: Optional[RequestIdentity] = None,
    ):
        """Internal generator carrying out message processing and streaming on an active db session."""
        import json
        from app.agents.trip_planner.nodes.understand_user_msg_node import understand_user_message
        from app.agents.trip_planner.nodes.validate_state_node import validate_state
        from app.agents.trip_planner.nodes.respond_to_user_node import respond_to_user
        from app.services.llm.service import llm_service

        # 1. Fetch trip & enforce ownership
        trip = await self.trip_repo.get_by_id(db, trip_id)
        if not trip:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Trip with ID '{trip_id}' not found.",
            )
        trip_service.verify_trip_ownership(trip, identity)
        if (trip.status != TripStatus.COMPLETED and (request.payload or {}).get("action") == "SUBMIT_TRIP_ONBOARDING"
                and trip.status != TripStatus.DRAFT):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Trip details can only be edited before itinerary generation.")
        from app.security.pii import mask_pii
        request.content = mask_pii(request.content or "")

        # 2. Get or create active session
        session = await self.conversation_repo.get_or_create_active_session(db, trip.id)
        # Read before this message is saved: the plan to revise and the exchange "it" and "yes" refer to.
        history = await self._history(db, trip.id) if trip.status != TripStatus.DRAFT else {}

        # 3. Save User message
        await self.message_repo.create_message(
            db,
            session_id=session.id,
            role=MessageRole.USER,
            message_type=request.message_type,
            content=request.content,
            payload=request.payload,
        )

        held = await self._person_hold(db, trip, session, request)
        if held:
            text = held.assistant_message.content or ""
            yield f"data: {json.dumps({'type': 'token', 'delta': text})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'trip': TripResponse.model_validate(trip).model_dump(mode='json'), 'conversation': ConversationSessionResponse.model_validate(session).model_dump(mode='json'), 'assistant_message': ConversationMessageResponse.model_validate(held.assistant_message).model_dump(mode='json')})}\n\n"
            return
        await self._apply_short_slot(db, trip, request)
        swapped = await self._swap_turn(db, trip, session, request, history)
        if swapped:
            text, payload, assistant_msg = swapped
            if payload.get("kind") == "ITINERARY_GRAPH" and payload.get("graph"):
                yield f"data: {json.dumps({'type': 'graph', 'graph': payload['graph'], 'final': True})}\n\n"
            yield f"data: {json.dumps({'type': 'token', 'delta': text})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'trip': TripResponse.model_validate(trip).model_dump(mode='json'), 'conversation': ConversationSessionResponse.model_validate(session).model_dump(mode='json'), 'assistant_message': ConversationMessageResponse.model_validate(assistant_msg).model_dump(mode='json')})}\n\n"
            return
        weather = await self._weather_turn(db, trip, session, request, history)
        if weather:
            text, assistant_msg = weather
            yield f"data: {json.dumps({'type': 'token', 'delta': text})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'trip': TripResponse.model_validate(trip).model_dump(mode='json'), 'conversation': ConversationSessionResponse.model_validate(session).model_dump(mode='json'), 'assistant_message': ConversationMessageResponse.model_validate(assistant_msg).model_dump(mode='json')})}\n\n"
            return

        control_turn = await self._trip_finalization_turn(db, trip, request)
        if control_turn is None:
            control_turn = await self._budget_turn(db, trip, request, history.get("last_reply"))
        if control_turn:
            response, payload = control_turn
            assistant_msg = await self.message_repo.create_message(
                db, session_id=session.id, role=MessageRole.ASSISTANT,
                message_type=MessageType.TEXT, content=response, payload=payload)
            await db.commit()
            await db.refresh(trip)
            await db.refresh(session)
            yield f"data: {json.dumps({'type': 'token', 'delta': response})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'trip': TripResponse.model_validate(trip).model_dump(mode='json'), 'conversation': ConversationSessionResponse.model_validate(session).model_dump(mode='json'), 'assistant_message': ConversationMessageResponse.model_validate(assistant_msg).model_dump(mode='json')})}\n\n"
            return

        # Draft trips move through form -> planning choice -> full itinerary.
        # Neither form nor choice makes an LLM call.
        generating_full_draft = (
            request.message_type == MessageType.UI_ACTION
            and isinstance(request.payload, dict)
            and request.payload.get("action") == "GENERATE_FULL_ITINERARY"
        )
        copilot_context = await self._copilot_context(db, trip, request)
        from app.core.config import settings
        from app.routing.rules import wants_to_clear_plan
        from app.services.admin_settings import router_enabled
        gate = router_enabled()
        if gate and wants_to_clear_plan(request.content or "") and (history.get("plan") or copilot_context.get("copilot")):
            async for event in self._stream_clear_plan(db, trip, session):
                yield event
            return
        starting_build = ((request.payload or {}).get("action") == "START_BUILD_WITH_AGENT"
                          and trip.onboarding_status == OnboardingStatus.COMPLETE)
        if (copilot_context["copilot"] or starting_build) and not generating_full_draft:
            from app.routing.models import Intent
            from app.routing.turn import classify, prepare, should_skip

            payload = request.payload if isinstance(request.payload, dict) else {}
            prepared = prepare(payload) if gate else None
            route_decision = None if prepared is None or should_skip(prepared) else prepared
            action = payload.get("action")
            if (gate and route_decision is None and copilot_context["copilot"]
                    and (request.content or "").strip() and action != "COPILOT_OPS"):
                days = [{"day": day.get("day"), "base": day.get("base"), "items": day.get("items") or []}
                        for day in (copilot_context["copilot"].get("days") or [])]
                route_decision = await classify(
                    request.content or "", destination=trip.destination, origin=trip.origin_text,
                    duration_days=trip.duration_days, has_plan=True, days=days,
                    last_reply=history.get("last_reply"), history=history.get("recent") or [],
                    start_date=(trip.planning_preferences or {}).get("start_date"), currency=trip.currency,
                )
                if (route_decision.intent == Intent.ASK and not route_decision.need_clarification
                        and route_decision.confidence >= 0.8):
                    outline = "\n".join(
                        f"Day {day.get('day')} in {day.get('base')}: "
                        + ", ".join(item.get("name") for item in day.get("items") or [] if isinstance(item, dict))
                        for day in days
                    )
                    async for event in self._stream_routed_answer(db, trip, session, request, route_decision, outline):
                        yield event
                    return
            async for event in self._stream_copilot_turn(
                db, trip, session, request, identity, copilot_context, history.get("recent") or [],
                route=route_decision.public() if route_decision is not None else None,
            ):
                yield event
            return
        if trip.status == TripStatus.DRAFT and not generating_full_draft:
            form_state = {
                "trip_id": str(trip.id),
                "user_name": identity.user_name if identity else None,
                "user_message": mask_for_model(request.content or "", _selected(request)),
                "origin": trip.origin_text,
                "destination": trip.destination,
                "places_to_visit": trip.places_to_visit or [],
                "planning_preferences": trip.planning_preferences or {},
                "duration_days": trip.duration_days,
                "onboarding_complete": trip.onboarding_status == OnboardingStatus.COMPLETE,
                "planning_started": False,
                "ui_action": request.payload if request.message_type == MessageType.UI_ACTION else None,
                "origin_latitude": trip.origin_latitude,
                "origin_longitude": trip.origin_longitude,
                **copilot_context,
            }
            result = await trip_planner_graph.ainvoke(form_state)
            await self._persist_copilot(db, trip, result)
            await self._apply_brief_budget(db, trip, request)
            complete = bool(result.get("onboarding_complete"))
            if complete:
                trip.origin_text = result["origin"]
                trip.destination = result["destination"]
                trip.places_to_visit = result["places_to_visit"]
                trip.planning_preferences = result.get("planning_preferences") or {}
                trip.duration_days = result["duration_days"]
                trip.onboarding_status = OnboardingStatus.COMPLETE
                planned = bool(result.get("itinerary_graph"))
                trip.status = TripStatus.PLANNING if planned else TripStatus.DRAFT
                session.status = ConversationSessionStatus.COMPLETED if planned else ConversationSessionStatus.ACTIVE
                session.current_stage = ConversationStage.COMPLETE if planned else ConversationStage.REVIEW
            else:
                session.current_stage = ConversationStage.TRIP_BASICS

            action = result.get("ui_action")
            assistant_payload = action or self._graph_payload(result)
            response = result.get("assistant_response") or "Tell me the basics of your journey."
            assistant_msg = await self.message_repo.create_message(
                db, session_id=session.id, role=MessageRole.ASSISTANT,
                message_type=MessageType.TEXT, content=response, payload=assistant_payload,
            )
            await db.commit()
            await db.refresh(trip)
            await db.refresh(session)
            yield f"data: {json.dumps({'type': 'metadata', 'destination': trip.destination, 'duration_days': trip.duration_days, 'origin': trip.origin_text, 'onboarding_complete': complete, 'missing_fields': result.get('missing_fields', [])})}\n\n"
            if action:
                yield f"data: {json.dumps({'type': 'action', 'action': action['action'], 'payload': action})}\n\n"
            if result.get("itinerary_graph"):
                yield f"data: {json.dumps({'type': 'graph', 'graph': result['itinerary_graph'], 'final': True})}\n\n"
            if result.get("copilot"):
                yield f"data: {json.dumps({'type': 'copilot', 'copilot': result['copilot']})}\n\n"
            for word in response.split(" "):
                yield f"data: {json.dumps({'type': 'token', 'delta': word + ' '})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'trip': TripResponse.model_validate(trip).model_dump(mode='json'), 'conversation': ConversationSessionResponse.model_validate(session).model_dump(mode='json'), 'assistant_message': ConversationMessageResponse.model_validate(assistant_msg).model_dump(mode='json')})}\n\n"
            return

        # 4. Handle UI actions if present
        if request.message_type == MessageType.UI_ACTION and request.payload:
            action = request.payload.get("action")
            if action == "SET_LOCATION" or ("origin_latitude" in request.payload and "origin_longitude" in request.payload):
                lat = request.payload.get("latitude") if request.payload.get("latitude") is not None else request.payload.get("origin_latitude")
                lon = request.payload.get("longitude") if request.payload.get("longitude") is not None else request.payload.get("origin_longitude")
                trip.origin_latitude = lat
                trip.origin_longitude = lon
                if request.payload.get("label"):
                    trip.origin_text = request.payload.get("label")
                elif request.payload.get("origin_text"):
                    trip.origin_text = request.payload.get("origin_text")
                elif not trip.origin_text and lat is not None and lon is not None:
                    trip.origin_text = f"{lat}, {lon}"
            elif action == "SET_ORIGIN" or request.payload.get("origin_text"):
                val = request.payload.get("origin_text") or request.payload.get("origin") or request.payload.get("label")
                if val:
                    trip.origin_text = str(val).strip()

        # 5. Build state and understand user message
        user_name = identity.user_name if identity else None
        current_state = {
            "trip_id": str(trip.id),
            "user_id": str(trip.user_id) if trip.user_id else None,
            "guest_id": str(trip.guest_id) if trip.guest_id else None,
            "user_name": user_name,
            "user_message": mask_for_model(request.content or "", _selected(request)),
            "destination": trip.destination,
            "duration_days": trip.duration_days,
            "places_to_visit": trip.places_to_visit or [],
            "planning_preferences": trip.planning_preferences or {},
            "origin": trip.origin_text,
            "origin_latitude": trip.origin_latitude,
            "origin_longitude": trip.origin_longitude,
            "onboarding_complete": trip.onboarding_status == OnboardingStatus.COMPLETE,
            "missing_fields": [],
            "assistant_response": "",
        }

        # Understand user message
        # This button already has validated journey fields. Skip a second
        # intent-parsing call so the draft can enter the real token stream.
        basics_before = (trip.destination, trip.duration_days, trip.origin_text)
        understood_updates = {} if generating_full_draft else await understand_user_message(current_state)
        raw_message = request.content or ""
        message = raw_message
        if history.get("plan") and understood_updates.get("duration_days") and not _TRIP_LENGTH.search(raw_message):
            # "Move X to day 2" read as a 2-day trip would cut the trip and redraft it from scratch.
            understood_updates.pop("duration_days")
        message = mask_for_model(raw_message, _selected(request))
        current_state.update(understood_updates)

        # What this turn changed, shown under the reply (a budget now; the day plan's diff after a revision).
        changes: list[dict] = []
        if understood_updates.get("budget_amount") and trip.onboarding_status == OnboardingStatus.COMPLETE:
            from app.services.budget import apply_agent_budget
            amount, currency = understood_updates["budget_amount"], understood_updates.get("budget_currency") or trip.currency
            if await apply_agent_budget(db, trip, amount, currency):
                changes.append({"status": "done", "text": f"Budget target set to {_money(amount)} {trip.currency}"})
            else:
                changes.append({"status": "skipped", "text": f"Kept the budget in {trip.currency}: costs are already "
                                                             f"entered in {trip.currency}, and amounts are never converted"})

        if current_state.get("user_name") and identity and not identity.user_name:
            identity.user_name = current_state["user_name"]
        if current_state.get("destination"):
            proposed = current_state["destination"]
            from app.planning.upgrades import map_hit, needs_place_confirmation, place_decision
            if needs_place_confirmation(proposed, basics_before[0]):
                stored, question = place_decision(proposed, basics_before[0], map_hit(proposed))
                if question:
                    trip.destination = stored
                    assistant_msg = await self.message_repo.create_message(
                        db, session_id=session.id, role=MessageRole.ASSISTANT,
                        message_type=MessageType.TEXT, content=question,
                        payload={"kind": "PLACE_CONFIRM", "proposed": proposed},
                    )
                    await db.commit()
                    await db.refresh(trip)
                    await db.refresh(session)
                    yield f"data: {json.dumps({'type': 'token', 'delta': question})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'trip': TripResponse.model_validate(trip).model_dump(mode='json'), 'conversation': ConversationSessionResponse.model_validate(session).model_dump(mode='json'), 'assistant_message': ConversationMessageResponse.model_validate(assistant_msg).model_dump(mode='json')})}\n\n"
                    return
                current_state["destination"] = stored or proposed
            trip.destination = current_state["destination"]
        if current_state.get("duration_days"):
            trip.duration_days = current_state["duration_days"]
        if current_state.get("origin"):
            trip.origin_text = current_state["origin"]

        # Validate state
        validation = validate_state(current_state)
        current_state.update(validation)

        is_complete = validation["onboarding_complete"]
        # A follow-up on an existing one-shot plan revises it (or answers) instead of starting over.
        previous_plan = None
        if is_complete and not generating_full_draft and basics_before == (trip.destination, trip.duration_days, trip.origin_text):
            previous_plan = history.get("plan")
        last_reply = history.get("last_reply")
        decision = None
        stored_plan = None
        mode = "draft"
        if previous_plan and gate:
            from app.routing.turn import classify, mode_for
            from app.services.budget import latest_itinerary_payload

            stored_plan = await latest_itinerary_payload(db, trip.id) or {}
            decision = await classify(
                message, destination=trip.destination, origin=trip.origin_text, duration_days=trip.duration_days,
                has_plan=True, days=stored_plan.get("day_plan") or [], last_reply=last_reply,
                history=history.get("recent") or [],
                start_date=(trip.planning_preferences or {}).get("start_date"), currency=trip.currency,
            )
            mode = mode_for(decision, message, has_plan=True)
            if mode == "rebuild":
                # A new trip, said as such: research again instead of patching the old plan.
                previous_plan = None
                mode = "draft"
            elif mode == "draft":
                mode = _follow_up_mode(message, understood_updates.get("intent"), offered="?" in (last_reply or ""))
        elif previous_plan:
            mode = _follow_up_mode(message, understood_updates.get("intent"), offered="?" in (last_reply or ""))
        if previous_plan and mode == "revise" and changes and not _EDIT.search(raw_message):
            mode = "answer"  # a budget on its own is saved, not a reason to rewrite the plan
        answering = mode == "answer"

        from app.planning.upgrades import render_plan, translate_request
        language = translate_request(raw_message)
        if language and previous_plan:
            translated = render_plan(previous_plan, language, (trip.planning_preferences or {}).get("place_names"))
            assistant_msg = await self.message_repo.create_message(
                db, session_id=session.id, role=MessageRole.ASSISTANT,
                message_type=MessageType.TEXT, content=translated, payload={"kind": "TRANSLATION", "language": language},
            )
            await db.commit()
            await db.refresh(trip)
            await db.refresh(session)
            yield f"data: {json.dumps({'type': 'token', 'delta': translated})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'trip': TripResponse.model_validate(trip).model_dump(mode='json'), 'conversation': ConversationSessionResponse.model_validate(session).model_dump(mode='json'), 'assistant_message': ConversationMessageResponse.model_validate(assistant_msg).model_dump(mode='json')})}\n\n"
            return

        # 6. Emit metadata event immediately
        yield f"data: {json.dumps({'type': 'metadata', 'destination': trip.destination, 'duration_days': trip.duration_days, 'origin': trip.origin_text, 'onboarding_complete': is_complete, 'missing_fields': validation.get('missing_fields', []), 'user_name': current_state.get('user_name')})}\n\n"

        # 7. Execute node based on completion status
        user_name_display = current_state.get("user_name") or "Friend / Traveler"
        accumulated_text = ""
        ui_action = None

        if decision is not None:
            from app.routing.turn import stage_label
            yield f"data: {json.dumps({'type': 'stage', 'label': stage_label(decision)})}\n\n"

        if mode == "clear":
            async for event in self._stream_clear_plan(db, trip, session):
                yield event
            return
        if mode == "clarify" and decision is not None:
            from app.routing.turn import clarify_text
            accumulated_text = clarify_text(decision)
            ui_action = {"route": decision.public()}
            yield f"data: {json.dumps({'type': 'token', 'delta': accumulated_text})}\n\n"
        elif is_complete:
            trip.onboarding_status = OnboardingStatus.COMPLETE
            trip.status = TripStatus.PLANNING
            session.status = ConversationSessionStatus.COMPLETED
            session.current_stage = ConversationStage.COMPLETE

            logger.info(
                "🔥 STREAM planning started: destination=%s duration=%s origin=%s",
                trip.destination,
                trip.duration_days,
                trip.origin_text,
            )

            from app.agents.trip_planner.nodes.planning_trip_node import (
                ANSWER_SYSTEM_INSTRUCTION,
                PLAN_GENERATION_SYSTEM_INSTRUCTION,
                REVISION_SYSTEM_INSTRUCTION,
                build_followup_prompt,
                build_plan_generation_prompt,
                execute_planning_subgraph,
            )

            candidates: list = []
            local_days = None
            local_before = None
            local_notes: list[str] = []
            if previous_plan and not answering:
                from app.planning.edit_patch import apply_edit, parse_edit
                from app.services.budget import latest_itinerary_payload

                stored = stored_plan or await latest_itinerary_payload(db, trip.id) or {}
                local_before = stored.get("day_plan")
                edit = parse_edit(message, local_before)
                if edit and local_before:
                    patched, local_notes = apply_edit(local_before, edit)
                    if local_notes and patched is not local_before:
                        local_days = patched
            if local_days is not None:
                accumulated_text = " ".join(local_notes)
                yield f"data: {json.dumps({'type': 'stage', 'label': 'Updating one stop'})}\n\n"
                yield f"data: {json.dumps({'type': 'token', 'delta': accumulated_text})}\n\n"
            elif previous_plan:
                yield f"data: {json.dumps({'type': 'stage', 'label': 'Thinking about that' if answering else 'Applying that change'})}\n\n"
                from app.services.budget import budget_response
                current_budget = await budget_response(db, trip)
                scope, notes = "", ""
                if decision is not None and not answering:
                    from app.routing.turn import lookup_block, plan_names, scope_instructions, snapshot_from
                    scope = scope_instructions(decision.affected_parts)
                    notes = await lookup_block(
                        "modify",
                        snapshot_from(trip.destination, trip.origin_text, trip.duration_days, True,
                                      (trip.planning_preferences or {}).get("start_date"), trip.currency),
                        message, decision.affected_parts, plan_names((stored_plan or {}).get("day_plan")),
                    )
                elif decision is not None and answering:
                    from app.routing.turn import lookup_block, plan_names, snapshot_from
                    notes = await lookup_block(
                        "ask",
                        snapshot_from(trip.destination, trip.origin_text, trip.duration_days, True,
                                      (trip.planning_preferences or {}).get("start_date"), trip.currency),
                        message, decision.affected_parts, plan_names((stored_plan or {}).get("day_plan")),
                    )
                from app.planning.ground import stop_facts
                pinned = stop_facts(history.get("day_plan"), _selected(request))
                traveler = f"{pinned}\n\n{message}".strip() if pinned else message
                plan_prompt = build_followup_prompt(
                    previous_plan, traveler, trip.destination, trip.duration_days,
                    trip.origin_text, trip.planning_preferences or {}, last_reply=last_reply,
                    saved=[change["text"] for change in changes if change["status"] == "done"],
                    budget_context={"currency": current_budget.currency,
                                    "target": str(current_budget.target_amount) if current_budget.target_amount is not None else None,
                                    "projected_total": str(current_budget.projected_total),
                                    "unpriced_count": current_budget.unpriced_count,
                                    "is_locked": current_budget.is_locked},
                    scope=scope, tool_notes=notes, answering=answering,
                )
                from app.services.admin_settings import active_instruction
                system_instruction = active_instruction(
                    "answer" if answering else "revise",
                    ANSWER_SYSTEM_INSTRUCTION if answering else REVISION_SYSTEM_INSTRUCTION,
                )
            else:
                # Fresh draft: destination research and candidate curation first.
                yield f"data: {json.dumps({'type': 'stage', 'label': 'Researching possible stops'})}\n\n"
                # Typical weather for the travel months runs alongside the research (no LLM call).
                season_task = asyncio.create_task(trip_season(
                    trip.destination, _start_date(trip.planning_preferences), trip.duration_days))
                # Forecast days and public holidays for the same dates, also alongside the research.
                conditions_task = asyncio.create_task(draft_conditions(
                    trip.destination, _start_date(trip.planning_preferences), trip.duration_days, trip.currency))
                planning_result = await execute_planning_subgraph(
                    destination=trip.destination,
                    duration_days=trip.duration_days,
                    origin=trip.origin_text,
                    places_to_visit=trip.places_to_visit or [],
                    planning_preferences=trip.planning_preferences or {},
                )
                # Awaited together, so the pair can't hold the draft for more than SEASON_WAIT in total.
                planning_result["season"], planning_result["conditions"] = await asyncio.gather(
                    _await_season(season_task), _await_season(conditions_task))
                candidates = planning_result.get("candidates", [])
                logger.info("🔥 PLANNING DATA READY: candidates=%d", len(candidates))
                yield f"data: {json.dumps({'type': 'stage', 'label': 'Writing your day-by-day draft'})}\n\n"
                from app.services.admin_settings import active_instruction
                plan_prompt = build_plan_generation_prompt(
                    planning_result=planning_result,
                    destination=trip.destination,
                    duration_days=trip.duration_days,
                    origin=trip.origin_text,
                    user_name=current_state.get("user_name"),
                    places_to_visit=trip.places_to_visit or [],
                    planning_preferences=trip.planning_preferences or {},
                )
                system_instruction = active_instruction("plan", PLAN_GENERATION_SYSTEM_INSTRUCTION)

            # Stream the reply; itinerary text also redraws the map as it arrives.
            from app.agents.trip_planner.nodes.extract_itinerary_node import build_itinerary_graph
            last_graph_length = 0
            last_graph_shape = None
            # A revision still writes the full day list for the studio. The chat only receives the opening.
            hide_days = bool(previous_plan) and not answering and local_days is None
            pending_line = ""
            days_started = False
            from app.agents.trip_planner.nodes.planning_trip_node import revision_visible
            try:
                if local_days is not None:
                    token_stream = None
                else:
                    token_stream = llm_service.generate_stream(
                        prompt=plan_prompt,
                        system_instruction=system_instruction,
                        temperature=0.5,
                    )
                async for token in token_stream or []:
                    accumulated_text += token
                    if hide_days:
                        if days_started:
                            continue
                        pending_line += token
                        while "\n" in pending_line:
                            line, pending_line = pending_line.split("\n", 1)
                            visible, started = revision_visible(line)
                            if started:
                                days_started = True
                                pending_line = ""
                                break
                            if visible:
                                yield f"data: {json.dumps({'type': 'token', 'delta': visible + chr(10)})}\n\n"
                        continue
                    yield f"data: {json.dumps({'type': 'token', 'delta': token})}\n\n"
                    if not answering and len(accumulated_text) - last_graph_length >= 700:
                        last_graph_length = len(accumulated_text)
                        preview = build_itinerary_graph(
                            markdown=accumulated_text,
                            origin=trip.origin_text,
                            destination=trip.destination or "Trip",
                            duration_days=trip.duration_days or 1,
                            candidates=candidates,
                            requested_places=trip.places_to_visit or [],
                        )
                        shape = (
                            tuple((node["name"], len(node["nearby_places"])) for node in preview["nodes"]),
                            tuple((edge["source"], edge["target"]) for edge in preview["edges"]),
                        )
                        if shape != last_graph_shape:
                            last_graph_shape = shape
                            yield f"data: {json.dumps({'type': 'graph', 'graph': preview, 'final': False})}\n\n"
                if hide_days and pending_line and not days_started:
                    visible, started = revision_visible(pending_line)
                    if visible and not started:
                        yield f"data: {json.dumps({'type': 'token', 'delta': visible})}\n\n"
            except Exception as exc:
                logger.warning("Error during plan stream generation: %s", exc)
                if not accumulated_text:
                    c_names = [c["name"] for c in candidates if isinstance(c, dict) and "name" in c]
                    c_clause = f" featuring {', '.join(c_names)}" if c_names else ""
                    accumulated_text = (
                        model_unavailable_message(exc) if is_auth_failure(exc) else
                        "Sorry, I couldn't finish that just now. Could you send it again?" if previous_plan else
                        f"Here is a first-draft outline for your {trip.duration_days}-day trip to {trip.destination}{c_clause}. "
                        f"Let's refine the sequence and activities together!"
                    )
                    yield f"data: {json.dumps({'type': 'token', 'delta': accumulated_text})}\n\n"

            logger.info("🔥 STREAM PLAN COMPLETED (mode=%s)", "answer" if answering else "revise" if previous_plan else "draft")
            plan_text = accumulated_text
            if hide_days:
                opening, _started = revision_visible(plan_text)
                accumulated_text = opening
            if not answering:
                # A final, evidence-backed graph before persisting keeps the map in sync with this reply.
                from app.agents.trip_planner.nodes.extract_itinerary_node import day_plan_changes, extract_itinerary
                from app.services.budget import latest_itinerary_payload

                # The day plan the studio shows now: the extractor reuses its names, and the receipt diffs against it.
                before = local_before if local_days is not None else (
                    (await latest_itinerary_payload(db, trip.id) or {}).get("day_plan") if previous_plan else None)
                if local_days is not None:
                    from app.planning.edit_patch import graph_from_days
                    day_plan = local_days
                    itinerary_graph = graph_from_days(
                        day_plan, origin=trip.origin_text, destination=trip.destination or "Trip",
                    )
                    changes += [{"status": "done", "text": note} for note in local_notes]
                    graph_result = {"itinerary_graph": itinerary_graph, "day_plan": day_plan}
                else:
                    graph_result = await extract_itinerary({
                        **current_state,
                        "assistant_response": plan_text,
                        "candidates": candidates,
                        "places_to_visit": trip.places_to_visit or [],
                        "known_places": [item["name"] for day in before or [] for item in day["items"]],
                    })
                    itinerary_graph = graph_result.get("itinerary_graph")
                    day_plan = graph_result.get("day_plan")
                if local_days is None and day_plan:
                    from app.planning.ground import fill_vague_stops, listings_for_cities, plan_cities
                    catalogues = await listings_for_cities(plan_cities(day_plan))
                    if catalogues:
                        day_plan = fill_vague_stops(day_plan, catalogues, only_unmappable=bool(previous_plan))
                if local_days is None and day_plan:
                    from app.planning.tickets import attach_trip_tickets
                    day_plan, ticket_note = attach_trip_tickets(
                        day_plan, origin=trip.origin_text, destination=trip.destination,
                        start=_start_date(trip.planning_preferences), duration_days=trip.duration_days,
                    )
                    if ticket_note and not previous_plan and ticket_note not in accumulated_text:
                        accumulated_text += "\n\n" + ticket_note
                        yield f"data: {json.dumps({'type': 'token', 'delta': chr(10) + chr(10) + ticket_note})}\n\n"
                if local_days is None and day_plan:
                    from app.planning.upgrades import apply_plan_upgrades
                    day_plan, plan_notes = apply_plan_upgrades(
                        day_plan, trip.planning_preferences,
                        known=set(trip.places_to_visit or []), city=trip.destination or "",
                    )
                    moves = [note for note in plan_notes if note.startswith("Moved ")]
                    if moves:
                        accumulated_text += "\n\n" + " ".join(moves)
                        changes += [{"status": "done", "text": note} for note in moves]
                if mode == "revise" and local_days is None and decision is not None and before and day_plan:
                    from app.routing.minimal import apply_minimal_change
                    day_plan, itinerary_graph, _kept = apply_minimal_change(
                        before, day_plan, (stored_plan or {}).get("graph"), itinerary_graph,
                        decision.affected_parts, message=message,
                    )
                if itinerary_graph and not day_plan and not previous_plan:
                    from app.services.trip_document import _legacy_days

                    day_plan = _legacy_days(itinerary_graph, trip.duration_days or 1, trip.destination or "Trip")
                if previous_plan:
                    if day_plan is None:
                        # Extraction failed on a revision: keep the last good day plan rather than losing it.
                        day_plan = before
                        changes.append({"status": "skipped", "text": "Couldn't read the new day-by-day plan, so the "
                                                                     "studio still shows the previous one. Try again?"})
                    elif before and local_days is None:
                        changes += day_plan_changes(before, day_plan) or [
                            {"status": "skipped", "text": "The day-by-day plan didn't change"}]
                if itinerary_graph:
                    ui_action = {"kind": "ITINERARY_GRAPH", "graph": itinerary_graph, "day_plan": day_plan}
                    if previous_plan:
                        ui_action["change_only"] = True
                    if not previous_plan:
                        ui_action["offer_finalize_trip"] = True
                    # The studio can draw the new days before the slower road check finishes.
                    yield f"data: {json.dumps({'type': 'graph', 'graph': itinerary_graph, 'final': True})}\n\n"
                    if day_plan:
                        from app.services.route_reality import assess_itinerary, changed_days
                        previous_route = (await latest_itinerary_payload(db, trip.id) or {}).get("route_reality")
                        touched = changed_days(before, day_plan) if previous_plan else None
                        if touched is None or touched:
                            yield f"data: {json.dumps({'type': 'stage', 'label': 'Checking travel time'})}\n\n"
                        ui_action["route_reality"] = await assess_itinerary(
                            day_plan, trip.destination or "Trip",
                            (trip.planning_preferences or {}).get("travel_mode") or "transit",
                            (trip.planning_preferences or {}).get("pace") or "balanced", previous_route, touched)
                        impossible = [warning for warning in ui_action["route_reality"]["warnings"]
                                      if warning["severity"] == "impossible"]
                        if impossible:
                            from app.planning.edit_patch import graph_from_days
                            from app.planning.travel_repair import repair_once
                            repaired, repair_notes, again = repair_once(
                                day_plan, ui_action["route_reality"],
                                (trip.planning_preferences or {}).get("travel_mode") or "transit",
                                (trip.planning_preferences or {}).get("pace") or "balanced",
                            )
                            if repair_notes and repaired is not day_plan:
                                day_plan = repaired
                                itinerary_graph = graph_from_days(
                                    day_plan, origin=trip.origin_text, destination=trip.destination or "Trip",
                                )
                                ui_action["graph"] = itinerary_graph
                                ui_action["day_plan"] = day_plan
                                ui_action["route_reality"]["warnings"] = again
                                yield f"data: {json.dumps({'type': 'graph', 'graph': itinerary_graph, 'final': True})}\n\n"
                            still = [warning for warning in again if warning.get("severity") == "impossible"]
                            if repair_notes:
                                notice = "\n\nTravel check: " + " ".join(repair_notes)
                                changes += [{"status": "done" if not still else "skipped", "text": note} for note in repair_notes]
                            else:
                                notice = "\n\nTravel check: " + " ".join(
                                    f"Day {warning['day']} needs a different base or day: {warning['message']}."
                                    for warning in still[:3])
                                changes += [{"status": "skipped", "text":
                                             f"Day {warning['day']} needs review: {warning['message']}"}
                                            for warning in still]
                            accumulated_text += notice
                            yield f"data: {json.dumps({'type': 'token', 'delta': notice})}\n\n"
        else:
            trip.onboarding_status = OnboardingStatus.IN_PROGRESS
            session.status = ConversationSessionStatus.ACTIVE
            if not trip.destination or not trip.duration_days:
                session.current_stage = ConversationStage.TRIP_BASICS
            else:
                session.current_stage = ConversationStage.ORIGIN

            respond_result = await respond_to_user(current_state)
            accumulated_text = respond_result.get("assistant_response", "")
            ui_action = respond_result.get("ui_action")

            if ui_action:
                yield f"data: {json.dumps({'type': 'action', 'action': ui_action.get('action'), 'payload': ui_action})}\n\n"

            if accumulated_text:
                words = accumulated_text.split(" ")
                for i, w in enumerate(words):
                    chunk = w if i == len(words) - 1 else w + " "
                    yield f"data: {json.dumps({'type': 'token', 'delta': chunk})}\n\n"

        if not accumulated_text.strip():
            accumulated_text = (
                "Updated the plan. The full days are in the studio."
                if changes else "I'm ready to help plan your trip! Where are you thinking of going?"
            )
        if mode == "revise":
            from app.desk.policy import HANDOFF_TEXT, desk_state, note_unchanged_edit
            unchanged = any("didn't change" in str(change.get("text") or "") for change in changes)
            desk = note_unchanged_edit(desk_state(trip.planning_preferences), raw_message, unchanged=unchanged)
            trip.planning_preferences = {**(trip.planning_preferences or {}), "desk": desk}
            if desk["status"] == "queued" and HANDOFF_TEXT not in accumulated_text:
                accumulated_text += "\n\n" + HANDOFF_TEXT
                changes.append({"status": "skipped", "text": "This chat is waiting for a person."})
        if changes:
            ui_action = {**(ui_action or {}), "changes": changes}
        if decision is not None:
            ui_action = {**(ui_action or {}), "route": decision.public()}
        ui_action = await self._remember_previous(db, trip.id, ui_action)

        # 8. Save assistant message and commit to database
        assistant_msg = await self.message_repo.create_message(
            db,
            session_id=session.id,
            role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT,
            content=accumulated_text.strip(),
            payload=ui_action,
        )

        if ui_action and ui_action.get("kind") == "ITINERARY_GRAPH":
            from app.services.budget import sync_budget_for_graph
            await sync_budget_for_graph(db, trip.id, ui_action["graph"])

        await db.commit()
        await db.refresh(trip)
        await db.refresh(session)

        # 9. Emit final done event with full persistent state
        done_payload = {
            "type": "done",
            "trip": TripResponse.model_validate(trip).model_dump(mode="json"),
            "conversation": ConversationSessionResponse.model_validate(session).model_dump(mode="json"),
            "assistant_message": ConversationMessageResponse.model_validate(assistant_msg).model_dump(mode="json"),
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    async def _stream_clear_plan(self, db: AsyncSession, trip, session):
        """Remove the saved itinerary and leave the brief, so a new draft can start."""
        import json
        text = "The itinerary is deleted. Your origin, destination, and dates are still saved, so you can draft again or build one day at a time."
        payload = {
            "kind": "PLAN_CLEARED",
            "action": "SHOW_PLANNING_CHOICE",
            "options": ["full_itinerary", "build_with_agent"],
            "route": {
                "intent": "modify", "confidence": 0.95, "source": "rules", "reason": "clear_plan",
                "affected_parts": ["plan"], "need_clarification": False, "missing_slots": [], "minimal": False,
            },
        }
        if trip.onboarding_status == OnboardingStatus.COMPLETE:
            trip.status = TripStatus.DRAFT
        session.status = ConversationSessionStatus.ACTIVE
        session.current_stage = ConversationStage.REVIEW
        assistant_msg = await self.message_repo.create_message(
            db, session_id=session.id, role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT, content=text, payload=payload,
        )
        await db.commit()
        await db.refresh(trip)
        await db.refresh(session)
        yield f"data: {json.dumps({'type': 'token', 'delta': text})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'trip': TripResponse.model_validate(trip).model_dump(mode='json'), 'conversation': ConversationSessionResponse.model_validate(session).model_dump(mode='json'), 'assistant_message': ConversationMessageResponse.model_validate(assistant_msg).model_dump(mode='json')})}\n\n"

    async def mark_useful(
        self, db: AsyncSession, trip_id: uuid.UUID, identity: Optional[RequestIdentity],
        useful: bool | None, stars: int | None = None,
    ) -> dict:
        trip = await self.trip_repo.get_by_id(db, trip_id)
        if not trip:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found.")
        trip_service.verify_trip_ownership(trip, identity)
        if trip.status != TripStatus.COMPLETED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You can rate a trip after it is finalized.")
        preferences = dict(trip.planning_preferences or {})
        if useful is None:
            preferences["useful"] = None
            preferences.pop("stars", None)
        else:
            preferences["useful"] = useful
            if useful and stars is not None:
                preferences["stars"] = stars
            elif not useful:
                preferences.pop("stars", None)
        trip.planning_preferences = preferences
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(trip, "planning_preferences")
        await db.commit()
        return {"useful": preferences.get("useful"), "stars": preferences.get("stars")}

    async def undo_last_plan(self, db: AsyncSession, trip_id: uuid.UUID, identity: Optional[RequestIdentity]):
        trip = await self.trip_repo.get_by_id(db, trip_id)
        if not trip:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found.")
        trip_service.verify_trip_ownership(trip, identity)
        from app.services.budget import latest_itinerary_payload, sync_budget_for_graph
        current = await latest_itinerary_payload(db, trip.id) or {}
        previous = current.get("previous") if isinstance(current, dict) else None
        if not isinstance(previous, dict) or not isinstance(previous.get("graph"), dict):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="There is no earlier plan to restore.")
        session = await self.conversation_repo.get_or_create_active_session(db, trip.id)
        restored = {"kind": "ITINERARY_GRAPH", "graph": previous["graph"]}
        if previous.get("day_plan"):
            restored["day_plan"] = previous["day_plan"]
        message = await self.message_repo.create_message(
            db, session_id=session.id, role=MessageRole.ASSISTANT, message_type=MessageType.TEXT,
            content="Restored the previous plan.", payload=restored,
        )
        await sync_budget_for_graph(db, trip.id, restored["graph"])
        await db.commit()
        await db.refresh(message)
        return message

    async def get_trip_messages(
        self,
        db: AsyncSession,
        trip_id: uuid.UUID,
        identity: Optional[RequestIdentity] = None,
    ) -> ConversationMessageListResponse:
        """Retrieve all ordered conversation messages for a trip after verifying ownership."""
        trip = await self.trip_repo.get_by_id(db, trip_id)
        if not trip:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Trip with ID '{trip_id}' not found.",
            )

        # Enforce trip-scoped authorization
        trip_service.verify_trip_ownership(trip, identity)

        session = await self.conversation_repo.get_active_session_by_trip_id(
            db, trip_id
        )
        if not session:
            return ConversationMessageListResponse(messages=[])

        messages = await self.message_repo.get_messages_by_session_id(db, session.id)
        return ConversationMessageListResponse(
            messages=[ConversationMessageResponse.model_validate(m) for m in messages]
        )

    async def get_or_create_itinerary_graph(
        self, db: AsyncSession, trip_id: uuid.UUID, identity: Optional[RequestIdentity] = None,
    ) -> dict:
        """Extract and save a graph for a trip planned before graph persistence existed."""
        trip = await self.trip_repo.get_by_id(db, trip_id)
        if not trip:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found.")
        trip_service.verify_trip_ownership(trip, identity)
        if trip.onboarding_status != OnboardingStatus.COMPLETE:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plan the trip first.")
        session = await self.conversation_repo.get_active_session_by_trip_id(db, trip_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip conversation not found.")
        messages = await self.message_repo.get_messages_by_session_id(db, session.id)
        saved = next((message for message in messages if (message.payload or {}).get("kind") == "ITINERARY_GRAPH"), None)
        if saved and (saved.payload["graph"].get("version") or 0) >= 8:
            return saved.payload["graph"]

        form_index = next((index for index, message in enumerate(messages)
                           if (message.payload or {}).get("action") == "SUBMIT_TRIP_ONBOARDING"), -1)
        candidates = [message for message in messages[form_index + 1:]
                      if message.role == MessageRole.ASSISTANT and message.content
                      and (message.payload or {}).get("action") != "SHOW_ONBOARDING_FORM"]
        plan = saved or (candidates[0] if form_index >= 0 and candidates else max(
            candidates, key=lambda message: len(message.content), default=None,
        ))
        if not plan:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Itinerary not found.")

        if saved and isinstance(saved.payload.get("graph", {}).get("nodes"), list):
            from app.agents.trip_planner.nodes.extract_itinerary_node import build_itinerary_graph

            old_stops = [node for node in saved.payload["graph"]["nodes"]
                         if isinstance(node, dict) and node.get("kind") != "origin"]
            graph = build_itinerary_graph(
                markdown=plan.content, origin=trip.origin_text,
                destination=trip.destination, duration_days=trip.duration_days,
                requested_places=trip.places_to_visit or [], extracted={"stops": old_stops},
            )
        else:
            from app.agents.trip_planner.nodes.extract_itinerary_node import extract_itinerary

            result = await extract_itinerary({
                "assistant_response": plan.content, "origin": trip.origin_text,
                "destination": trip.destination, "duration_days": trip.duration_days,
                "places_to_visit": trip.places_to_visit or [],
            })
            graph = result["itinerary_graph"]
        plan.payload = {"kind": "ITINERARY_GRAPH", "graph": graph}
        from app.services.budget import sync_budget_for_graph
        await sync_budget_for_graph(db, trip.id, graph)
        await db.commit()
        return graph


conversation_service = ConversationService()
