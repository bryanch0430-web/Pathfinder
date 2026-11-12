"""Per-path tool allowlist and call logging.

Every provider call in the application goes through this object. That is the one
choke point where a path is prevented from using a tool it does not own.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Awaitable, Callable, TypeVar

from app.domain.errors import ProviderError, SecurityBlocked, ToolBudgetExceeded
from app.domain.models import FailureKind
from app.domain.ports import Tracer
from app.infrastructure.observability import developer_view, emit_live
from app.security.untrusted import neutralise

T = TypeVar("T")

PATH_ALLOWLIST: dict[str, frozenset[str]] = {
    "plan": frozenset(
        {
            "geocode",
            "weather.forecast",
            "places.search",
            "places.notes",
            "hotels.search",
            # The rooms of the candidate stays, for the stay research step.
            "hotels.rooms",
            "flights.search",
            "routes.leg",
            "routes.matrix",
            # Transfer times between a day's stops (the transport model).
            "routes.segments",
            "timezone.resolve",
            "elevation.gain",
            "fx.rates",
            # Reference pages for a section the official sources left empty.
            "web.search",
            # Hong Kong only: HKO warnings and MTR line status (TC-03).
            "weather.warnings",
            "routes.status",
        }
    ),
    "modify": frozenset(
        {
            "geocode",
            "weather.forecast",
            "places.search",
            "places.notes",
            "hotels.search",
            "flights.search",
            "routes.leg",
            "routes.matrix",
            # Transfer times between a day's stops (the transport model).
            "routes.segments",
            "timezone.resolve",
            "elevation.gain",
            "fx.rates",
            "web.search",
            "weather.warnings",
            "routes.status",
        }
    ),
    "ask": frozenset({"geocode", "weather.forecast", "routes.leg", "places.search", "web.search"}),
    "clarify": frozenset(),
    # Reading a link the traveller shared: that one fetch, never a search or a booking source.
    "intake": frozenset({"web.fetch"}),
    # Re-fetching an outdated section of a saved plan (Proposal DB-13): each
    # section's own source only, never a web search or a place lookup.
    "refresh": frozenset({"weather.forecast", "flights.search", "hotels.search"}),
}


class Toolbox:
    def __init__(
        self,
        *,
        path: str,
        tracer: Tracer,
        store: Any | None,
        request_id: str,
        extra_allowed: frozenset[str] | None = None,
        max_calls: int | None = None,
    ):
        self.path = path
        base = PATH_ALLOWLIST.get(path, frozenset())
        self.allowed = base | (extra_allowed or frozenset())
        self.tracer = tracer
        self.store = store
        self.request_id = request_id
        self.calls = 0
        #: The most tool calls this toolbox lets through (None: no cap).
        self.max_calls = max_calls
        #: The tools the cap refused, in order, so a caller that carries on without one can say so.
        self.refused: list[str] = []

    async def call(self, tool: str, fn: Callable[..., Awaitable[T]], /, **kwargs: Any) -> T:
        if tool not in self.allowed:
            if self.store is not None:
                self.store.log_tool_call(
                    request_id=self.request_id,
                    tool=tool,
                    args=_safe_args(kwargs),
                    status="blocked",
                    latency_ms=0,
                    error_class="blocked",
                    source="allowlist",
                )
            raise SecurityBlocked(f"tool {tool} is not allowed on the {self.path} path", category="tool_allowlist")
        try:
            inspect.signature(fn).bind(**kwargs)
        except TypeError as exc:
            # A malformed request (missing or unknown field) never reaches the
            # provider: the caller gets a structured, non-retryable error.
            if self.store is not None:
                self.store.log_tool_call(
                    request_id=self.request_id,
                    tool=tool,
                    args=_safe_args(kwargs),
                    status="invalid",
                    latency_ms=0,
                    error_class=FailureKind.INVALID_REQUEST.value,
                )
            raise ProviderError(
                f"{tool} request is malformed: {exc}",
                kind=FailureKind.INVALID_REQUEST,
                source=tool,
                retryable=False,
            ) from exc
        except ValueError:
            pass  # no introspectable signature; the provider validates its own arguments
        if self.max_calls is not None and self.calls >= self.max_calls:
            if self.store is not None:
                self.store.log_tool_call(
                    request_id=self.request_id,
                    tool=tool,
                    args=_safe_args(kwargs),
                    status="blocked",
                    latency_ms=0,
                    error_class="budget",
                    source="toolbox",
                )
            self.refused.append(tool)
            raise ToolBudgetExceeded(
                detail=(
                    f"{tool} was not called: this turn already made {self.calls} tool calls "
                    f"(the limit is {self.max_calls})"
                )
            )
        started = time.perf_counter()
        self.calls += 1
        try:
            with self.tracer.span(f"tool.{tool}", path=self.path) as span:
                try:
                    result = await fn(**kwargs)
                except asyncio.CancelledError as exc:
                    span.attrs["status"] = "timeout"
                    span.attrs["reason"] = str(exc.args[0]) if exc.args else "cancelled"
                    raise
                # Fetched text is data: instruction-like text is replaced before
                # any agent, prompt or reply sees it (Proposal TC-05).
                screened: list[str] = []
                result = neutralise(result, screened)
                if screened:
                    span.attrs["screened"] = sorted(set(screened))
                    if self.store is not None:
                        self.store.log_injection("tool_result_injection", "high", channel="tool_result")
                span.attrs["status"] = "ok"
                latency = int((time.perf_counter() - started) * 1000)
                if self.store is not None:
                    self.store.log_tool_call(
                        request_id=self.request_id,
                        tool=tool,
                        args=_safe_args(kwargs),
                        status="ok",
                        latency_ms=latency,
                        source=getattr(fn, "__self__", None).__class__.__name__ if getattr(fn, "__self__", None) else "",
                    )
                _remember_tool(self.tracer, tool, "ok", latency, kwargs, result)
                emit_live("tool", _tool_event(tool, "ok", latency, kwargs, result))
                return result
        except ProviderError as exc:
            latency = int((time.perf_counter() - started) * 1000)
            if self.store is not None:
                self.store.log_tool_call(
                    request_id=self.request_id,
                    tool=tool,
                    args=_safe_args(kwargs),
                    status="error",
                    latency_ms=latency,
                    error_class=exc.kind.value,
                    source=exc.source,
                )
            _remember_tool(self.tracer, tool, "error", latency, kwargs, None)
            emit_live("tool", _tool_event(tool, "error", latency, kwargs, None))
            raise
        except Exception:
            latency = int((time.perf_counter() - started) * 1000)
            _remember_tool(self.tracer, tool, "error", latency, kwargs, None)
            emit_live("tool", _tool_event(tool, "error", latency, kwargs, None))
            if self.store is not None:
                self.store.log_tool_call(
                    request_id=self.request_id,
                    tool=tool,
                    args=_safe_args(kwargs),
                    status="error",
                    latency_ms=latency,
                    error_class=FailureKind.SERVER_ERROR.value,
                )
            raise
        except asyncio.CancelledError:
            # The caller stopped waiting (a call deadline, the pre-planning
            # budget, or an abandoned turn): the call ran but has no result.
            latency = int((time.perf_counter() - started) * 1000)
            if self.store is not None:
                self.store.log_tool_call(
                    request_id=self.request_id,
                    tool=tool,
                    args=_safe_args(kwargs),
                    status="timeout",
                    latency_ms=latency,
                    error_class=FailureKind.TIMEOUT.value,
                )
            raise


class LoggedWebSource:
    """A web search backend whose calls go through the toolbox, so each is logged."""

    def __init__(self, source: Any, toolbox: Toolbox):
        self._source = source
        self._toolbox = toolbox

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)

    async def search(self, **kwargs: Any) -> Any:
        return await self._toolbox.call("web.search", self._source.search, **kwargs)

    async def answer(self, **kwargs: Any) -> Any:
        return await self._toolbox.call("web.search", self._source.answer, **kwargs)


def _remember_tool(tracer: Tracer, tool: str, status: str, latency_ms: int, kwargs: dict[str, Any], result: Any) -> None:
    record = getattr(tracer, "record_tool", None)
    if record is None:
        return
    note = ""
    if isinstance(result, list):
        note = f"{len(result)} rows"
    elif isinstance(result, dict) and (result.get("name") or result.get("title")):
        note = str(result.get("name") or result.get("title"))[:160]
    record(name=tool, status=status, latency_ms=latency_ms, args=_safe_args(kwargs), note=note)


def _tool_event(tool: str, status: str, latency_ms: int, kwargs: dict[str, Any], result: Any) -> dict[str, Any]:
    note = ""
    if isinstance(result, list):
        note = f"{len(result)} rows"
    elif isinstance(result, dict) and (result.get("name") or result.get("title")):
        note = str(result.get("name") or result.get("title"))[:160]
    payload: dict[str, Any] = {"name": tool, "status": status, "latency_ms": latency_ms, "note": note}
    if developer_view():
        payload["args"] = _safe_args(kwargs)
    return payload


def _safe_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value[:300] if isinstance(value, str) else value
        else:
            out[key] = type(value).__name__
    return out
