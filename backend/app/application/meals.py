"""Meal options near each day's stops, and nearby alternatives for attractions.

After the writer has assigned the day's stops, each day's breakfast, lunch, dinner and an
optional cafe stop are searched around that day's own stops (Places text search with a
location bias at the day's centroid), all in parallel inside one wall-clock budget. A search
that fails, times out or comes back empty leaves the city-wide restaurant pool the attraction
agent already fetched. No model is called here.

Every fact on an option comes from the place source; the app adds only computed distances.
A fact the source did not give stays empty (``reservable`` None, no cuisine, no rating).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.application.opening_hours import windows_on
from app.config import Settings
from app.domain.models import (
    SOURCE_SUMMARY_MAX,
    GeoPoint,
    ImageRef,
    PlanAlternative,
    SourceRef,
)
from app.infrastructure.http import haversine_km

logger = logging.getLogger(__name__)

MEAL_SLOTS = ("breakfast", "lunch", "dinner")
#: The place-search category each slot is looked up with (lunch and dinner share one search).
SEARCH_CATEGORY = {"breakfast": "breakfast", "lunch": "restaurant", "dinner": "restaurant", "snack": "cafe"}
#: When the slot's meal may happen, minutes after midnight: a venue the source says is shut for the
#: whole of it on that weekday is not offered.
SLOT_WINDOW = {
    "breakfast": (8 * 60, 9 * 60 + 30),
    "lunch": (12 * 60 + 30, 14 * 60 + 30),
    "dinner": (19 * 60, 21 * 60 + 30),
    "snack": (14 * 60, 18 * 60),
}
#: One default on the timeline plus up to two alternatives.
OPTIONS_PER_SLOT = 3
#: Results asked for per nearby search.
NEARBY_LIMIT = 8
#: The location-bias radius around a day's centroid.
NEARBY_RADIUS_M = 1500.0
#: How much one kilometre from the day's centre costs against rating * log(reviews).
DISTANCE_WEIGHT = 2.0
#: An attraction's alternative is a place at most this far from it.
ALTERNATIVE_MAX_KM = 5.0


def day_centre(rows: list[dict], fallback: dict | None) -> dict | None:
    """The centroid of the day's located stops, else the fallback (hotel or city centre)."""
    points = [_geo(row) for row in rows]
    points = [point for point in points if point is not None]
    if not points:
        return fallback
    return {"lat": sum(p[0] for p in points) / len(points), "lng": sum(p[1] for p in points) / len(points)}


async def search_nearby(
    *,
    sources: list[Any],
    toolbox: Any,
    destination: str,
    centres: dict[int, dict],
    settings: Settings,
) -> dict[int, dict[str, list[dict]]]:
    """Breakfast, restaurant and cafe results near each day's centre: {day: {category: rows}}.

    Only a source that takes a location bias (``supports_bias``) is asked: a city-wide list would
    add nothing to the pool already fetched. The searches run together; each has its own timeout
    and all of them share ``meal_search_budget_seconds``. A failed, timed-out or empty search
    leaves its slot to the city-wide pool.
    """
    source = next(
        (s for s in sources if getattr(s, "available", True) and getattr(s, "supports_bias", False) is True), None
    )
    budget = float(getattr(settings, "meal_search_budget_seconds", 15.0) or 0)
    if source is None or not centres or budget <= 0:
        return {}
    per_call = max(0.1, min(float(settings.http_timeout_seconds), budget))
    categories = sorted(set(SEARCH_CATEGORY.values()))

    async def one(index: int, category: str, centre: dict) -> tuple[int, str, list[dict]]:
        kwargs = dict(
            destination=destination,
            geo=GeoPoint(lat=float(centre["lat"]), lng=float(centre["lng"])),
            category=category,
            limit=NEARBY_LIMIT,
            query=None,
            bias=(float(centre["lat"]), float(centre["lng"]), NEARBY_RADIUS_M),
        )
        call = toolbox.call("places.search", source.search, **kwargs) if toolbox is not None else source.search(**kwargs)
        results = await asyncio.wait_for(call, timeout=per_call)
        rows: list[dict] = []
        for position, place in enumerate(results or []):
            row = place.model_dump(mode="json") if hasattr(place, "model_dump") else dict(place)
            if _geo(row) is None or not str(row.get("name") or "").strip():
                continue
            rows.append({**row, "id": venue_id(row)})
        return index, category, rows

    tasks = [
        asyncio.ensure_future(one(index, category, centre))
        for index, centre in sorted(centres.items())
        for category in categories
    ]
    done, pending = await asyncio.wait(tasks, timeout=budget)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    found: dict[int, dict[str, list[dict]]] = {}
    for task in done:
        if task.cancelled() or task.exception() is not None:
            if not task.cancelled():
                logger.info("nearby meal search failed: %s", type(task.exception()).__name__)
            continue
        index, category, rows = task.result()
        if rows:
            found.setdefault(index, {})[category] = rows
    return found


def choose_meals(
    *,
    pool: list[dict],
    nearby: dict[int, dict[str, list[dict]]],
    assignment: dict[int, list[dict]],
    dates: list[date],
    fallback_centre: dict | None,
    suitable: Callable[[dict], bool] | None = None,
) -> dict[int, dict[str, dict]]:
    """Each day's meals: {day: {"breakfast"|"lunch"|"dinner"|"snack": row}}.

    Each chosen row is a copy of a source row with ``meal_slot`` and ``alternatives`` (up to two
    other source rows for the same slot). Lunch is offered every day; breakfast, dinner and the
    cafe stop on a day with stops (the cafe stop on a day with two or more). Breakfast and the
    cafe stop need that day's nearby search; lunch and dinner add the city-wide pool to it.
    ``suitable`` is the hard diet filter; a venue the source says is shut for the whole slot on
    that weekday is never offered.

    Ranking: with nearby results, venues not yet used elsewhere in the trip first, then rating x
    log(reviews) less a distance cost; a used venue is reused only when nothing else is left. From
    the city-wide pool alone, the nearest venue no other day has (the planner's long-standing pick).
    """
    keep = suitable or (lambda row: True)
    pool = [row for row in pool if _geo(row) is not None and keep(row)]
    used: dict[str, int] = {}
    default_ids: set[str] = set()
    plan: dict[int, dict[str, dict]] = {}
    for index in sorted(assignment):
        if index >= len(dates):
            continue
        rows = assignment[index]
        centre = day_centre(rows, fallback_centre)
        if centre is None:
            continue
        day_date = dates[index]
        day_nearby = nearby.get(index, {})
        slots = [*MEAL_SLOTS, "snack"] if len(rows) >= 2 else (list(MEAL_SLOTS) if rows else ["lunch"])
        ranked_by_slot: dict[str, list[dict]] = {}
        taken_today: set[str] = set()
        # Defaults first, slot by slot, so a day's alternatives never take another slot's default.
        for slot in slots:
            # A nearby search for the slot's own category is suitable as it stands; a pool row only by type.
            local = [row for row in day_nearby.get(SEARCH_CATEGORY[slot], []) if keep(row)]
            # Breakfast and the cafe stop come only from their own nearby search; lunch and dinner
            # fall back to the city-wide pool.
            fallback = pool if slot in {"lunch", "dinner"} else []
            candidates = [row for row in _dedupe([*local, *fallback]) if _open_for(row, day_date, slot)]
            if local:
                ranked = sorted(candidates, key=lambda row: (used.get(_key(row), 0), -_score(row, centre)))
            else:
                # The pool alone: nearest first, and never a venue another day already has.
                ranked = sorted((row for row in candidates if not used.get(_key(row))), key=lambda row: _km(row, centre))
            ranked = [row for row in ranked if _key(row) not in taken_today]
            if not ranked:
                continue
            ranked_by_slot[slot] = ranked
            default = dict(ranked[0])
            key = _key(default)
            used[key] = used.get(key, 0) + 1
            taken_today.add(key)
            # A reused venue is a second stop in the plan; its item id must stay unique.
            # The id follows the venue, so another venue never takes a stop's id (and its facts) later.
            base_id = venue_id(default)
            item_id = base_id if base_id not in default_ids else f"{base_id}r{index + 1}{slot[0]}"
            default_ids.add(item_id)
            default["id"] = item_id
            default["meal_slot"] = slot
            plan.setdefault(index, {})[slot] = default
        for slot, ranked in ranked_by_slot.items():
            alternatives = [dict(row) for row in ranked[1:] if _key(row) not in taken_today][: OPTIONS_PER_SLOT - 1]
            taken_today.update(_key(row) for row in alternatives)
            plan[index][slot]["alternatives"] = alternatives
    return plan


def alternatives_for(row: dict) -> list[PlanAlternative]:
    """The ``alternatives`` rows on a chosen meal as plan alternatives, with their distance from it."""
    origin = _geo(row)
    out: list[PlanAlternative] = []
    for other in row.get("alternatives") or []:
        alternative = to_alternative(other, origin)
        if alternative is not None:
            out.append(alternative)
    return out


def to_alternative(row: dict, origin: tuple[float, float] | None) -> PlanAlternative | None:
    """A source row as a plan alternative. Only the distance is the app's own."""
    name = str(row.get("name") or "").strip()
    if not name:
        return None
    point = _geo(row)
    distance = None
    if origin is not None and point is not None:
        distance = int(round(haversine_km(origin[0], origin[1], point[0], point[1]) * 1000))
    refs: list[SourceRef] = []
    for ref in row.get("source_refs") or []:
        try:
            refs.append(SourceRef.model_validate(ref))
        except Exception:
            continue
    return PlanAlternative(
        id=f"alt_{venue_id(row)}",
        name=name,
        geo=GeoPoint(lat=point[0], lng=point[1]) if point is not None else None,
        address=str(row.get("address") or ""),
        rating=_float(row.get("rating")),
        review_count=_int(row.get("review_count")),
        price_level=price_text(row),
        cuisine=(str(row["cuisine"]) if row.get("cuisine") else None),
        reservable=row.get("reservable") if isinstance(row.get("reservable"), bool) else None,
        opening_hours=[str(line) for line in row.get("opening_hours") or [] if str(line).strip()],
        source_refs=refs,
        booking_url=str(row.get("booking_url") or ""),
        distance_m=distance,
        image=image_of(row),
        **_source_labels(row),
    )


def price_text(row: dict) -> str | None:
    """The source's price range when it gave one, else its price level; None when it gave neither."""
    text = str(row.get("price_range") or "").strip() or str(row.get("price_level") or "").strip()
    return text or None


def source_facts(row: dict) -> dict[str, Any]:
    """The place facts a meal item carries, exactly as the source gave them."""
    return {
        "rating": _float(row.get("rating")),
        "review_count": _int(row.get("review_count")),
        "price_level": price_text(row),
        "cuisine": str(row["cuisine"]) if row.get("cuisine") else None,
        "reservable": row.get("reservable") if isinstance(row.get("reservable"), bool) else None,
        "image": image_of(row),
        **_source_labels(row),
    }


def image_of(row: dict) -> ImageRef | None:
    """The picture a source row carries, or None (a row from an older plan or a source with none has no ``image``)."""
    value = row.get("image")
    if isinstance(value, ImageRef):
        return value
    if isinstance(value, dict):
        try:
            return ImageRef.model_validate(value)
        except ValidationError:
            return None
    return None


def _source_labels(row: dict) -> dict[str, Any]:
    """The source's own type labels and short description, kept so later checks read what compose read."""
    return {
        "source_types": [str(tag) for tag in row.get("tags") or [] if str(tag).strip()][:12],
        "source_summary": str(row.get("description") or "").strip()[:SOURCE_SUMMARY_MAX],
    }


def venue_id(row: dict) -> str:
    """A short id that follows the venue: from its first source link, else its name and position.

    Positional ids ("the first lunch result") would let a recompose put another venue under an id
    the plan already uses; this one changes whenever the venue does.
    """
    refs = row.get("source_refs") or []
    first = refs[0] if refs else None
    url = (first.get("url") if isinstance(first, dict) else getattr(first, "url", "")) if first is not None else ""
    if url:
        key = f"url:{url}"
    else:
        point = _geo(row)
        where = f"{point[0]:.4f},{point[1]:.4f}" if point is not None else ""
        key = f"name:{_key(row)}@{where}"
    return "v" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def attraction_alternative(
    item_geo: dict | None,
    unused: list[dict],
    day_date: date,
    given: set[str],
) -> PlanAlternative | None:
    """The nearest unused place candidate open on ``day_date``, within ALTERNATIVE_MAX_KM.

    One not yet offered elsewhere in the trip is preferred; ``given`` collects the ones offered.
    """
    origin = _geo({"geo": item_geo})
    if origin is None:
        return None
    options = []
    for row in unused:
        point = _geo(row)
        if point is None or windows_on(row.get("opening_hours") or [], day_date) == []:
            continue
        km = haversine_km(origin[0], origin[1], point[0], point[1])
        if km <= ALTERNATIVE_MAX_KM:
            options.append((_key(row) in given, km, row))
    if not options:
        return None
    _, _, row = min(options, key=lambda option: (option[0], option[1]))
    given.add(_key(row))
    return to_alternative(row, origin)


# --- helpers ---------------------------------------------------------------


def _geo(row: dict | None) -> tuple[float, float] | None:
    geo = (row or {}).get("geo") or {}
    if not isinstance(geo, dict):
        geo = {"lat": getattr(geo, "lat", None), "lng": getattr(geo, "lng", None)}
    if geo.get("lat") is None or geo.get("lng") is None:
        return None
    return float(geo["lat"]), float(geo["lng"])


def _km(row: dict, centre: dict) -> float:
    point = _geo(row)
    if point is None:
        return 10.0**6
    return haversine_km(centre["lat"], centre["lng"], point[0], point[1])


def _score(row: dict, centre: dict) -> float:
    rating = _float(row.get("rating")) or 0.0
    reviews = _int(row.get("review_count")) or 0
    return rating * math.log10(reviews + 1) - DISTANCE_WEIGHT * _km(row, centre)


def _key(row: dict) -> str:
    return " ".join(str(row.get("name") or "").lower().split())


def _dedupe(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        key = _key(row)
        if key and key not in seen:
            seen.add(key)
            out.append(row)
    return out


def _open_for(row: dict, day_date: date, slot: str) -> bool:
    """False only when the source's hours say the venue is shut for the whole slot that day."""
    windows = windows_on(row.get("opening_hours") or [], day_date)
    if windows is None:
        return True
    start, end = SLOT_WINDOW[slot]
    return any(opens < end and closes > start for opens, closes in windows)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
