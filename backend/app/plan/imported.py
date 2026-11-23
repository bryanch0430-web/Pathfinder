"""Turn a structured image/document import into a sourced draft plan.

The ordinary planner is deliberately single-city: it gathers evidence for the
resolved destination. A full itinerary poster can span Osaka, Kyoto and Tokyo.
This module keeps that imported structure, looks up each named stop, and lets the
existing builder validate, schedule and persist it. If too few stops can be
sourced it returns None and the normal single-city planner runs unchanged.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.config import Settings
from app.core.dates import parse_date
from app.core.trace import TurnTrace
from app.domain.models import ImportedItinerary, ImportedStop, TripContext
from app.evidence.models import Bundle
from app.evidence.providers import Providers
from app.plan.builder import build_plan, validate_structure
from app.understand.schemas import DayChoice, PlanDraft, StopChoice

GENERIC = {
    "arrival", "departure", "check-in", "check in", "check-out", "check out", "free time",
    "rest", "breakfast", "lunch", "dinner", "shopping", "explore", "walk", "hotel",
    "airport", "station", "day trip", "optional", "tbd", "buy", "sell", "buy sell",
}
DEFAULT_TIMES = ["09:30", "12:15", "14:00", "16:00", "19:00"]


@dataclass
class ImportedPlan:
    plan: object
    bundle: Bundle
    sourced: int
    total: int


def _time(value: str, fallback: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return fallback
    words = {"morning": "09:30", "afternoon": "14:00", "evening": "19:00", "night": "20:00"}
    if text in words:
        return words[text]
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if match:
        hour = int(match.group(1)) % 24
        minute = int(match.group(2))
        if 0 <= minute < 60:
            return f"{hour:02d}:{minute:02d}"
    return fallback


def _usable(stop: ImportedStop) -> bool:
    name = (stop.name or "").strip()
    lowered = name.lower()
    if len(name) < 3 or lowered in GENERIC:
        return False
    if any(lowered.startswith(prefix) for prefix in ("check-in", "check in", "check-out", "check out")):
        return False
    return True


async def build_imported_plan(
    *,
    providers: Providers,
    settings: Settings,
    context: TripContext,
    itinerary: ImportedItinerary,
    trace: TurnTrace | None = None,
) -> ImportedPlan | None:
    if not itinerary.days:
        return None
    days = itinerary.days[:15]
    start = parse_date(context.start_date)
    if start is None:
        return None
    total_days = int(context.days or len(days))
    end = parse_date(context.end_date) or (start + timedelta(days=max(0, total_days - 1)))
    fallback_city = (itinerary.cities[0] if itinerary.cities else context.destination or "").strip()

    candidates: list[tuple[int, ImportedStop, str]] = []
    seen_names: set[str] = set()
    for day_index, day in enumerate(days):
        city = (day.city or fallback_city).strip()
        for stop in list(day.stops)[:8]:
            if not _usable(stop):
                continue
            key = (stop.name or "").strip().lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            candidates.append((day_index, stop, city))

    if not candidates:
        return None

    bundle = Bundle()
    semaphore = asyncio.Semaphore(6)
    resolved: dict[int, object] = {}

    async def lookup(index: int, stop: ImportedStop, city: str) -> None:
        async with semaphore:
            try:
                found = await providers.lookup_place(stop.name, city or fallback_city)
            except Exception:  # noqa: BLE001 - one failed lookup is a gap
                found = []
        if not found:
            bundle.gap("import", "unsourced", f"{stop.name} could not be sourced; it was not added.")
            return
        evidence = next((item for item in found if item.source_refs), None)
        if evidence is None:
            bundle.gap("import", "unsourced", f"{stop.name} had no citable source; it was not added.")
            return
        if stop.kind in {"attraction", "meal"}:
            evidence.kind = stop.kind
        evidence.ref = f"im{index}"
        bundle.add([evidence], "import")
        resolved[index] = evidence

    if trace:
        trace.say(f"Importing {len(candidates)} named stops from the image.")
    await asyncio.gather(*(lookup(index, stop, city) for index, (_, stop, city) in enumerate(candidates)))

    # Hotels: the poster usually names one per city. Keep the first sourced one;
    # the existing builder expects a single stay for the cost model.
    hotel_evidence = None
    hotel_names: list[tuple[ImportedStop, str]] = []
    for hotel in list(itinerary.hotels)[:6]:
        if (hotel.name or "").strip():
            hotel_names.append((hotel, hotel.city or fallback_city))
    for stop, city in hotel_names:
        try:
            found = await providers.lookup_place(stop.name, city)
        except Exception:  # noqa: BLE001 - a hotel lookup failure falls back to the provider
            found = []
        # A fuzzy lookup for "Hotel Mitsuwaya Osaka" can return a restaurant
        # called "Mitsuwaya". Only treat a result as a stay when it looks like
        # one; otherwise fall through to the proper hotel provider below.
        hotel_evidence = next(
            (
                item
                for item in found
                if item.source_refs
                and item.kind == "hotel"
                and any(
                    word in (item.name or "").lower()
                    for word in ("hotel", "hostel", "inn", "ryokan", "stay", "resort")
                )
            ),
            None,
        )
        if hotel_evidence is not None:
            hotel_evidence.kind = "hotel"
            hotel_evidence.ref = "im-hotel"
            bundle.add([hotel_evidence], "import")
            break
    if hotel_evidence is None:
        try:
            stays = await providers.hotels(fallback_city, limit=4) if fallback_city else []
        except Exception:  # noqa: BLE001
            stays = []
        if stays:
            hotel_evidence = stays[0]
            hotel_evidence.ref = "im-hotel"
            bundle.add([hotel_evidence], "import")

    # Weather is per city. The first city with a forecast wins each date; one
    # unavailable city must not hide the others.
    weather: dict[str, object] = {}
    cities = list(dict.fromkeys([city for _, _, city in candidates if city][:4]))
    for city in cities:
        try:
            meta = await providers.locate(city)
            forecast = await providers.weather(city, meta.get("geo"), start, end)
        except Exception:  # noqa: BLE001
            continue
        for row in forecast:
            weather.setdefault(row.date, row)

    draft_days: list[DayChoice] = []
    for day_index, day in enumerate(days):
        day_date = start + timedelta(days=day_index)
        if day_date > end:
            break
        stops: list[StopChoice] = []
        for position, stop in enumerate(list(day.stops)[:8]):
            index = next(
                (i for i, (candidate_day, candidate, _) in enumerate(candidates) if candidate_day == day_index and candidate is stop),
                None,
            )
            if index is None or index not in resolved:
                continue
            evidence = resolved[index]
            start_time = _time(stop.start, DEFAULT_TIMES[min(position, len(DEFAULT_TIMES) - 1)])
            stops.append(
                StopChoice(
                    ref=evidence.ref,
                    start=start_time,
                    end=_time(stop.end, ""),
                    meal_slot="dinner" if evidence.kind == "meal" and start_time >= "18:00" else ("lunch" if evidence.kind == "meal" else None),
                )
            )
        draft_days.append(
            DayChoice(
                date=day_date.isoformat(),
                title=day.title or (day.city or fallback_city),
                stops=stops,
            )
        )

    draft = PlanDraft(
        summary=itinerary.summary or f"A {len(draft_days)}-day itinerary imported from the image.",
        days=draft_days,
        hotel_ref=hotel_evidence.ref if hotel_evidence else None,
        ticket_refs=[],
        confidence=0.75,
    )
    plan = build_plan(
        context=context,
        bundle=bundle,
        draft=draft,
        disruptions=[],
        weather_by_date=weather,
        start=start,
        end=end,
        settings=settings,
    )
    problems = validate_structure(plan, context)
    empty_days = sum(1 for problem in problems if getattr(problem, "code", "") == "empty_day")
    if plan.item_count() < max(3, int(len(candidates) * 0.35)) or empty_days > max(2, len(draft_days) // 3):
        if trace:
            trace.say(
                f"Import only sourced {plan.item_count()} of {len(candidates)} named stops; "
                "using the normal planner instead."
            )
        return None
    return ImportedPlan(plan=plan, bundle=bundle, sourced=plan.item_count(), total=len(candidates))
