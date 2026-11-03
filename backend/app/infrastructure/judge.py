"""Optional LLM-as-a-judge for evaluation runs.

Advisory only: the deterministic checks still decide pass/fail. The judge adds a
1-5 quality score and human-review flags. It is disabled unless JUDGE_API_KEY is
set, and any judge failure (timeout, bad JSON, 401) returns None so an eval run
never fails because of it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.domain.models import EvalCase, TripPlan

SYSTEM_PROMPT = """You are an independent reviewer of travel itineraries.
Score the plan from 1 to 5:
5 = grounded, realistic pacing, every hard constraint honoured, gaps disclosed.
3 = usable, with clear but disclosed gaps.
1 = wrong destination/dates, invented facts, impossible travel, or hidden gaps.

Penalise: places that contradict the request, travel times that cannot work,
budget/date/party-size violations, and claims without a source.
Do not reward confident wording. Ignore style and formatting.

Return ONLY JSON:
{"score": 1-5, "flags": ["short reasons for a human to check"], "reason": "one or two sentences"}"""

_CACHE: dict[str, "JudgeVerdict"] = {}


class JudgeVerdict(BaseModel):
    score: float = Field(ge=1, le=5)
    flags: list[str] = Field(default_factory=list)
    reason: str = ""


def _parse_verdict(text: str) -> JudgeVerdict | None:
    """Accept plain JSON, fenced JSON, or a JSON object embedded in prose."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
        score = max(1.0, min(5.0, float(data.get("score"))))
        flags = [str(flag)[:80] for flag in (data.get("flags") or [])][:6]
        reason = str(data.get("reason") or "")[:400]
        return JudgeVerdict(score=score, flags=flags, reason=reason)
    except (ValueError, TypeError, ValidationError):
        return None


def _case_payload(case: EvalCase, plan: TripPlan, checks: list[str], scores: dict[str, float]) -> str:
    days = [
        {
            "date": day.date.isoformat(),
            "title": day.title,
            "items": [f"{item.start}-{item.end} {item.name} ({item.data_status.value})" for item in day.items[:6]],
        }
        for day in plan.days[:8]
    ]
    payload: dict[str, Any] = {
        "request": case.input[:1200],
        "expected": {
            "intent": case.expected_intent.value if case.expected_intent else "",
            "days": case.days,
            "budget": case.budget,
            "party_size": case.party_size,
            "constraints": case.constraints[:6],
        },
        "plan": {
            "destination": plan.destination.name,
            "dates": [plan.dates.start.isoformat(), plan.dates.end.isoformat()],
            "party_size": plan.party_size,
            "summary": plan.summary[:600],
            "days": days,
            "cost": {
                "total": plan.cost.total,
                "currency": plan.cost.currency,
                "status": plan.cost.status,
            },
            "gaps": [f"{gap.section}:{gap.kind.value}" for gap in plan.data_quality.gaps][:8],
        },
        "deterministic": {"checks": checks[:6], "scores": scores},
    }
    return json.dumps(payload, ensure_ascii=False)[:8000]


class Judge:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = settings.judge_base_url.rstrip("/")
        self.model = settings.judge_model
        self.available = settings.judge_available
        # Prompt + completion tokens the judge API reported (cached verdicts cost none).
        self.tokens = 0

    async def score(
        self,
        *,
        case: EvalCase,
        plan: TripPlan,
        checks: list[str],
        scores: dict[str, float],
    ) -> JudgeVerdict | None:
        if not self.available:
            return None
        user = _case_payload(case, plan, checks, scores)
        cache_key = hashlib.sha256(f"{self.model}:{user}".encode("utf-8")).hexdigest()
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=self.settings.judge_timeout_seconds) as client:
                response = await client.post(
                    f"{self.base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.judge_api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0,
                        "max_tokens": 350,
                        "response_format": {"type": "json_object"},
                    },
                )
            response.raise_for_status()
            usage = response.json().get("usage") or {}
            self.tokens += int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
            content = ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        except Exception:
            return None
        verdict = _parse_verdict(content)
        if verdict is not None:
            if len(_CACHE) > 500:
                _CACHE.clear()
            _CACHE[cache_key] = verdict
        return verdict
