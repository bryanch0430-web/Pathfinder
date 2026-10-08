"""Retry policy: exponential backoff with FULL jitter inside a time budget.

Proposal §4: "timeout / rate limit / brief server fault -> retry with backoff + jitter inside a
time budget". Full jitter (delay drawn uniformly from [0, cap]) spreads retries from parallel
agents so they do not hammer a recovering provider in lockstep; the cap doubles per retry up to
`max_s`. A sleep is never allowed to run past the remaining budget: if the next backoff would
exceed it, the call stops with BUDGET_EXHAUSTED instead.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from backend.settings import Settings

# 2**64 * base already dwarfs any sane max_s; clamping the exponent avoids float overflow.
_MAX_EXPONENT = 64


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_s: float
    max_s: float
    attempt_timeout_s: float
    budget_s: float

    @classmethod
    def from_settings(cls, settings: Settings) -> RetryPolicy:
        return cls(
            max_attempts=settings.tool_max_attempts,
            base_s=settings.tool_backoff_base_s,
            max_s=settings.tool_backoff_max_s,
            attempt_timeout_s=settings.tool_call_timeout_s,
            budget_s=settings.tool_retry_budget_s,
        )

    def with_budget(self, budget_s: float | None) -> RetryPolicy:
        """Policy whose budget is min(own budget, budget_s) — the agent's remaining time budget
        further caps the per-call retry budget."""
        if budget_s is None:
            return self
        return replace(self, budget_s=max(0.0, min(self.budget_s, budget_s)))

    def backoff_cap(self, retry_number: int) -> float:
        """Upper bound of the jittered delay before retry `retry_number` (1 = first retry):
        min(max_s, base_s * 2**(retry_number - 1))."""
        exponent = min(max(retry_number - 1, 0), _MAX_EXPONENT)
        return min(self.max_s, self.base_s * (2**exponent))

    def backoff_delay(
        self, retry_number: int, rng: random.Random, retry_after_s: float | None = None
    ) -> float:
        """Full-jitter delay rng.uniform(0, cap); at least `retry_after_s` when the provider
        asked for it (rate limit). Not capped by max_s: the provider's hint wins, only the
        budget can veto it (see `plan_delay`)."""
        delay = rng.uniform(0.0, self.backoff_cap(retry_number))
        if retry_after_s is not None and retry_after_s > delay:
            delay = retry_after_s
        return delay

    def plan_delay(
        self,
        retry_number: int,
        rng: random.Random,
        remaining_s: float,
        retry_after_s: float | None = None,
    ) -> float | None:
        """The delay to sleep before retry `retry_number`, or None when sleeping it would use up
        the remaining budget (no time would be left for the attempt itself)."""
        delay = self.backoff_delay(retry_number, rng, retry_after_s)
        if delay >= remaining_s:
            return None
        return delay
