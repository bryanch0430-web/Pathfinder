"""In-memory sliding-window rate limiting, one set of counters per app.

Single-node by design, like the SQLite store: counts live in this process and
start empty on restart. Callers are keyed by the socket peer address
(`request.client.host`); `X-Forwarded-For` is never read, because any client
can set it.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import HTTPException, Request

#: Sweep keys with no recent hits after this many calls, so idle callers do not
#: accumulate.
_SWEEP_EVERY = 512


class SlidingWindowLimiter:
    """At most `limit` hits per key within any `window_seconds` span.

    A refused hit is not recorded, so a caller that keeps knocking is let in
    again as soon as its oldest counted hit leaves the window. A limit of 0 or
    less turns the limiter off.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def hit(self, key: str) -> float:
        """Count one request for `key`: 0.0 if allowed, else seconds until a slot frees."""
        if self.limit <= 0:
            return 0.0
        now = self.clock()
        cutoff = now - self.window_seconds
        with self._lock:
            self._calls += 1
            if self._calls % _SWEEP_EVERY == 0:
                self._sweep(cutoff)
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                # hits[0] > cutoff, so this is always positive.
                return hits[0] + self.window_seconds - now
            hits.append(now)
            return 0.0

    def _sweep(self, cutoff: float) -> None:
        for key in [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]


@dataclass
class RateLimits:
    """The per-app limiters; lives on `app.state.rate_limits`."""

    #: sign-in and sign-up, against password guessing and account spam
    auth: SlidingWindowLimiter
    #: routes that start a model or planner run, against cost and load abuse
    ai: SlidingWindowLimiter
    #: new trips and share links, which cost the caller nothing and are kept for good
    create: SlidingWindowLimiter


def client_key(request: Request) -> str:
    """The caller's socket address; proxy headers are deliberately ignored."""
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, message: str) -> Callable[[Request], Awaitable[None]]:
    """A route dependency that answers 429 with Retry-After once `bucket` is full."""

    async def check(request: Request) -> None:
        limiter: SlidingWindowLimiter = getattr(request.app.state.rate_limits, bucket)
        wait = limiter.hit(client_key(request))
        if wait > 0:
            seconds = max(1, math.ceil(wait))
            raise HTTPException(
                status_code=429,
                detail=f"{message} Try again in {seconds} seconds.",
                headers={"Retry-After": str(seconds)},
            )

    return check
