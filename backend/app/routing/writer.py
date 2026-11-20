"""Writer-model fallback. Used only when the rules are not sure and Jev is absent or fails."""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from app.routing.models import Intent, RouteDecision, TripSnapshot

Generate = Callable[..., Awaitable[str]]

SYSTEM = """You route one traveler message for a trip planner. Return JSON only, with no markdown:
{"intent":"plan|modify|ask|unclear","confidence":0.0,"need_clarification":false,"reason_code":"short_label","affected_parts":[],"rationale":"one sentence"}
plan creates a new itinerary. modify changes an existing one. ask reads it and does not change it.
unclear means you are not sure enough to act. confidence is your probability that the intent is right, from 0 to 1.
affected_parts is only for modify, chosen from: hotel, ticket, attraction, weather, budget, dates, day_1, day_2, and so on.
"""


class WriterRouter:
    def __init__(self, generate: Generate | None = None):
        self._generate = generate

    async def route(
        self,
        *,
        message: str,
        snapshot: TripSnapshot,
        plan_summary: dict[str, Any] | None,
        history: list[dict[str, str]] | None = None,
    ) -> RouteDecision:
        generate = self._generate or _default_generate
        outline = ""
        if plan_summary:
            outline = json.dumps(
                {"destination": plan_summary.get("destination"), "days": [
                    {"day": day.get("day"), "items": [item.get("name") for item in (day.get("items") or []) if isinstance(item, dict)][:6]}
                    for day in (plan_summary.get("days") or [])[:10] if isinstance(day, dict)
                ]},
                ensure_ascii=False,
            )
        prompt = (
            f"Trip destination: {snapshot.destination or 'unknown'}\n"
            f"Origin: {snapshot.origin or 'unknown'}\n"
            f"Days: {snapshot.duration_days or 'unknown'}\n"
            f"Current plan: {outline or 'none'}\n"
            f"Earlier: {json.dumps((history or [])[-4:], ensure_ascii=False)}\n\n"
            f"Message:\n{message}"
        )
        raw = await generate(prompt, system_instruction=SYSTEM, temperature=0)
        return _parse(raw, snapshot=snapshot, has_plan=plan_summary is not None or snapshot.has_plan)


def _parse(raw: str, *, snapshot: TripSnapshot, has_plan: bool) -> RouteDecision:
    match = re.search(r"\{.*\}", raw or "", re.S)
    data = json.loads(match.group(0) if match else raw)
    if not isinstance(data, dict):
        raise ValueError("router json was not an object")
    intent = str(data.get("intent") or "unclear").lower()
    if intent == "clarify":
        intent = "unclear"
    if intent not in {"plan", "modify", "ask", "unclear"}:
        intent = "unclear"
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    parts = [str(part) for part in data.get("affected_parts") or [] if isinstance(part, str)]
    missing = snapshot.missing_slots()
    return RouteDecision(
        intent=Intent(intent),
        confidence=max(0.0, min(1.0, confidence)),
        need_clarification=bool(data.get("need_clarification")) or intent == "unclear",
        reason_code=str(data.get("reason_code") or "model_route")[:80],
        affected_parts=parts if intent == "modify" else [],
        missing_slots=missing,
        related_to_plan=has_plan,
        rationale=str(data.get("rationale") or "")[:240],
        source="writer",
    )


async def _default_generate(prompt: str, system_instruction: str | None = None, temperature: float = 0) -> str:
    from app.services.llm.service import llm_service
    return await llm_service.generate(
        prompt=prompt, system_instruction=system_instruction, temperature=temperature, max_output_tokens=300,
    )
