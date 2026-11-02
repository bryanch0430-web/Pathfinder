"""Deterministic before/after check for the open-path 2-opt route pass.

Run: PYTHONPATH=. python -m app.route_bench
"""

from __future__ import annotations

import random

from app.application.planner import _route_length, _two_opt


def run() -> dict[str, float]:
    rng = random.Random(30)
    before = 0.0
    after = 0.0
    days = 40
    for _ in range(days):
        rows = [
            {"geo": {"lat": rng.uniform(22.2, 22.5), "lng": rng.uniform(114.0, 114.4)}}
            for _ in range(6)
        ]
        before += _route_length(rows)
        after += _route_length(_two_opt(rows))
    return {
        "days": days,
        "avg_before_km": round(before / days, 3),
        "avg_after_km": round(after / days, 3),
        "reduction_pct": round((1 - after / before) * 100, 2) if before else 0.0,
    }


if __name__ == "__main__":
    print(run())
