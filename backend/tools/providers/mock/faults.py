"""Deterministic fault injection and scenario overrides for the mock providers.

Used by tests and by the evaluation harness (Hong Kong disruption scenarios: typhoon signal, MTR
delay, venue closure). Everything is explicit and queue-based (no randomness), so a scenario
replays identically every run.

Matching rules: city arguments use the same tolerant lookup as the mock dataset ("HK",
"Hong Kong", "hong kong, china" are one city); venue names match case-insensitively by full
name, by the name without its parenthetical, or by place_id.
"""

from __future__ import annotations

from collections import deque
from datetime import date
from enum import StrEnum
from typing import Literal

from backend.schemas.tools import ToolOperation
from backend.tools.providers.mock.data import city_key, matches_place


class FaultKind(StrEnum):
    TIMEOUT = "timeout"  # raises ProviderTimeout
    RATE_LIMIT = "rate_limit"  # raises ProviderRateLimited
    SERVER_FAULT = "server_fault"  # raises ProviderServerError
    BAD_REQUEST = "bad_request"  # raises ProviderBadRequest
    UNAVAILABLE = "unavailable"  # raises ProviderUnavailable


_QueueKey = tuple[ToolOperation, str | None]


class FaultPlan:
    def __init__(self) -> None:
        self._queues: dict[_QueueKey, deque[FaultKind]] = {}
        self._always: dict[_QueueKey, FaultKind] = {}
        self._latency: dict[ToolOperation, float] = {}
        self._closures: list[tuple[str, date]] = []
        self._warnings: dict[tuple[str, date], str] = {}
        self._delays: list[tuple[str, date, int, Literal["train", "flight"]]] = []
        self._injections: dict[str, list[str]] = {}

    def fail(self, operation: ToolOperation, *faults: FaultKind, provider: str | None = None) -> FaultPlan:
        """Queue faults consumed one per call to `operation` (optionally only for the provider
        with that name, e.g. 'mock-google' in the maps chain). Calls succeed once the queue is
        empty. Returns self for chaining.

        A generic (provider=None) fault is consumed by whichever provider serves the next call,
        so in the maps chain it hits mock-google first and mock-amap then answers."""
        self._queues.setdefault((operation, provider), deque()).extend(faults)
        return self

    def fail_always(
        self, operation: ToolOperation, kind: FaultKind, provider: str | None = None
    ) -> FaultPlan:
        """Every call to `operation` (on `provider`, or on any provider) fails with `kind`."""
        self._always[(operation, provider)] = kind
        return self

    def set_latency(self, operation: ToolOperation, seconds: float) -> FaultPlan:
        """Simulated latency (asyncio.sleep) before the provider answers; used for time budgets."""
        self._latency[operation] = max(0.0, seconds)
        return self

    def close_venue(self, name_or_id: str, on: date) -> FaultPlan:
        """Places results list `on` in closed_dates for the matching attraction."""
        self._closures.append((name_or_id, on))
        return self

    def weather_warning(self, location: str, on: date, signal: str) -> FaultPlan:
        """The forecast for `on` carries `warning_signal=signal` (e.g. 'T8')."""
        self._warnings[(city_key(location), on)] = signal
        return self

    def delay_transit(
        self, city: str, on: date, minutes: int, mode: Literal["train", "flight"] = "train"
    ) -> FaultPlan:
        """Ticket results of `mode` touching `city` on `on` come back delayed by `minutes`."""
        self._delays.append((city_key(city), on, max(0, minutes), mode))
        return self

    def inject_search_text(self, destination: str, text: str) -> FaultPlan:
        """Web search results for `destination` gain one extra hit whose snippet is `text`
        (indirect prompt-injection probes: untrusted text arriving through a tool result)."""
        self._injections.setdefault(city_key(destination), []).append(text)
        return self

    def next_fault(self, operation: ToolOperation, provider: str) -> FaultKind | None:
        """Pop the next queued fault for this call (provider-specific queue first), else the
        persistent `fail_always` fault (provider-specific first), else None."""
        for key in ((operation, provider), (operation, None)):
            queue = self._queues.get(key)
            if queue:
                return queue.popleft()
        for key in ((operation, provider), (operation, None)):
            kind = self._always.get(key)
            if kind is not None:
                return kind
        return None

    # ---- read side used by the mock providers ------------------------------------------------

    def latency(self, operation: ToolOperation) -> float:
        return self._latency.get(operation, 0.0)

    def closed_dates(self, name: str, pid: str) -> list[date]:
        return sorted({on for wanted, on in self._closures if matches_place(name, pid, wanted)})

    def warning_for(self, location_key: str, on: date) -> str | None:
        return self._warnings.get((location_key, on))

    def transit_delay(
        self, mode: Literal["train", "flight"], on: date, *city_keys: str
    ) -> int | None:
        """Largest configured delay for `mode` on `on` touching any of `city_keys`."""
        hits = [m for key, d, m, md in self._delays if md == mode and d == on and key in city_keys]
        return max(hits) if hits else None

    def injected_texts(self, destination_key: str) -> list[str]:
        return list(self._injections.get(destination_key, []))

    def pending(self, operation: ToolOperation, provider: str | None = None) -> int:
        """Queued (not yet consumed) faults for (operation, provider)."""
        return len(self._queues.get((operation, provider), ()))


__all__ = ["FaultKind", "FaultPlan"]
