"""Small numeric helpers shared by the metric modules (pure, no I/O)."""

from __future__ import annotations

import statistics
from collections.abc import Sequence


def median(values: Sequence[float]) -> float | None:
    """Median, or None for an empty sample (a report must not invent a number)."""
    return float(statistics.median(values)) if values else None


def rate(numerator: int, denominator: int) -> float | None:
    """numerator / denominator, or None when nothing was measured."""
    return numerator / denominator if denominator else None


def meets(value: float | None, target: float, *, higher_is_better: bool = True) -> bool | None:
    """True/False against a target; None when the value could not be measured."""
    if value is None:
        return None
    return value >= target if higher_is_better else value <= target
