"""ToolGateway: the single entry point for every tool call.

Behaviour (proposal §4):
  1. Parse: a `str` request is JSON proposed by a model. Invalid JSON, a missing/extra field or a
     free-text date yields `ToolOutcome(status=VALIDATION_ERROR, failure=VALIDATION, issues=[...])`
     WITHOUT contacting any provider. Validation failures are never retried unchanged.
  2. Allowlist: a tool not allowed for (path, agent) yields `status=BLOCKED, failure=NOT_ALLOWED`,
     is never executed, and is recorded via `SecurityAudit.record(TOOL_BLOCKED, path=...)`.
  3. Execute through `ToolRegistry` with a per-attempt timeout (`tool_call_timeout_s`).
     Timeout / rate limit / server fault -> retry with exponential backoff + full jitter, at most
     `tool_max_attempts` attempts, all inside min(`tool_retry_budget_s`, budget_s).
     `ProviderBadRequest` -> VALIDATION_ERROR (no retry). `ProviderUnavailable` -> UNAVAILABLE
     (failure=PERMANENT, no retry). Retries exhausted -> UNAVAILABLE with the last failure kind;
     budget ran out -> UNAVAILABLE with failure=BUDGET_EXHAUSTED.
  4. Success -> status OK, payload set, `fetched_at = utcnow()` (drives staleness).
  5. Every call (any status) -> `Observability.record_tool_call(ToolCallRecord(...))` carrying the
     raw request text, the trace id, path and agent.

Implementation notes:
  * The allowlist is checked as soon as the operation is known — even when the rest of a string
    request is malformed — so a disallowed tool is blocked rather than offered model repair.
  * Budget accounting adds the measured duration of each attempt to the requested backoff
    delays. That is equal to wall-clock time with the real `asyncio.sleep`, and it keeps the
    budget logic exact when tests inject a fake sleep that returns immediately.
  * When the per-attempt timeout had to be shortened to fit the remaining budget and that
    attempt timed out, the failure is BUDGET_EXHAUSTED (the budget, not the provider, ran out).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from backend.observability.recorder import Observability
from backend.schemas.common import AgentName, PathName, ToolName, utcnow
from backend.schemas.observability import ToolCallRecord, TraceContext
from backend.schemas.security import SecurityEventKind
from backend.schemas.tools import (
    OPERATION_TOOL,
    FailureKind,
    FieldIssue,
    ToolOperation,
    ToolOutcome,
    ToolOutcomeStatus,
    ToolPayload,
    ToolRequest,
)
from backend.security.audit import SecurityAudit
from backend.settings import Settings
from backend.tools.allowlist import ToolAllowlist
from backend.tools.errors import classify
from backend.tools.providers.base import ProviderBadRequest, ProviderRateLimited
from backend.tools.registry import ToolRegistry
from backend.tools.retry import RetryPolicy

Sleep = Callable[[float], Awaitable[None]]

logger = logging.getLogger(__name__)

_REQUEST_ADAPTER: TypeAdapter[ToolRequest] = TypeAdapter(ToolRequest)
_OPERATION_VALUES = frozenset(op.value for op in ToolOperation)


def new_call_id() -> str:
    return "tc_" + uuid4().hex[:12]


def parse_tool_request(raw: str) -> tuple[ToolRequest | None, list[FieldIssue]]:
    """Validate model-proposed JSON against the strict request models.

    Returns (request, []) on success or (None, issues). Issue locations are relative to the
    request object (the discriminator tag pydantic prefixes is dropped), '' = whole payload."""
    try:
        return _REQUEST_ADAPTER.validate_json(raw), []
    except ValidationError as exc:
        issues: list[FieldIssue] = []
        for err in exc.errors(include_url=False):
            loc = [str(part) for part in err["loc"]]
            if loc and loc[0] in _OPERATION_VALUES:
                loc = loc[1:]
            issues.append(FieldIssue(loc=".".join(loc), message=err["msg"], kind=err["type"]))
        return None, issues or [FieldIssue(loc="", message=str(exc), kind="validation_error")]


def sniff_operation(raw: str) -> ToolOperation | None:
    """Best-effort `operation` of a (possibly invalid) JSON request; None if not determinable."""
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if isinstance(data, dict):
        value = data.get("operation")
        if isinstance(value, str) and value in _OPERATION_VALUES:
            return ToolOperation(value)
    return None


class ToolGateway:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        allowlist: ToolAllowlist,
        settings: Settings,
        observability: Observability,
        audit: SecurityAudit,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._registry = registry
        self._allowlist = allowlist
        self._settings = settings
        self._policy = RetryPolicy.from_settings(settings)
        self._observability = observability
        self._audit = audit
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()

    @property
    def allowlist(self) -> ToolAllowlist:
        return self._allowlist

    @property
    def policy(self) -> RetryPolicy:
        return self._policy

    def scoped(
        self, *, path: PathName, agent: AgentName | None, trace: TraceContext
    ) -> ScopedToolGateway:
        """Return a gateway bound to one path/agent/trace. All calls go through the allowlist
        for that (path, agent) pair."""
        return ScopedToolGateway(gateway=self, path=path, agent=agent, trace=trace)

    # ---- one logical call ----------------------------------------------------------------------

    async def _call(
        self, scope: ScopedToolGateway, request: str | ToolRequest, budget_s: float | None
    ) -> ToolOutcome:
        call_id = new_call_id()
        started_at = utcnow()
        t0 = time.monotonic()

        parsed: ToolRequest | None
        issues: list[FieldIssue]
        if isinstance(request, str):
            raw = request
            parsed, issues = parse_tool_request(request)
            operation = parsed.operation if parsed is not None else sniff_operation(request)
        else:
            raw = request.model_dump_json()
            parsed, issues = request, []
            operation = request.operation
        tool: ToolName | None = OPERATION_TOOL[operation] if operation is not None else None

        def finish(
            status: ToolOutcomeStatus,
            *,
            failure: FailureKind | None = None,
            outcome_issues: list[FieldIssue] | None = None,
            attempts: int = 0,
            provider: str | None = None,
            payload: ToolPayload | None = None,
            fetched_at: datetime | None = None,
        ) -> ToolOutcome:
            ended_at = utcnow()
            latency_ms = (time.monotonic() - t0) * 1000.0
            outcome = ToolOutcome(
                call_id=call_id,
                operation=operation,
                tool=tool,
                status=status,
                request=parsed,
                payload=payload,
                issues=outcome_issues or [],
                failure=failure,
                attempts=attempts,
                provider=provider,
                fetched_at=fetched_at,
                latency_ms=latency_ms,
            )
            self._record(scope, outcome, raw, started_at, ended_at)
            return outcome

        # 2. allowlist (as soon as the tool is known, even if the rest is malformed)
        if tool is not None and not self._allowlist.is_allowed(scope.path, scope.agent, tool):
            self._audit_blocked(scope.path)
            return finish(ToolOutcomeStatus.BLOCKED, failure=FailureKind.NOT_ALLOWED)

        # 1. validation: structured issues back to the model, provider never contacted
        if parsed is None:
            return finish(
                ToolOutcomeStatus.VALIDATION_ERROR,
                failure=FailureKind.VALIDATION,
                outcome_issues=issues,
            )

        # 3./4. execute with retries inside the budget
        policy = self._policy.with_budget(budget_s)
        spent = 0.0
        attempts = 0
        last_failure: FailureKind | None = None
        while True:
            remaining = policy.budget_s - spent
            if remaining <= 0:
                return finish(
                    ToolOutcomeStatus.UNAVAILABLE,
                    failure=FailureKind.BUDGET_EXHAUSTED,
                    attempts=attempts,
                )
            timeout = min(policy.attempt_timeout_s, remaining)
            budget_capped = timeout < policy.attempt_timeout_s
            attempts += 1
            attempt_t0 = time.monotonic()
            try:
                payload, provider = await asyncio.wait_for(
                    self._registry.execute(parsed), timeout=timeout
                )
            except Exception as exc:  # classified below; CancelledError is not caught
                spent += time.monotonic() - attempt_t0
                kind = classify(exc)
                if kind is FailureKind.VALIDATION:
                    provider_issues = exc.issues if isinstance(exc, ProviderBadRequest) else []
                    return finish(
                        ToolOutcomeStatus.VALIDATION_ERROR,
                        failure=FailureKind.VALIDATION,
                        outcome_issues=provider_issues
                        or [FieldIssue(loc="", message=str(exc), kind="provider_bad_request")],
                        attempts=attempts,
                    )
                if kind is FailureKind.PERMANENT:
                    return finish(
                        ToolOutcomeStatus.UNAVAILABLE, failure=FailureKind.PERMANENT, attempts=attempts
                    )
                if budget_capped and isinstance(exc, TimeoutError):
                    return finish(
                        ToolOutcomeStatus.UNAVAILABLE,
                        failure=FailureKind.BUDGET_EXHAUSTED,
                        attempts=attempts,
                    )
                last_failure = kind
                if attempts >= policy.max_attempts:
                    return finish(
                        ToolOutcomeStatus.UNAVAILABLE, failure=last_failure, attempts=attempts
                    )
                retry_after = exc.retry_after_s if isinstance(exc, ProviderRateLimited) else None
                delay = policy.plan_delay(attempts, self._rng, policy.budget_s - spent, retry_after)
                if delay is None:
                    return finish(
                        ToolOutcomeStatus.UNAVAILABLE,
                        failure=FailureKind.BUDGET_EXHAUSTED,
                        attempts=attempts,
                    )
                logger.debug(
                    "tool %s attempt %d failed (%s); retrying in %.3fs",
                    parsed.operation.value,
                    attempts,
                    kind.value,
                    delay,
                )
                await self._sleep(delay)
                spent += delay
                continue
            return finish(
                ToolOutcomeStatus.OK,
                attempts=attempts,
                provider=provider,
                payload=payload,
                fetched_at=utcnow(),
            )

    # ---- side channels (never break the call) --------------------------------------------------

    def _record(
        self,
        scope: ScopedToolGateway,
        outcome: ToolOutcome,
        raw: str,
        started_at: datetime,
        ended_at: datetime,
    ) -> None:
        record = ToolCallRecord(
            record_id=uuid4().hex,
            trace_id=scope.trace.trace_id,
            call_id=outcome.call_id,
            path=scope.path,
            agent=scope.agent,
            operation=outcome.operation,
            tool=outcome.tool,
            raw_request=raw,
            status=outcome.status,
            failure=outcome.failure,
            issues=outcome.issues,
            attempts=outcome.attempts,
            provider=outcome.provider,
            payload=outcome.payload,
            fetched_at=outcome.fetched_at,
            started_at=started_at,
            ended_at=ended_at,
            latency_ms=outcome.latency_ms,
        )
        try:
            self._observability.record_tool_call(record)
        except Exception:  # observability must never break a turn
            logger.exception("failed to record tool call %s", outcome.call_id)

    def _audit_blocked(self, path: PathName) -> None:
        try:
            self._audit.record(SecurityEventKind.TOOL_BLOCKED, path=path)
        except Exception:
            logger.exception("failed to audit blocked tool call on path %s", path)


class ScopedToolGateway:
    path: PathName
    agent: AgentName | None
    trace: TraceContext

    def __init__(
        self,
        *,
        gateway: ToolGateway,
        path: PathName,
        agent: AgentName | None,
        trace: TraceContext,
    ) -> None:
        self._gateway = gateway
        self.path = path
        self.agent = agent
        self.trace = trace
        # One slot reserved per call when it starts, so concurrent calls (asyncio.gather) are
        # still listed in call order rather than completion order.
        self._slots: list[ToolOutcome | None] = []

    @property
    def allowed_tools(self) -> frozenset[ToolName]:
        """Tools this scope may call (useful for listing tools in the agent's prompt)."""
        return self._gateway.allowlist.allowed_tools(self.path, self.agent)

    async def call(self, request: str | ToolRequest, *, budget_s: float | None = None) -> ToolOutcome:
        """Execute one logical tool call (see module docstring). Never raises for tool failures;
        failures are returned as a classified `ToolOutcome`."""
        index = len(self._slots)
        self._slots.append(None)
        outcome = await self._gateway._call(self, request, budget_s)
        self._slots[index] = outcome
        return outcome

    @property
    def outcomes(self) -> list[ToolOutcome]:
        """Every outcome produced through this scope, in call order (calls still in flight are
        not listed)."""
        return [outcome for outcome in self._slots if outcome is not None]
