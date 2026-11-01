"""Aggregate public trending cards from retrieved saved journeys.

Retrieval decides *which* journeys are candidates (see `trending_rag.py`); this
module turns them into destination cards. Nothing here is personal: only
destination, length, year and sight names leave the module. Preference tags are
used for ranking, and only the caller's own matched tags are echoed back as a
`match` reason.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Sequence

from app.domain.models import TripPlan


def _tags(plan: TripPlan) -> Counter[str]:
    tags: Counter[str] = Counter()
    for day in plan.days:
        for item in day.items:
            for tag in item.tags:
                value = tag.strip().lower()
                if value:
                    tags[value] += 1
    return tags


def aggregate(
    plans: Sequence[TripPlan],
    *,
    scores: Sequence[float] | None = None,
    preferences: Sequence[str] | None = None,
    query: str = "",
    limit: int = 6,
    mode: str = "popularity",
) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for index, plan in enumerate(plans):
        destination = (plan.destination.name or "").strip()
        if not destination or not plan.days:
            continue
        key = destination.casefold()
        group = grouped.setdefault(
            key,
            {
                "destination": destination,
                "plans": 0,
                "days": [],
                "places": Counter(),
                "tags": Counter(),
                "year": None,
                "score": 0.0,
            },
        )
        group["plans"] += 1
        group["days"].append(len(plan.days))
        group["tags"].update(_tags(plan))
        for day in plan.days:
            for item in day.items:
                if item.type.value == "attraction" and item.name:
                    group["places"][item.name] += 1
        year = plan.dates.start.year if plan.dates and plan.dates.start else None
        if year:
            group["year"] = max(group["year"] or year, year)
        if scores is not None and index < len(scores):
            group["score"] = max(group["score"], float(scores[index]))

    wanted = {tag.strip().lower() for tag in (preferences or []) if tag.strip()}
    items: list[dict[str, Any]] = []
    for group in grouped.values():
        places = [name for name, _ in group["places"].most_common(6)]
        days = Counter(group["days"]).most_common(1)[0][0]
        overlap = sorted(wanted & set(group["tags"]))[:3]
        rank = (
            group["score"]
            + 0.15 * math.log1p(group["plans"])
            + 0.15 * len(overlap)
            + 0.001 * ((group["year"] or 0) - 2000)
        )
        item: dict[str, Any] = {
            "destination": group["destination"],
            "plans": group["plans"],
            "likes": group["plans"],
            "days": days,
            "year": group["year"],
            "places": places,
            "summary": f"{days}-day plan · {len(places)} sights",
            "score": round(rank, 4),
        }
        if overlap:
            # Only the requester's own matched tags; never another traveller's.
            item["match"] = overlap
        items.append(item)

    items.sort(key=lambda row: (-row["score"], -(row["year"] or 0), row["destination"]))
    note = (
        "Retrieved from trips marked useful by travellers with a hybrid lexical + "
        "dense search, then fused and aggregated. Only destination, trip length, year "
        "and sight names are exposed; budgets, party size, session ids and other "
        "travellers' preferences never leave the store."
        if mode != "popularity"
        else "Aggregated from trips marked useful by travellers. Only destination, trip "
        "length, year and sight names are exposed; budgets, party size, session ids and "
        "other travellers' preferences never leave the store."
    )
    return {
        "items": items[: max(1, min(limit, 12))],
        "trace": {
            "mode": mode,
            "candidates": len(plans),
            "note": note,
            "filter": query.strip(),
            "dropped": [],
        },
    }


def trending_report(
    plans: Sequence[TripPlan],
    query: str = "",
    limit: int = 6,
    preferences: Sequence[str] | None = None,
) -> dict[str, Any]:
    return aggregate(plans, query=query, limit=limit, preferences=preferences)
