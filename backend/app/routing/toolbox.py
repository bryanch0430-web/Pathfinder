"""Per-path tool allowlist. A question cannot book, search hotels, or rewrite the plan."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

PATH_ALLOWLIST: dict[str, frozenset[str]] = {
    "plan": frozenset({
        "geocode", "weather.forecast", "places.search", "hotels.search", "flights.search",
        "routes.leg", "fx.rates", "web.search",
    }),
    "modify": frozenset({
        "geocode", "weather.forecast", "places.search", "hotels.search", "flights.search",
        "routes.leg", "fx.rates", "web.search",
    }),
    "ask": frozenset({"geocode", "weather.forecast", "routes.leg", "places.search", "web.search"}),
    "clarify": frozenset(),
}


class ToolRefused(Exception):
    def __init__(self, tool: str, reason: str):
        super().__init__(reason)
        self.tool = tool
        self.reason = reason


class Toolbox:
    def __init__(self, path: str, *, max_calls: int = 3):
        self.path = path
        self.allowed = PATH_ALLOWLIST.get(path, frozenset())
        self.max_calls = max_calls
        self.calls = 0
        self.log: list[dict[str, Any]] = []

    async def call(self, tool: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        if tool not in self.allowed:
            self.log.append({"tool": tool, "status": "refused"})
            raise ToolRefused(tool, "not on this path")
        if self.calls >= self.max_calls:
            self.log.append({"tool": tool, "status": "budget"})
            raise ToolRefused(tool, "tool budget")
        self.calls += 1
        started = time.perf_counter()
        try:
            value = await fn()
        except Exception as exc:
            self.log.append({"tool": tool, "status": "error", "error": type(exc).__name__})
            return None
        self.log.append({"tool": tool, "status": "ok", "ms": round((time.perf_counter() - started) * 1000)})
        return value
