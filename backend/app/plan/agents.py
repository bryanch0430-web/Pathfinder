"""The evidence workers.

Four fixed workers gather evidence concurrently: attractions-and-food, hotels,
weather and tickets. Each is a tool adapter behind a typed result; one source
failing never stops the others. They are plain code on purpose — the model's
judgement is used for *choosing and scheduling*, not for fetching facts.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.config import Settings
from app.core.dates import parse_date
from app.core.trace import TurnTrace
from app.domain.models import Disruption, GeoPoint, TripContext
from app.evidence.models import Bundle, Evidence
from app.evidence.providers import Providers

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class Gathered:
    bundle: Bundle
    disruptions: list[Disruption] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    avoid_transit_days: set[int] = field(default_factory=set)


def _stable_ref(kind: str, name: str) -> str:
    digest = hashlib.sha1(f"{kind}:{name.strip().lower()}".encode("utf-8")).hexdigest()[:8]
    return f"{kind[:2]}-{digest}"


async def gather(
    *,
    providers: Providers,
    settings: Settings,
    context: TripContext,
    trace: TurnTrace,
    emit: Emit,
    scenario: dict[str, Any] | None = None,
    with_tickets: bool = True,
) -> Gathered:
    destination = context.destination or ""
    if not destination:
        return Gathered(Bundle(), notes=["No destination, so no evidence could be gathered."])
    meta = await providers.locate(destination)
    city = meta.get("name") or destination
    geo: GeoPoint | None = meta.get("geo")
    start = parse_date(context.start_date) or parse_date(context.end_date)
    from datetime import date, timedelta

    start = start or date.today() + timedelta(days=30)
    days = int(context.days or 3)
    end = parse_date(context.end_date) or (start + timedelta(days=max(0, days - 1)))
    preferences = [*context.preferences, context.hotel_style]

    bundle = Bundle()
    disruptions: list[Disruption] = []
    notes: list[str] = []
    avoid_transit_days: set[int] = set()

    async def attraction_agent() -> None:
        started = time.perf_counter()
        await emit("agent", {"agent": "attraction", "phase": "start"})
        try:
            sights, food = await asyncio.gather(
                providers.attractions(city, limit=18, interests=preferences),
                providers.meals(city, limit=10),
            )
            for item in [*sights, *food]:
                item.ref = _stable_ref(item.kind, item.name)
            bundle.add(sights, "attractions")
            bundle.add(food, "meals")
            summary = f"{len(sights)} sights, {len(food)} places to eat"
            status = "ok" if sights else "unavailable"
            if not sights:
                bundle.gap("attractions", "no_results", f"No attraction source returned places for {city}.")
        except Exception as exc:  # noqa: BLE001 - a source failure is a gap, not a crash
            status, summary = "failed", "the attraction source failed"
            bundle.gap("attractions", "provider_error", f"The attraction source failed: {exc}")
        trace.tool(name="attractions", status=status, latency_ms=(time.perf_counter() - started) * 1000, note=summary)
        await emit(
            "agent",
            {
                "agent": "attraction",
                "phase": "end",
                "status": status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "summary": summary,
            },
        )

    async def hotel_agent() -> None:
        started = time.perf_counter()
        await emit("agent", {"agent": "hotel", "phase": "start"})
        try:
            stays = await providers.hotels(city, limit=6, style=context.hotel_style)
            for item in stays:
                item.ref = _stable_ref("hotel", item.name)
            bundle.add(stays, "hotels")
            status = "ok" if stays else "unavailable"
            summary = f"{len(stays)} stays"
            if not stays:
                bundle.gap("hotels", "no_results", f"No hotel source returned stays for {city}.")
        except Exception as exc:  # noqa: BLE001
            status, summary = "failed", "the hotel source failed"
            bundle.gap("hotels", "provider_error", f"The hotel source failed: {exc}")
        trace.tool(name="hotels", status=status, latency_ms=(time.perf_counter() - started) * 1000, note=summary)
        await emit(
            "agent",
            {
                "agent": "hotel",
                "phase": "end",
                "status": status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "summary": summary,
            },
        )

    async def weather_agent() -> None:
        started = time.perf_counter()
        await emit("agent", {"agent": "weather", "phase": "start"})
        try:
            days_weather = await providers.weather(city, geo, start, end, scenario=scenario)
            bundle.notes.append(f"weather:{len(days_weather)}")
            status = "ok" if any(day.status == "ok" for day in days_weather) else "partial"
            summary = "; ".join(f"{day.date}: {day.summary}" for day in days_weather[:3]) or "no forecast"
            if not any(day.status == "ok" for day in days_weather):
                bundle.gap("weather", "unavailable", "No forecast covered the trip dates.")
            bundle.weather = days_weather
        except Exception as exc:  # noqa: BLE001
            status, summary = "failed", "the weather source failed"
            bundle.gap("weather", "provider_error", f"The weather source failed: {exc}")
            bundle.weather = []
        trace.tool(name="weather", status=status, latency_ms=(time.perf_counter() - started) * 1000, note=summary)
        await emit(
            "agent",
            {
                "agent": "weather",
                "phase": "end",
                "status": status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "summary": summary,
            },
        )

    async def video_agent() -> None:
        started = time.perf_counter()
        await emit("agent", {"agent": "videos", "phase": "start"})
        try:
            notes = await providers.videos(f"{city} travel guide", limit=3)
            bundle.video_notes = notes
            status = "ok" if notes else "unavailable"
            summary = f"{len(notes)} video note(s)"
            if not notes:
                reason = getattr(providers, "last_video_error", "") or "No video source returned notes."
                bundle.gap("videos", "unavailable", reason)
        except Exception as exc:  # noqa: BLE001
            status, summary = "failed", "the video source failed"
            notes = []
        trace.tool(name="videos", status=status, latency_ms=(time.perf_counter() - started) * 1000, note=summary)
        await emit(
            "agent",
            {
                "agent": "videos",
                "phase": "end",
                "status": status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "summary": summary,
            },
        )

    async def ticket_agent() -> None:
        if not with_tickets:
            return
        started = time.perf_counter()
        await emit("agent", {"agent": "ticket", "phase": "start"})
        try:
            rides = await providers.tickets(
                context.origin, city, start, end, int(context.party_size or 1),
                currency=context.currency, scenario=scenario,
            )
            for item in rides:
                item.ref = _stable_ref("ticket", f"{context.origin}-{item.name}")
            bundle.add(rides, "tickets")
            status = "ok" if rides and all(ride.price_kind != "estimate" for ride in rides) else "partial"
            summary = f"{len(rides)} travel option(s)" + ("" if status == "ok" else ", partly estimated")
        except Exception as exc:  # noqa: BLE001
            status, summary = "failed", "the ticket source failed"
            bundle.gap("tickets", "provider_error", f"The ticket source failed: {exc}")
        trace.tool(name="tickets", status=status, latency_ms=(time.perf_counter() - started) * 1000, note=summary)
        await emit(
            "agent",
            {
                "agent": "ticket",
                "phase": "end",
                "status": status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "summary": summary,
            },
        )

    await asyncio.gather(attraction_agent(), hotel_agent(), weather_agent(), ticket_agent(), video_agent())
    bundle.dedupe()

    if scenario and scenario.get("fault") == "venue_closure":
        detail = scenario.get("fault_detail") or {}
        venue = str(detail.get("venue") or "").strip().lower()
        if venue:
            for item in bundle.items:
                if venue in item.name.lower() or item.name.lower() in venue:
                    item.data_status = "unavailable"
                    item.source_refs = [
                        *item.source_refs,
                        _notice_source(f"{detail.get('venue')} closure"),
                    ]
                    disruptions.append(
                        Disruption(
                            id=f"disruption-{len(disruptions) + 1}",
                            kind="venue_closure",
                            summary=str(detail.get("report") or f"{detail.get('venue')} is closed."),
                            affected_item_ids=[],
                            resolution="Reported closed; the planner replaced it with another sourced stop.",
                            resolved=False,
                        )
                    )
                    notes.append(f"{detail.get('venue')} is reported closed; it must not appear in the plan.")
                    break
            else:
                notes.append(f"{detail.get('venue')} is reported closed; avoid it.")
                disruptions.append(
                    Disruption(
                        id="disruption-1",
                        kind="venue_closure",
                        summary=str(detail.get("report") or f"{detail.get('venue')} is closed."),
                        resolution="Marked unavailable; the plan avoids it.",
                        resolved=True,
                    )
                )
    if scenario and scenario.get("fault") == "transit_delay":
        detail = scenario.get("fault_detail") or {}
        note = str(detail.get("report") or "Public transport is delayed on that day.")
        notes.append(
            f"Transit disruption: {note} Keep day 1 local and allow extra time between stops; prefer walking-distance clusters."
        )
        day = int((detail or {}).get("day") or 1)
        avoid_transit_days.add(day)
        notes.append(f"On day {day}, do not use the MTR: cluster stops and walk or take a taxi.")
        disruptions.append(
            Disruption(
                id="disruption-1",
                kind="transit_delay",
                summary=note,
                resolution=f"Day {day} avoids public transport; transfers are walking or taxi.",
                resolved=True,
            )
        )

    return Gathered(
        bundle=bundle,
        disruptions=disruptions,
        notes=notes,
        avoid_transit_days=avoid_transit_days,
    )


def _notice_source(title: str) -> Any:
    from app.domain.models import SourceRef, now_iso

    return SourceRef(title=title, url="", source="Disruption report", fetched_at=now_iso())


def candidate_pool(bundle: Bundle, *, attractions: int = 12, hotels: int = 5, meals: int = 8, tickets: int = 3) -> dict[str, list[dict[str, Any]]]:
    """The compact list the planner chooses from, capped so the prompt stays small."""

    def usable(items: list[Evidence], limit: int) -> list[dict[str, Any]]:
        ordered = sorted(
            (item for item in items if item.data_status != "unavailable"),
            key=lambda item: 0 if item.ref.split("-")[0] in {"pl", "x", "wiki"} else 1,
        )
        return [item.brief() for item in ordered][:limit]

    return {
        "attractions": usable(bundle.of_kind("attraction"), attractions),
        "meals": usable(bundle.of_kind("meal"), meals),
        "hotels": usable(bundle.of_kind("hotel"), hotels),
        "tickets": usable(bundle.of_kind("ticket"), tickets),
    }
