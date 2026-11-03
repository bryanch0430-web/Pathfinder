"""System 1 router: typed decision, confidence and affected parts.

Priority: Jev (when configured) -> writer model -> deterministic rules. All three
return the same `RouteDecision`; a lower layer is always able to take over.
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from app.application.llm_gateway import LLMGateway
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import Intent, Message, RouteDecision, TripContext
from app.security import fence_user_message

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
        history_lines = [f"{item.role}: {item.content[:280]}" for item in history[-6:]]
        user = (
            f"Current date: {__import__('datetime').date.today().isoformat()}\n"
            f"Trip context: {context.model_dump_json()}\n"
            f"Current plan: {json.dumps(plan_summary) if plan_summary else 'none'}\n"
            f"Recent history:\n" + ("\n".join(history_lines) or "(none)") + "\n\n"
            + fence_user_message(message)
        )
        data = await self.gateway.json("router", user=user, purpose="router")
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
        decision = self.rules.route(
            message=message,
            context=context,
            plan_summary=plan_summary,
            history=history,
        )
        return _gate(decision, context, plan_summary, self.settings, source="rules")


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
