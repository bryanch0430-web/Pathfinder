"""Public cards from trips a traveller marked useful.

Only destination, length, year, sight names and the average star rating leave
this module. Origin, budget, party size, guide and who planned the trip stay in
the row.
"""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MessageRole
from app.models.trip import ConversationMessage, ConversationSession, Trip

_PRIVATE_STOP = re.compile(r"\b(flight|fly|airport|hotel|check-?in|overnight)\b", re.I)


def _year(preferences: dict) -> int | None:
    start = str(preferences.get("start_date") or "")
    return int(start[:4]) if len(start) >= 4 and start[:4].isdigit() else None


def public_shape(day_plan: list | None) -> tuple[list[str], list[str]]:
    """Overnight cities and sight names. Flights, hotels, origin and budget stay out."""
    stays: list[str] = []
    sights: list[str] = []
    for day in day_plan or []:
        if not isinstance(day, dict):
            continue
        base = str(day.get("base") or "").strip()
        if base and base.casefold() not in {item.casefold() for item in stays}:
            stays.append(base)
        for item in day.get("items") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or len(name) > 80 or _PRIVATE_STOP.search(name):
                continue
            if name.casefold() not in {seen.casefold() for seen in sights}:
                sights.append(name)
    return stays[:4], sights[:8]


def _places(trip: Trip) -> list[str]:
    names: list[str] = []
    for raw in trip.places_to_visit or []:
        name = str(raw).strip()
        if name and len(name) <= 80 and name.casefold() not in {item.casefold() for item in names}:
            names.append(name)
    return names[:8]


async def _latest_day_plans(db: AsyncSession) -> dict:
    rows = (await db.execute(
        select(ConversationSession.trip_id, ConversationMessage.payload)
        .join(ConversationMessage, ConversationMessage.session_id == ConversationSession.id)
        .where(ConversationMessage.role == MessageRole.ASSISTANT)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
    )).all()
    plans: dict = {}
    seen: set = set()
    for trip_id, payload in rows:
        if trip_id in seen or not isinstance(payload, dict):
            continue
        kind = payload.get("kind")
        if kind == "PLAN_CLEARED":
            seen.add(trip_id)
        elif kind == "ITINERARY_GRAPH" and isinstance(payload.get("day_plan"), list):
            seen.add(trip_id)
            plans[trip_id] = payload["day_plan"]
    return plans


async def trending_cards(db: AsyncSession, query: str = "", limit: int = 6) -> dict:
    rows = list((await db.execute(select(Trip))).scalars().all())
    day_plans = await _latest_day_plans(db)
    grouped: dict[str, dict] = {}
    for trip in rows:
        preferences = trip.planning_preferences or {}
        if preferences.get("useful") is not True:
            continue
        destination = (trip.destination or "").strip()
        if not destination:
            continue
        key = destination.casefold()
        group = grouped.setdefault(key, {
            "destination": destination,
            "plans": 0,
            "days": [],
            "places": Counter(),
            "stays": Counter(),
            "years": [],
            "stars": [],
        })
        group["plans"] += 1
        if trip.duration_days:
            group["days"].append(trip.duration_days)
        stays, sights = public_shape(day_plans.get(trip.id))
        for name in stays:
            group["stays"][name] += 1
        for name in [*sights, *_places(trip)]:
            group["places"][name] += 1
        year = _year(preferences)
        if year:
            group["years"].append(year)
        stars = preferences.get("stars")
        if isinstance(stars, int) and 1 <= stars <= 5:
            group["stars"].append(stars)

    needle = query.strip().casefold()
    items: list[dict] = []
    for group in grouped.values():
        places = [name for name, _count in group["places"].most_common(8)]
        stays = [name for name, _count in group["stays"].most_common(4)]
        if needle and needle not in group["destination"].casefold() and not any(needle in name.casefold() for name in [*places, *stays]):
            continue
        days = Counter(group["days"]).most_common(1)[0][0] if group["days"] else None
        stars = round(sum(group["stars"]) / len(group["stars"]), 1) if group["stars"] else None
        items.append({
            "destination": group["destination"],
            "plans": group["plans"],
            "days": days,
            "year": max(group["years"]) if group["years"] else None,
            "stays": stays,
            "places": places,
            "stars": stars,
        })
    items.sort(key=lambda row: (-row["plans"], -(row["stars"] or 0), row["destination"].casefold()))
    return {
        "items": items[: max(1, min(limit, 12))],
        "note": "Trips travellers marked useful. Destination, length, year, overnight cities, sight names and average stars only.",
    }
