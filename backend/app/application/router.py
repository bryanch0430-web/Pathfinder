"""System 1 router: typed decision, confidence and affected parts.

Priority: confident rules -> Jev (when configured) -> writer model -> deterministic
rules. The rules decide alone only when a message carries one unambiguous signal
(``rules_decide_alone``) and the injection screen flags nothing; every other
message goes to the model layers, which see a compact prompt (``router_prompt``).
All layers return the same `RouteDecision`; a lower layer is always able to take
over. Proposal TC-10: the shortcut is what makes a routed turn cheaper than the
full pipeline.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Sequence

from app.application.llm_gateway import LLMGateway
from app.application.prompt_context import plan_outline, recent_turns, trip_slots
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import Intent, Message, RouteDecision, TripContext
from app.security import fence_user_message, screen_message

#: rationale on a decision the rules made without asking a model
RULES_FIRST = "rules-first: one unambiguous signal"

MODIFY_VERBS = r"(change|swap|replace|switch|move|add|remove|drop|cancel|upgrade|downgrade|shorten|extend|make it|turn it|adjust|update)"


def looks_like_question(message: str) -> bool:
    """A traveller asking about a stop, including a sentence that only ends with ?."""
    text = (message or "").strip()
    if not text:
        return False
    if text.endswith("?"):
        return True
    return bool(re.match(ASK_STARTERS, text.lower()))
PLAN_VERBS = r"(plan|planning|itinerary|organi[sz]e|arrange|build|create|design|route|schedule)"
ASK_STARTERS = r"^(what|where|when|which|who|why|how|is|are|can|could|do|does|should|will|would|any)\b"

# --- rules-first gate ---------------------------------------------------------
# The rules answer alone only when the message carries one unambiguous signal.
# Everything else (hedges, mixed signals, bare fragments, non-English text,
# anything the injection screen flags) still goes to Jev or the writer model.
_HEDGES = (
    r"\b(not sure|maybe|perhaps|i don'?t know|dunno|whatever|surprise me|never ?mind|"
    r"something|somewhere|anything|thinking (of|about)|what if|what about|rather)\b"
)
_COMMAND_QUESTION = r"^(please\b|(can|could|would|will) (you|we)\b)"
_WORD_MODIFY = rf"\b{MODIFY_VERBS}\b"
_TRAVEL_ANCHORS = (
    r"\b(hotels?|hostels?|ryokan|rooms?|stay|check[- ]?(in|out)|flights?|fly|trains?|stations?|airport|"
    r"tickets?|fares?|tram|ferry|bus|metro|mtr|subway|taxi|weather|rain\w*|forecast|typhoon|"
    r"temperature|umbrellas?|budget|cost|price|pay|total|day\s*\d+|(first|second|third|last) "
    r"(day|morning|afternoon|evening|night)|walk\w*|open|opening|close[sd]?|hours|museums?|"
    r"temples?|shrines?|castle|park|market|restaurants?|cafe|food|meals?|lunch|dinner|breakfast|"
    r"vegetarian|vegan|halal|stops?|trip|visit\w*|morning|afternoon|evening)\b"
)
_TRIP_DETAIL = (
    r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|a)\s+(days?|nights?|weeks?)\b|"
    r"\b(week|weekend|fortnight)\b"
)


class RuleRouter:
    def route(
        self,
        *,
        message: str,
        context: TripContext,
        plan_summary: dict[str, Any] | None,
        history: Sequence[Message],
    ) -> RouteDecision:
        text = message.strip()
        lower = text.lower()
        has_plan = plan_summary is not None
        missing = context.missing_slots()

        affected = _affected_parts(lower)
        is_question = bool(re.match(ASK_STARTERS, lower)) or text.endswith("?")
        modify_hit = bool(re.search(MODIFY_VERBS, lower))
        plan_hit = bool(re.search(PLAN_VERBS, lower)) or bool(re.search(r"\b\d+\s*(day|night)s?\b", lower))
        travel_hit = bool(re.search(r"\b(trip|travel|holiday|vacation|visit|go to|going to)\b", lower))

        if is_question and not modify_hit:
            intent = Intent.ASK
            confidence = 0.9 if has_plan else 0.82
            reason = "question"
        elif modify_hit and has_plan and not _looks_like_new_trip(lower):
            intent = Intent.MODIFY
            confidence = 0.86
            reason = "change_current_plan"
        elif plan_hit or travel_hit:
            intent = Intent.PLAN
            confidence = 0.84 if (context.destination or missing) else 0.6
            reason = "new_trip_request"
        else:
            intent = Intent.UNCLEAR
            confidence = 0.45
            reason = "no_travel_signal"

        if intent == Intent.MODIFY and not has_plan:
            intent = Intent.UNCLEAR
            confidence = 0.7
            reason = "no_plan_to_modify"

        need_clarification = bool(missing) or intent == Intent.UNCLEAR
        if intent == Intent.PLAN and missing:
            need_clarification = True
            reason = "missing_" + "_".join(missing[:2])

        return RouteDecision(
            intent=intent,
            confidence=confidence,
            need_clarification=need_clarification,
            reason_code=reason,
            affected_parts=affected if intent == Intent.MODIFY else [],
            missing_slots=missing,
            related_to_plan=has_plan,
            rationale="rule-based fallback",
        )


class LLMRouter:
    def __init__(self, gateway: LLMGateway, settings: Settings):
        self.gateway = gateway
        self.settings = settings

    async def route(
        self,
        *,
        message: str,
        context: TripContext,
        plan_summary: dict[str, Any] | None,
        history: Sequence[Message],
    ) -> RouteDecision:
        if not self.gateway.available:
            raise RuntimeError("writer unavailable")
        data = await self.gateway.json("router", user=router_prompt(message, context, plan_summary, history), purpose="router")
        intent = str(data.get("intent", "unclear")).lower()
        if intent == "clarify":
            intent = "unclear"
        if intent not in {"plan", "modify", "ask", "unclear"}:
            intent = "unclear"
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        affected = [str(part) for part in data.get("affected_parts") or [] if isinstance(part, str)]
        missing = [str(slot) for slot in data.get("missing_slots") or [] if isinstance(slot, str)]
        if not missing:
            missing = context.missing_slots()
        return RouteDecision(
            intent=Intent(intent),
            confidence=max(0.0, min(1.0, confidence)),
            need_clarification=bool(data.get("need_clarification")) or intent == "unclear",
            reason_code=str(data.get("reason_code") or "model_route"),
            affected_parts=affected,
            missing_slots=missing,
            related_to_plan=bool(data.get("related_to_plan", plan_summary is not None)),
            rationale=str(data.get("rationale") or ""),
        )


def router_prompt(
    message: str,
    context: TripContext,
    plan_summary: dict[str, Any] | None,
    history: Sequence[Message],
) -> str:
    """What the model router reads: trip slots, a day-by-day outline of the plan
    (names and times, no ids or statuses), earlier turns and the fenced message.

    The current message is already the last history row; it is sent once, fenced.
    """
    return (
        f"Today: {date.today().isoformat()}\n"
        f"Trip: {trip_slots(context)}\n"
        f"Current plan: {plan_outline(plan_summary, times=False)}\n"
        f"Earlier turns:\n{recent_turns(history, message)}\n\n"
        + fence_user_message(message)
    )


class System1Router:
    def __init__(
        self,
        *,
        gateway: LLMGateway,
        settings: Settings,
        decision_client: Any | None = None,
        use_model: bool = True,
    ):
        self.settings = settings
        self.gateway = gateway
        self.llm = LLMRouter(gateway, settings)
        self.rules = RuleRouter()
        self.decision_client = decision_client
        self.use_model = use_model

    async def route(
        self,
        *,
        message: str,
        context: TripContext,
        plan_summary: dict[str, Any] | None,
        history: Sequence[Message],
    ) -> RouteDecision:
        ruled = self.rules.route(message=message, context=context, plan_summary=plan_summary, history=history)
        if self.use_model and rules_first_applies(
            message, ruled, context=context, plan_summary=plan_summary, settings=self.settings
        ):
            # Rules first: a message with one unambiguous signal is decided here,
            # with no Jev or writer call. A message the injection screen flags,
            # or one the rules are unsure about, takes the model path below.
            decided = ruled.model_copy(update={"rationale": RULES_FIRST})
            return _gate(decided, context, plan_summary, self.settings, source="rules")
        if self.use_model and self.decision_client is not None:
            try:
                decision = await self.decision_client.route(
                    message=message,
                    context=context,
                    plan_summary=plan_summary,
                    history=history,
                )
                if decision is not None:
                    return _gate(decision, context, plan_summary, self.settings, source="jev")
            except CanaryLeak:
                raise
            except Exception:
                pass
        if self.use_model and self.gateway.available:
            try:
                decision = await self.llm.route(
                    message=message,
                    context=context,
                    plan_summary=plan_summary,
                    history=history,
                )
                return _gate(decision, context, plan_summary, self.settings, source="model")
            except CanaryLeak:
                raise
            except Exception:
                pass
        return _gate(ruled, context, plan_summary, self.settings, source="rules")


def _gate(
    decision: RouteDecision,
    context: TripContext,
    plan_summary: dict[str, Any] | None,
    settings: Settings,
    *,
    source: str,
) -> RouteDecision:
    """Apply the confidence gate and deterministic safety rules to any router."""
    missing = context.missing_slots()
    intent = decision.intent
    if plan_summary is None and intent == Intent.MODIFY:
        intent = Intent.UNCLEAR
    if decision.confidence < settings.router_threshold:
        if not missing and plan_summary is None and intent in {Intent.UNCLEAR, Intent.PLAN}:
            # The context is complete: a short fragment (often a clarify answer)
            # must move to planning, not re-ask a slot that is already filled.
            return decision.model_copy(
                update={
                    "intent": Intent.PLAN,
                    "need_clarification": False,
                    "reason_code": "context_complete",
                    "missing_slots": [],
                }
            )
        return decision.model_copy(
            update={
                "need_clarification": True,
                "reason_code": f"below_threshold_{source}",
                "missing_slots": missing,
            }
        )
    if intent == Intent.PLAN and missing:
        return decision.model_copy(
            update={
                "intent": Intent.PLAN,
                "need_clarification": True,
                "reason_code": "missing_" + "_".join(missing[:2]),
                "missing_slots": missing,
            }
        )
    return decision.model_copy(update={"intent": intent, "missing_slots": missing})


def _affected_parts(lower: str) -> list[str]:
    parts: list[str] = []
    mapping = [
        ("hotel", r"\b(hotel|hostel|stay|accommodation|room|ryokan)\b"),
        ("ticket", r"\b(flight|fly|train|ticket|fare|seat|rail|shinkansen)\b"),
        ("attraction", r"\b(attraction|sight|museum|temple|shrine|park|visit|stop|activity|eat|eating|food|meal|dinner|lunch|breakfast|restaurant|cafe)\b"),
        ("weather", r"\b(weather|rain|forecast|typhoon|temperature)\b"),
        ("budget", r"\b(budget|cost|cheaper|expensive|money|price)\b|\bspend\s+(?:less|more|under|over)\b"),
        ("dates", r"\b(date|dates|day|days|nights|extend|shorten|earlier|later)\b"),
    ]
    for name, pattern in mapping:
        if re.search(pattern, lower):
            parts.append(name)
    day_matches = re.findall(r"\bday\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+day\b", lower)
    for first, second in day_matches:
        value = first or second
        if value:
            parts.append(f"day_{int(value)}")
    # "second day" / "third day"
    ordinal = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
    for word, index in ordinal.items():
        if re.search(rf"\b{word}\s+day\b", lower):
            parts.append(f"day_{index}")
    return sorted(set(parts))


def _looks_like_new_trip(lower: str) -> bool:
    return bool(
        re.search(r"\b(new|another|different)\s+(trip|itinerary|plan)\b", lower)
        or re.search(r"\b(plan|make|create|build)\b.{0,20}\b(trip|itinerary)\b", lower)
    )


def affected_parts_for(message: str) -> list[str]:
    """Public wrapper over the scope rules, shared with the Jev adapter."""
    return _affected_parts(message.lower())


def rules_first_applies(
    message: str,
    decision: RouteDecision,
    *,
    context: TripContext,
    plan_summary: dict[str, Any] | None,
    settings: Settings,
) -> bool:
    """The System 1 router's shortcut test: the rules' decision stands with no model call.

    It needs the setting on, a confidence at or above the router threshold, one
    unambiguous signal (``rules_decide_alone``) and nothing the injection screen
    flags, so a suspicious message always gets the fenced, canary-checked path.
    """
    return (
        settings.router_rules_first
        and decision.confidence >= settings.router_threshold
        and rules_decide_alone(message, decision, context=context, plan_summary=plan_summary)
        and not screen_message(message, settings).flagged
    )


def rules_decide_alone(
    message: str,
    decision: RouteDecision,
    *,
    context: TripContext,
    plan_summary: dict[str, Any] | None,
) -> bool:
    """True when the rules' decision is unambiguous enough to skip Jev and the writer.

    Only a plan, modify or ask decision backed by exactly one kind of signal
    qualifies: a question about a named part of the trip, a change verb aimed at
    the current plan, or a planning request with a concrete detail. Hedges,
    mixed signals (a question that also asks for a plan or a change), bare
    fragments and non-English text return False and go to the model router.
    The caller also keeps every message the injection screen flags away from
    this shortcut.
    """
    text = (message or "").strip()
    lower = text.lower()
    words = re.findall(r"[a-z0-9']+", lower)
    if len(words) < 3 or not text.isascii() or re.search(_HEDGES, lower):
        return False
    is_question = bool(re.match(ASK_STARTERS, lower)) or text.endswith("?")
    command_question = bool(re.match(_COMMAND_QUESTION, lower))
    plan_hit = bool(re.search(PLAN_VERBS, lower)) or bool(re.search(r"\b\d+\s*-?\s*(day|night)s?\b", lower))
    modify_hit = bool(re.search(_WORD_MODIFY, lower))
    if decision.intent == Intent.ASK:
        return (
            is_question
            and not plan_hit
            and not modify_hit
            and (bool(re.search(_TRAVEL_ANCHORS, lower)) or _names_a_known_place(lower, context, plan_summary))
        )
    if decision.intent == Intent.MODIFY:
        return (
            plan_summary is not None
            and modify_hit
            and (not is_question or command_question)
            and not re.search(r"\b(new|another|different|fresh)\s+(trip|itinerary|plan)\b", lower)
        )
    if decision.intent == Intent.PLAN:
        if is_question and not command_question:
            return False
        travel_hit = bool(re.search(r"\b(trip|travel|holiday|vacation|visit|go to|going to)\b", lower))
        detail = bool(re.search(_TRIP_DETAIL, lower)) or _names_a_place(text)
        ready = plan_summary is None and not context.missing_slots() and bool(context.destination)
        return (plan_hit or travel_hit) and (detail or ready)
    return False


def _names_a_place(text: str) -> bool:
    """A capitalised word after the first one, e.g. "a weekend in Osaka"."""
    return any(word[0].isupper() and word not in {"I", "I'd", "I'm"} for word in text.split()[1:])


def _names_a_known_place(lower: str, context: TripContext, plan_summary: dict[str, Any] | None) -> bool:
    names = [context.destination or ""]
    if plan_summary:
        names.append(str(plan_summary.get("destination") or ""))
        for day in plan_summary.get("days") or []:
            names.extend(str(item.get("name") or "") for item in day.get("items") or [])
    return any(len(name) >= 3 and name.lower() in lower for name in names)
