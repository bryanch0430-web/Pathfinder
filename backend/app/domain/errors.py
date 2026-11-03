"""Typed failures shared by every layer."""

from __future__ import annotations

from app.domain.models import FailureKind


class PathfinderError(Exception):
    code = "pathfinder_error"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        if code:
            self.code = code
        if retryable is not None:
            self.retryable = retryable

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


class ValidationError(PathfinderError):
    code = "validation_error"


class PlanInvalidError(PathfinderError):
    code = "plan_invalid"
    retryable = True


class ProviderError(PathfinderError):
    """A data source or the writer failed.

    ``str(error)`` is written by us and is safe to show a traveller: it never holds the source's own
    wording, which can be an error page, an address or a key. That wording goes in ``detail``, which
    only the trace reads (see ``operator_text``).
    """

    code = "provider_error"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        kind: FailureKind = FailureKind.SERVER_ERROR,
        source: str = "",
        retryable: bool | None = None,
        detail: str = "",
    ):
        super().__init__(message, retryable=retryable)
        self.kind = kind
        self.source = source
        self.detail = detail

    def as_dict(self) -> dict[str, object]:
        data = super().as_dict()
        data.update({"kind": self.kind.value, "source": self.source})
        return data


def operator_text(exc: BaseException) -> str:
    """An exception's text for the operator's trace: the message, and the source's own words when it has them."""
    detail = getattr(exc, "detail", "")
    return f"{exc} | {detail}" if detail else str(exc)


class ToolBudgetExceeded(ProviderError):
    """A turn tried to make more tool calls than `max_tool_calls_per_turn` allows."""

    code = "tool_budget_exceeded"

    def __init__(self, message: str):
        super().__init__(message, kind=FailureKind.BLOCKED, source="toolbox", retryable=False)


class ProviderNoData(ProviderError):
    code = "no_data"
    retryable = False

    def __init__(self, message: str, *, source: str = ""):
        super().__init__(message, kind=FailureKind.NO_DATA, source=source, retryable=False)


class SecurityBlocked(PathfinderError):
    code = "security_blocked"
    retryable = False

    def __init__(self, message: str, *, category: str = "prompt_injection"):
        super().__init__(message)
        self.category = category


class CanaryLeak(PathfinderError):
    code = "canary_leak"
    retryable = False
