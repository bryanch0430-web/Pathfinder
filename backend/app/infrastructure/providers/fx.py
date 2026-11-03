"""Reference exchange rates with a dated static fallback.

Live rates come from open.er-api.com (keyless, cached). If the call fails the
static table keeps the budget meaningful and every converted figure is labelled
as a reference estimate.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from typing import Callable

from app.domain.errors import operator_text
from app.infrastructure.http import request_json

logger = logging.getLogger(__name__)

#: How long a good answer is kept before the upstream is asked again.
RATES_TTL_SECONDS = 12 * 3600
#: After a failed fetch the upstream is left alone this long, so a public route is not a
#: request to a dead host (waiting out the timeout) for every page load.
FAILURE_BACKOFF_SECONDS = 60.0
LIVE_SOURCE = "open.er-api"

# rates per 1 USD, approximate reference values; replaced by live data when available
STATIC_USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "JPY": 150.0,
    "HKD": 7.8,
    "CNY": 7.2,
    "SGD": 1.34,
    "THB": 36.0,
    "TWD": 32.0,
    "KRW": 1350.0,
    "AUD": 1.52,
    "CAD": 1.37,
    "CHF": 0.88,
    "INR": 83.0,
    "MYR": 4.7,
    "IDR": 15800.0,
    "PHP": 56.0,
    "VND": 25000.0,
}


def _live_rates(data: object) -> dict[str, float] | None:
    """The rate table of a usable open.er-api answer (the static table under the live rates), else None."""
    if not isinstance(data, dict):
        return None
    rates = data.get("rates")
    if data.get("result") != "success" or not isinstance(rates, dict) or not rates.get("JPY"):
        return None
    merged = dict(STATIC_USD_RATES)
    merged.update({str(k): float(v) for k, v in rates.items() if isinstance(v, (int, float))})
    return merged


class FxService:
    def __init__(self, timeout: float = 8.0, clock: Callable[[], float] = time.time):
        self.timeout = timeout
        self._clock = clock
        self._rates: dict[str, float] = dict(STATIC_USD_RATES)
        self._fetched_at: float = 0.0
        self._retry_at: float = 0.0
        # One fetch at a time: requests that arrive during it wait for its outcome instead of making their own.
        self._fetching = asyncio.Lock()
        self.source = "static"
        self.as_of = date(2026, 10, 1)

    def _settled(self) -> bool:
        """True when there is nothing to fetch: live rates are current, or a fetch failed a moment ago."""
        now = self._clock()
        live = self.source == LIVE_SOURCE and now - self._fetched_at < RATES_TTL_SECONDS
        return live or now < self._retry_at

    async def refresh(self) -> None:
        if self._settled():
            return
        async with self._fetching:
            if self._settled():
                return  # the request ahead of this one has just fetched, or failed
            try:
                data = await request_json(
                    "GET",
                    "https://open.er-api.com/v6/latest/USD",
                    source="open.er-api",
                    timeout=self.timeout,
                    retries=0,
                )
            except Exception as exc:
                logger.warning("exchange rates unavailable, using the static table: %s", operator_text(exc))
                self._fall_back()
                return
            merged = _live_rates(data)
            if merged is None:
                logger.warning("exchange rates unavailable, using the static table: unusable answer")
                self._fall_back()
                return
            self._rates = merged
            self.source = LIVE_SOURCE
            self.as_of = date.today()
            self._fetched_at = self._clock()

    def _fall_back(self) -> None:
        self.source = "static"
        self._retry_at = self._clock() + FAILURE_BACKOFF_SECONDS

    def convert(self, amount: float, from_currency: str, to_currency: str) -> float:
        source = from_currency.upper()
        target = to_currency.upper()
        if source == target:
            return amount
        source_rate = self._rates.get(source)
        target_rate = self._rates.get(target)
        if not source_rate or not target_rate:
            return amount
        return amount / source_rate * target_rate

    def snapshot(self, base: str = "HKD") -> dict:
        base = base.upper()
        base_rate = self._rates.get(base)
        if not base_rate:
            base, base_rate = "USD", 1.0
        return {
            "base": base,
            "rates": {code: round(rate / base_rate, 6) for code, rate in self._rates.items()},
            "source": self.source,
            "as_of": self.as_of.isoformat(),
            "note": self.note(),
        }

    def note(self) -> str:
        return (
            f"Converted at {self.source} reference rates"
            + (f" ({self.as_of.isoformat()})" if self.source == "static" else "")
            + "; card or cash rates may differ."
        )
