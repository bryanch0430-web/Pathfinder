"""System 1 router: confident rules, then Jev, then the writer, then the rules. The gate applies to every layer."""

from __future__ import annotations

import logging
from typing import Any

from app.routing.gate import apply_gate
from app.routing.jev import JevDecisionClient
from app.routing.models import Intent, RouteDecision, TripSnapshot
from app.routing.rules import RULES_FIRST, RuleRouter, affected_parts_for, message_flagged, rules_decide_alone, wants_to_clear_plan
from app.routing.writer import WriterRouter

logger = logging.getLogger(__name__)


class System1Router:
    def __init__(
        self,
        *,
        threshold: float = 0.8,
        rules_first: bool = True,
        jev: JevDecisionClient | None = None,
        writer: WriterRouter | None = None,
        use_writer: bool = True,
    ):
        self.threshold = threshold
        self.rules_first = rules_first
        self.rules = RuleRouter()
        self.jev = jev
        self.writer = writer if writer is not None else WriterRouter()
        self.use_writer = use_writer

    async def route(
        self,
        *,
        message: str,
        snapshot: TripSnapshot,
        plan_summary: dict[str, Any] | None,
        history: list[dict[str, str]] | None = None,
        last_reply: str | None = None,
    ) -> RouteDecision:
        ruled = self.rules.route(
            message=message, snapshot=snapshot, plan_summary=plan_summary, last_reply=last_reply,
        )
        if self.rules_first and ruled.confidence >= self.threshold and not message_flagged(message) and rules_decide_alone(
            message, ruled, snapshot=snapshot, plan_summary=plan_summary,
        ):
            decided = ruled.replace(rationale=RULES_FIRST)
            return self._finish(decided, snapshot, plan_summary, message, source="rules")
        if self.jev is not None and not message_flagged(message):
            try:
                decision = await self.jev.route(
                    message=message, snapshot=snapshot, plan_summary=plan_summary, history=history,
                )
            except Exception as exc:
                logger.info("Jev router unavailable, falling through: %s", exc)
                decision = None
            if decision is not None:
                return self._finish(decision, snapshot, plan_summary, message, source="jev")
        if self.use_writer:
            try:
                decision = await self.writer.route(
                    message=message, snapshot=snapshot, plan_summary=plan_summary, history=history,
                )
                return self._finish(decision, snapshot, plan_summary, message, source="writer")
            except Exception as exc:
                logger.info("Writer router unavailable, using rules: %s", exc)
        return self._finish(ruled, snapshot, plan_summary, message, source="rules")

    def _finish(
        self, decision: RouteDecision, snapshot: TripSnapshot, plan_summary: dict[str, Any] | None,
        message: str, *, source: str,
    ) -> RouteDecision:
        gated = apply_gate(decision, snapshot, plan_summary, threshold=self.threshold, source=source)
        if wants_to_clear_plan(message):
            return gated.replace(
                intent=Intent.MODIFY, need_clarification=False, reason_code="clear_plan",
                affected_parts=["plan"], confidence=max(gated.confidence, 0.9), source=source,
            )
        if gated.intent == Intent.MODIFY and not gated.affected_parts and not gated.need_clarification:
            gated = gated.replace(affected_parts=affected_parts_for(message))
        if gated.intent == Intent.MODIFY and not gated.affected_parts:
            gated = gated.replace(need_clarification=True, reason_code="no_affected_part")
        return gated


def build_router() -> System1Router:
    from app.core.config import settings
    from app.services.admin_settings import jev_enabled, router_controls

    threshold, rules_first = router_controls()
    jev = None
    if jev_enabled() and settings.DECISION_API_URL.strip() and settings.DECISION_API_KEY.strip():
        jev = JevDecisionClient(
            api_url=settings.DECISION_API_URL,
            api_key=settings.DECISION_API_KEY,
            model=settings.DECISION_MODEL,
            path=settings.DECISION_PATH,
            timeout=settings.DECISION_TIMEOUT_SECONDS,
            auth_prefix=settings.DECISION_AUTH_PREFIX,
        )
    return System1Router(threshold=threshold, rules_first=rules_first, jev=jev)
