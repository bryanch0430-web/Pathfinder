"""Failure classification for tool calls.

Proposal §4: failures are classified BEFORE any retry decision. Validation problems go back to
the model for bounded repair (never resent unchanged); timeouts, rate limits and brief server
faults are retried with backoff + jitter; a source that cannot be used is marked unavailable.
"""

from __future__ import annotations

import asyncio

from backend.schemas.tools import FailureKind
from backend.tools.providers.base import (
    ProviderBadRequest,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
    ProviderUnavailable,
)


def classify(exc: BaseException) -> FailureKind:
    """Map an exception raised while executing a tool call to a `FailureKind`.

    Anything unrecognised (including a malformed provider response that fails payload
    validation) counts as a SERVER_FAULT: transient from the caller's point of view, so it is
    retried inside the budget rather than surfaced to the model as a request error.
    """
    if isinstance(exc, ProviderTimeout | asyncio.TimeoutError | TimeoutError):
        return FailureKind.TIMEOUT
    if isinstance(exc, ProviderRateLimited):
        return FailureKind.RATE_LIMIT
    if isinstance(exc, ProviderServerError):
        return FailureKind.SERVER_FAULT
    if isinstance(exc, ProviderBadRequest):
        return FailureKind.VALIDATION
    if isinstance(exc, ProviderUnavailable):
        return FailureKind.PERMANENT
    return FailureKind.SERVER_FAULT
