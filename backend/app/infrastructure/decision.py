"""TypeSafe Jev adapter (System 1 decision model).

The `systemone` endpoint is **not** an OpenAI chat endpoint. It takes a `state`
(string, object or array) plus a map of typed questions -- `choice`, `score` and
`noul` -- and returns calibrated answers. Sending `messages` there is rejected
with `400 api_usage_error`, so the adapter builds the native payload and maps the
answers back onto `RouteDecision`.

Every failure path returns `None`: the caller then falls back to the writer model
and finally to the deterministic rules.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

import httpx

from app.application.prompts import PromptLibrary
from app.application.router import affected_parts_for
from app.config import Settings
from app.domain.models import Intent, Message, RouteDecision, TripContext
from app.security import fence_user_message

INTENT_CRITERIA: dict[str, str] = {
    "plan": "Create a brand-new itinerary from scratch",
    "modify": "Change, refine or repair an itinerary that already exists",
    "ask": "Ask a question about an existing itinerary without changing it",
    "unclear": "Essential trip details are missing, or this is not a travel request",
}

# The choice answer ships calibrated probabilities for every option, so the
# router gate uses P(chosen intent) directly instead of a separate score question.


class JevDecisionClient:
    name = "jev"

    def __init__(self, settings: Settings, prompts: PromptLibrary):
        self.settings = settings
        self.prompts = prompts
        self.endpoint = self._endpoint()

    def _endpoint(self) -> str:
        base = self.settings.decision_api_url.rstrip("/")
        path = (self.settings.decision_path or "").strip()
        if path and not base.endswith(path):
            return base + "/" + path.lstrip("/")
        return base

    def _payload(
        self,
        *,
        message: str,
        context: TripContext,
        plan_summary: dict[str, Any] | None,
        history: Sequence[Message],
    ) -> dict[str, Any]:
        history_lines = [f"{item.role}: {item.content[:280]}" for item in history[-6:]]
        state = (
            f"Trip context: {context.model_dump_json()}\n"
            f"Current plan: {json.dumps(plan_summary) if plan_summary else 'none'}\n"
            f"Recent history:\n" + ("\n".join(history_lines) or "(none)") + "\n\n"
            + fence_user_message(message)
        )
        return {
            "model": self.settings.decision_model,
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
                        "The traveller still has to supply a destination, origin, start "
                        "date or budget before an itinerary can be built"
                    ),
                },
            },
        }

    async def route(
        self,
        *,
        message: str,
        context: TripContext,
        plan_summary: dict[str, Any] | None,
        history: Sequence[Message],
    ) -> RouteDecision | None:
        headers = {
            "Authorization": f"{self.settings.decision_auth_prefix} {self.settings.decision_api_key}".strip(),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.settings.decision_timeout_seconds) as client:
            response = await client.post(
                self.endpoint,
                headers=headers,
                json=self._payload(
                    message=message,
                    context=context,
                    plan_summary=plan_summary,
                    history=history,
                ),
            )
            response.raise_for_status()
            data = response.json()

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
        need_clarification = intent == "unclear" or bool(needs_detail is not None and needs_detail >= 0.5)

        return RouteDecision(
            intent=Intent(intent),
            confidence=confidence,
            need_clarification=need_clarification,
            reason_code=f"jev_{intent}",
            affected_parts=affected_parts_for(message) if intent == "modify" else [],
            missing_slots=context.missing_slots(),
            related_to_plan=plan_summary is not None,
            rationale="system one decision",
        )


def _confidence(intent_answer: Any, intent: str) -> float | None:
    """Calibrated probability of the chosen intent, on the gate's 0..1 scale.

    Falls back to the answer's own `confidence` field when probabilities are absent,
    and returns None when neither is present so the caller can use another router.
    """
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
