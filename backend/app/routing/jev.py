"""TypeSafe Jev adapter.

The systemone endpoint is not a chat endpoint. It takes a state plus typed
questions and returns a calibrated choice. Any failure returns None so the
writer, then the rules, can take the turn.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.routing.models import Intent, RouteDecision, TripSnapshot
from app.routing.rules import affected_parts_for

INTENT_CRITERIA = {
    "plan": "Create a brand-new itinerary from scratch",
    "modify": "Change, refine or repair an itinerary that already exists",
    "ask": "Ask a question about an existing itinerary without changing it",
    "unclear": "Essential trip details are missing, or this is not a travel request",
}


class JevDecisionClient:
    name = "jev"

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str = "jev-latest",
        path: str = "/v1/systemone",
        timeout: float = 8.0,
        auth_prefix: str = "Bearer",
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.auth_prefix = auth_prefix
        base = api_url.rstrip("/")
        suffix = (path or "").strip()
        self.endpoint = base if not suffix or base.endswith(suffix) else base + "/" + suffix.lstrip("/")

    def payload(
        self,
        *,
        message: str,
        snapshot: TripSnapshot,
        plan_summary: dict[str, Any] | None,
        history: list[dict[str, str]] | None,
    ) -> dict[str, Any]:
        lines = [f"{item.get('role', 'traveler')}: {str(item.get('text') or '')[:280]}" for item in (history or [])[-6:]]
        state = (
            f"Trip: destination={snapshot.destination or 'unknown'}; origin={snapshot.origin or 'unknown'}; "
            f"days={snapshot.duration_days or 'unknown'}\n"
            f"Current plan: {json.dumps(_outline(plan_summary), ensure_ascii=False) if plan_summary else 'none'}\n"
            f"Recent history:\n" + ("\n".join(lines) or "(none)") + "\n\n"
            f"<untrusted_user_message>\n{message}\n</untrusted_user_message>"
        )
        return {
            "model": self.model,
            "state": state,
            "questions": {
                "intent": {
                    "type": "choice",
                    "instructions": "What does the traveller want from the assistant on this turn?",
                    "criteria": INTENT_CRITERIA,
                },
                "needs_detail": {
                    "type": "noul",
                    "instructions": (
                        "The traveller still has to supply a destination, origin or length "
                        "before an itinerary can be built"
                    ),
                },
            },
        }

    async def route(
        self,
        *,
        message: str,
        snapshot: TripSnapshot,
        plan_summary: dict[str, Any] | None,
        history: list[dict[str, str]] | None = None,
    ) -> RouteDecision | None:
        headers = {
            "Authorization": f"{self.auth_prefix} {self.api_key}".strip(),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.endpoint, headers=headers, json=self.payload(
                message=message, snapshot=snapshot, plan_summary=plan_summary, history=history,
            ))
            response.raise_for_status()
            data = response.json()
        return decision_from_jev(data, message=message, snapshot=snapshot, plan_summary=plan_summary)


def decision_from_jev(
    data: Any, *, message: str, snapshot: TripSnapshot, plan_summary: dict[str, Any] | None,
) -> RouteDecision | None:
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        return None
    intent_answer = answers.get("intent") or {}
    intent = str(intent_answer.get("choice") or "").lower()
    if intent == "clarify":
        intent = "unclear"
    if intent not in {"plan", "modify", "ask", "unclear"}:
        return None
    confidence = _confidence(intent_answer, intent)
    if confidence is None:
        return None
    needs_detail = _noul(answers.get("needs_detail"))
    return RouteDecision(
        intent=Intent(intent),
        confidence=confidence,
        need_clarification=intent == "unclear" or bool(needs_detail is not None and needs_detail >= 0.5),
        reason_code=f"jev_{intent}",
        affected_parts=affected_parts_for(message) if intent == "modify" else [],
        missing_slots=snapshot.missing_slots(),
        related_to_plan=plan_summary is not None or snapshot.has_plan,
        rationale="system one decision",
        source="jev",
    )


def _outline(plan_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not plan_summary:
        return {}
    days = []
    for day in (plan_summary.get("days") or [])[:14]:
        if not isinstance(day, dict):
            continue
        days.append({
            "day": day.get("day"),
            "base": day.get("base"),
            "items": [item.get("name") for item in (day.get("items") or []) if isinstance(item, dict)][:8],
        })
    return {"destination": plan_summary.get("destination"), "days": days}


def _confidence(intent_answer: Any, intent: str) -> float | None:
    if not isinstance(intent_answer, dict):
        return None
    probabilities = intent_answer.get("probabilities")
    if isinstance(probabilities, dict):
        try:
            return max(0.0, min(1.0, float(probabilities.get(intent))))
        except (TypeError, ValueError):
            pass
    try:
        return max(0.0, min(1.0, float(intent_answer.get("confidence"))))
    except (TypeError, ValueError):
        return None


def _noul(answer: Any) -> float | None:
    if not isinstance(answer, dict):
        return None
    try:
        return max(0.0, min(1.0, float(answer.get("noul"))))
    except (TypeError, ValueError):
        return None
