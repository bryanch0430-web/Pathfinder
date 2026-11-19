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

from app.application.prompt_context import earlier_turns, trip_state
from app.application.prompts import PromptLibrary
from app.application.router import affected_parts_for
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import Intent, Message, RouteDecision, TripContext
from app.security import canary_leaked, fence_user_message, strip_fence_tags, strip_fence_tags_deep

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
        history_lines = [f"{item.role}: {strip_fence_tags(item.content)[:280]}" for item in earlier_turns(history, message)[-6:]]
        state = (
            f"Trip context: {trip_state(context)}\n"
            f"Current plan: {json.dumps(strip_fence_tags_deep(plan_summary)) if plan_summary else 'none'}\n"
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
            if canary_leaked(response.text, self.settings):
                raise CanaryLeak("Canary token detected in Jev decision output")
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


SENTIMENT_LEVELS = [
    "Hostile. Attacks the product or a person. Insults, or tells others to leave.",
    "Negative. Names a problem or disappointment, without attacking anyone.",
    "Neutral. A question, a fact, or no evaluation.",
    "Positive. Says something worked, in ordinary language.",
    "Enthusiastic. Tells other people to use it.",
]


def _jev_endpoint(settings: Settings) -> str:
    base = settings.decision_api_url.rstrip("/")
    path = (settings.decision_path or "").strip()
    if path and not base.endswith(path):
        return base + "/" + path.lstrip("/")
    return base


def _scored(value: list[Any] | None, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in value or []:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("utterance") or "").strip()
            score = item.get("score", 60)
        else:
            text = str(item).strip()
            score = 60
        if not text:
            continue
        try:
            number = max(0, min(100, int(score)))
        except (TypeError, ValueError):
            number = 60
        rows.append({"text": text, "score": number})
        if len(rows) >= limit:
            break
    return rows


def sentiment_payload(text: str, words: list[Any] | None = None, examples: list[Any] | None = None) -> dict[str, Any]:
    """The Jev Score request. Each keyword and example is sent with the score the operator gave it."""
    return {
        "state": {
            "mention_text": text.strip()[:2000],
            "negative_words": _scored(words, 80),
            "extra_examples": _scored(examples, 40),
        },
        "questions": {
            "sentiment": {
                "type": "score",
                "instructions": (
                    "How favourably this message speaks about the trip planner. "
                    "Each negative_word and extra_example has a score from 0 to 100. "
                    "A higher score is less favourable. Use that score when the message contains the text."
                ),
                "criteria": SENTIMENT_LEVELS,
            }
        },
    }


async def jev_sentiment(
    settings: Settings,
    text: str,
    *,
    words: list[str] | None = None,
    examples: list[str] | None = None,
) -> int | None:
    """Frustration 0 to 100 from a Jev Score, or None when Jev cannot be asked.

    Level 0 is hostile and becomes 100. Level 4 is enthusiastic and becomes 0.
    The saved handoff key is used when there is one. Otherwise the Jev gate key is used.
    """
    from app.application.sentiment import frustration_from_jev

    key = (getattr(settings, "handoff_api_key", "") or settings.decision_api_key or "").strip()
    if not key or not settings.decision_api_url.strip() or not (text or "").strip():
        return None
    payload = {"model": settings.decision_model, **sentiment_payload(text, words, examples)}
    headers = {
        "Authorization": f"{settings.decision_auth_prefix} {key}".strip(),
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.decision_timeout_seconds) as client:
            response = await client.post(_jev_endpoint(settings), headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    answers = data.get("answers") if isinstance(data, dict) else None
    sentiment = answers.get("sentiment") if isinstance(answers, dict) else None
    if not isinstance(sentiment, dict):
        return None
    try:
        return frustration_from_jev(float(sentiment.get("score")))
    except (TypeError, ValueError):
        return None


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
