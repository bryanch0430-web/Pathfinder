"""Reference exchange rates with a dated static fallback.

Live rates come from open.er-api.com (keyless, cached). If the call fails the
static table keeps the budget meaningful and every converted figure is labelled
as a reference estimate.
"""

from __future__ import annotations

import time
from datetime import date

from app.infrastructure.http import request_json

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


class FxService:
    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout
        self._rates: dict[str, float] = dict(STATIC_USD_RATES)
        self._fetched_at: float = 0.0
        self.source = "static"
        self.as_of = date(2026, 10, 1)

    async def refresh(self) -> None:
        if time.time() - self._fetched_at < 12 * 3600 and self.source == "live":
            return
        try:
            data = await request_json(
                "GET",
                "https://open.er-api.com/v6/latest/USD",
                source="open.er-api",
                timeout=self.timeout,
                retries=0,
            )
            rates = data.get("rates") or {}
            if data.get("result") == "success" and isinstance(rates, dict) and rates.get("JPY"):
                merged = dict(STATIC_USD_RATES)
                merged.update({str(k): float(v) for k, v in rates.items() if isinstance(v, (int, float))})
                self._rates = merged
                self.source = "open.er-api"
                self.as_of = date.today()
                self._fetched_at = time.time()
        except Exception:
            self.source = "static"

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
