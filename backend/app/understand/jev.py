"""Optional Jev (TypeSafe system-one) turn router.

The endpoint is not an OpenAI chat endpoint: it takes a state string plus typed
questions and returns calibrated answers. Jev only decides the path; the analyst
still extracts slots, constraints and the reply, so a wrong Jev answer can cost a
clarification but cannot invent a plan.

Every failure returns None and the analyst's own route is kept.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

import httpx

from app.config import Settings
from app.domain.models import ChatMessage, TripContext, TripPlan

INTENT_CRITERIA = {
    "plan": "Create a brand-new itinerary from scratch",
    "modify": "Change, refine or repair an itinerary that already exists",
    "ask": "Ask a question about an existing itinerary without changing it",
    "unclear": "Essential trip details are missing, or this is not a travel request",
}


@dataclass(frozen=True)
class JevDecision:
    intent: str
    confidence: float
    need_clarification: bool
    reason: str


def _confidence(answer: object, intent: str) -> float | None:
    if not isinstance(answer, dict):
        return None
    probabilities = answer.get("probabilities")
    if isinstance(probabilities, dict):
        try:
            return max(0.0, min(1.0, float(probabilities.get(intent))))
        except (TypeError, ValueError):
            pass
    try:
        return max(0.0, min(1.0, float(answer.get("confidence"))))
    except (TypeError, ValueError):
        return None


def _noul(answer: object) -> float | None:
    if not isinstance(answer, dict):
        return None
    try:
        return max(0.0, min(1.0, float(answer.get("noul"))))
    except (TypeError, ValueError):
        return None


def _state(
    context: TripContext,
    plan: TripPlan | None,
    message: str,
    history: Sequence[ChatMessage],
) -> str:
    trip = {
        "destination": context.destination,
        "origin": context.origin,
        "start": context.start_date,
        "end": context.end_date,
        "days": context.days,
        "party": context.party_size,
        "budget": context.budget,
        "currency": context.currency,
        "preferences": context.preferences[:8],
        "constraints": context.hard_constraints[:8],
        "profile": context.profile.model_dump() if context.profile else None,
    }
    outline = None
    if plan is not None:
        outline = {
            "summary": plan.summary,
            "days": [
                {"date": day.date, "stops": [item.name for item in day.items]}
                for day in plan.days
            ],
        }
    recent = [
        f"{row.role}: {(row.content or '')[:240]}"
        for row in history[-6:]
        if row.role in {"user", "assistant"}
    ]
    return (
        f"Trip context: {json.dumps(trip, ensure_ascii=False)}\n"
        f"Current plan: {json.dumps(outline, ensure_ascii=False) if outline else 'none'}\n"
        f"Recent history:\n" + ("\n".join(recent) or "(none)") + f"\n\nTraveller: {message}"
    )


async def jev_route(
    *,
    settings: Settings,
    context: TripContext,
    plan: TripPlan | None,
    message: str,
    history: Sequence[ChatMessage],
) -> JevDecision | None:
    """Ask Jev for the path, or None when it is not configured or did not answer."""
    url = (settings.decision_api_url or "").strip()
    key = (settings.decision_api_key or "").strip()
    if not (url and key and settings.decision_model):
        return None
    payload = {
        "model": settings.decision_model,
        "state": _state(context, plan, message, history),
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": "What does the traveller want from the assistant on this turn?",
                "criteria": INTENT_CRITERIA,
            },
            "needs_detail": {
                "type": "noul",
                "instructions": (
                    "The traveller still has to supply a destination, departure city, start date, "
                    "length, party size, budget, or a currency they named "
                    "before an itinerary can be built"
                ),
            },
        },
    }
    headers = {
        "Authorization": f"{settings.decision_auth_prefix} {key}".strip(),
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=max(5.0, settings.decision_timeout_seconds)) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        return None
    intent_answer = answers.get("intent") or {}
    intent = str(intent_answer.get("choice") or "").strip().lower()
    if intent == "clarify":
        intent = "unclear"
    if intent not in {"plan", "modify", "ask", "unclear"}:
        return None
    confidence = _confidence(intent_answer, intent)
    if confidence is None:
        return None
    detail = _noul(answers.get("needs_detail"))
    need = intent == "unclear" or bool(detail is not None and detail >= 0.5)
    return JevDecision(intent=intent, confidence=confidence, need_clarification=need, reason=f"jev_{intent}")
