"""Per-path tool allowlist and call logging.

Every provider call in the application goes through this object. That is the one
choke point where a path is prevented from using a tool it does not own.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, TypeVar

from app.domain.errors import ProviderError, SecurityBlocked
from app.domain.models import FailureKind
from app.domain.ports import Tracer
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
            "flights.search",
            "routes.leg",
            "routes.matrix",
            "timezone.resolve",
            "elevation.gain",
            "fx.rates",
            # Reference pages for a section the official sources left empty.
            "web.search",
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
            "timezone.resolve",
            "elevation.gain",
            "fx.rates",
            "web.search",
        }
    ),
    "ask": frozenset({"geocode", "weather.forecast", "routes.leg", "places.search", "web.search"}),
    "clarify": frozenset(),
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
    ):
        self.path = path
        base = PATH_ALLOWLIST.get(path, frozenset())
        self.allowed = base | (extra_allowed or frozenset())
        self.tracer = tracer
        self.store = store
        self.request_id = request_id
        self.calls = 0

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
        started = time.perf_counter()
        self.calls += 1
        try:
            with self.tracer.span(f"tool.{tool}", path=self.path) as span:
                result = await fn(**kwargs)
                # Fetched text is data: instruction-like text is replaced before
                # any agent, prompt or reply sees it (Proposal TC-05).
                screened: list[str] = []
                result = neutralise(result, screened)
                if screened:
                    span.attrs["screened"] = sorted(set(screened))
                    if self.store is not None:
                        self.store.log_injection("tool_result_injection", "high")
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
            raise
        except Exception:
            latency = int((time.perf_counter() - started) * 1000)
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


def _safe_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value[:300] if isinstance(value, str) else value
        else:
            out[key] = type(value).__name__
    return out
