"""ModelCaller: the one place where a model is called.

Why a single choke point: the proposal requires that every model call is logged (Appendix B)
and that canary leakage is "a string check". Doing both here, around an unmodified request,
means no path can call a model without being traced or screened, and nothing between our code
and the model can alter the prompt.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence

from backend.agents.runtime.llm import LLMClient
from backend.observability.recorder import Observability
from backend.schemas.common import AgentName, PathName, utcnow
from backend.schemas.llm import ChatMessage, LLMRequest, LLMResponse
from backend.schemas.observability import LLMCallRecord, TraceContext
from backend.schemas.security import SecurityEventKind
from backend.security.audit import SecurityAudit
from backend.security.canary import CanaryGuard


class ModelCallError(Exception):
    """The model client failed (transport / provider error)."""


class OutputRejected(Exception):
    """The model answered, but the answer must not be used (e.g. it leaked the canary)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ModelCaller:
    def __init__(
        self,
        client: LLMClient,
        *,
        model: str,
        observability: Observability,
        audit: SecurityAudit,
        canary: CanaryGuard,
    ) -> None:
        self._client = client
        self._model = model
        self._obs = observability
        self._audit = audit
        self._canary = canary

    @property
    def model(self) -> str:
        return self._model

    @property
    def canary(self) -> CanaryGuard:
        return self._canary

    async def call(
        self,
        *,
        purpose: str,
        messages: Sequence[ChatMessage],
        trace: TraceContext,
        path: PathName,
        agent: AgentName | None = None,
        json_output: bool = True,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        request = LLMRequest(
            purpose=purpose,
            model=self._model,
            messages=list(messages),
            json_output=json_output,
            max_tokens=max_tokens,
        )
        started_at = utcnow()
        t0 = time.perf_counter()
        response: LLMResponse | None = None
        error: str | None = None
        try:
            response = await self._client.complete(request)
        except Exception as exc:  # recorded, then surfaced as a typed error
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - t0) * 1000
        self._obs.record_llm_call(
            LLMCallRecord(
                record_id=f"llm_{uuid.uuid4().hex[:12]}",
                trace_id=trace.trace_id,
                purpose=purpose,
                path=path,
                agent=agent,
                model=self._model,
                request=request,
                response_content=response.content if response else None,
                usage=response.usage if response else None,
                error=error,
                started_at=started_at,
                ended_at=utcnow(),
                latency_ms=latency_ms,
            )
        )
        if response is None:
            raise ModelCallError(error or "model call failed")
        if self._canary.leaked(response.content):
            self._audit.record(SecurityEventKind.CANARY_LEAK, path=path)
            self._obs.record_event(trace, "canary_leak_blocked", {"purpose": purpose})
            raise OutputRejected("canary_leak")
        return response
