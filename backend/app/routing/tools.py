"""Allowlisted lookups for a turn. Missing keys and failed calls return nothing; they never invent a price."""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

import httpx

from app.routing.models import TripSnapshot
from app.routing.toolbox import Toolbox

logger = logging.getLogger(__name__)


def covered_by_plan(message: str, names: list[str]) -> bool:
    lower = (message or "").lower()
    return any(len(name) >= 3 and name.lower() in lower for name in names)


async def tool_notes(
    path: str,
    snapshot: TripSnapshot,
    message: str,
    parts: list[str],
    *,
    names: list[str] | None = None,
) -> str:
    """One short block for the writer. Empty when this path has nothing it is allowed to call."""
    if path == "ask" and covered_by_plan(message, names or []):
        return ""
    box = Toolbox(path, max_calls=3 if path != "plan" else 4)
    jobs = _jobs(path, snapshot, message, parts)
    lines: list[str] = []
    for tool, factory in jobs:
        try:
            value = await box.call(tool, factory)
        except Exception:
            continue
        text = (value or {}).get("text") if isinstance(value, dict) else None
        if text:
            lines.append(text)
    if not lines:
        return ""
    return "LOOKED UP (use only these; do not invent prices or times beyond them):\n" + "\n".join(f"- {line}" for line in lines)


def _jobs(path: str, snapshot: TripSnapshot, message: str, parts: list[str]):
    lower = message.lower()
    dest = (snapshot.destination or "").strip()
    jobs = []
    if path == "ask":
        if re.search(r"\b(weather|rain|forecast|typhoon)\b", lower) and dest:
            jobs.append(("weather.forecast", lambda: _weather(snapshot)))
        elif re.search(r"\b(how (long|far)|route|get to|directions)\b", lower):
            jobs.append(("routes.leg", lambda: _route_note(snapshot)))
        else:
            jobs.append(("web.search", lambda: _search(message, dest)))
        return jobs
    if path == "modify":
        if "hotel" in parts and dest:
            jobs.append(("hotels.search", lambda: _hotels(dest)))
        if "ticket" in parts and dest:
            jobs.append(("flights.search", lambda: _flights(snapshot.origin, dest)))
        if "attraction" in parts and dest:
            jobs.append(("places.search", lambda: _search(f"{dest} {message}", dest)))
        if "weather" in parts and dest:
            jobs.append(("weather.forecast", lambda: _weather(snapshot)))
        if "budget" in parts and snapshot.currency:
            jobs.append(("fx.rates", lambda: _fx(snapshot.currency or "")))
        return jobs
    if path == "plan" and dest:
        jobs.append(("hotels.search", lambda: _hotels(dest)))
        if snapshot.origin:
            jobs.append(("flights.search", lambda: _flights(snapshot.origin, dest)))
        if snapshot.currency:
            jobs.append(("fx.rates", lambda: _fx(snapshot.currency)))
    return jobs


async def _search(query: str, destination: str) -> dict | None:
    from app.core.config import settings
    if not settings.TAVILY_API_KEY:
        return None
    from app.agents.trip_planner.planning.tools.web_search import search_web
    rows = await search_web(query[:180], max_results=3)
    if not rows:
        return None
    titles = "; ".join(row.get("title") or "" for row in rows[:3] if row.get("title"))
    return {"text": f"Web references for {destination or 'the trip'}: {titles}"} if titles else None


async def _hotels(destination: str) -> dict | None:
    from app.core.config import settings
    if not settings.SERPAPI_KEY or not settings.ENABLE_HOTELS:
        return None
    data = await _serp({"engine": "google_hotels", "q": f"hotels in {destination}", "hl": "en", "gl": "us"})
    props = (data or {}).get("properties") or []
    lines = []
    for prop in props[:3]:
        if not isinstance(prop, dict) or not prop.get("name"):
            continue
        rate = (prop.get("rate_per_night") or {}).get("lowest") if isinstance(prop.get("rate_per_night"), dict) else ""
        lines.append(f"{prop['name']}" + (f" from {rate}" if rate else ""))
    return {"text": "Hotel references (SerpApi, check before booking): " + "; ".join(lines)} if lines else None


async def _flights(origin: str | None, destination: str) -> dict | None:
    from app.core.config import settings
    if not settings.SERPAPI_KEY or not settings.ENABLE_FLIGHTS or not origin:
        return None
    data = await _serp({
        "engine": "google_flights", "departure_id": origin, "arrival_id": destination,
        "hl": "en", "currency": "USD",
    })
    flights = (data or {}).get("best_flights") or (data or {}).get("other_flights") or []
    if not flights:
        return None
    first = flights[0] if isinstance(flights[0], dict) else {}
    price = first.get("price")
    return {"text": f"Flight reference {origin} to {destination}" + (f": about {price}" if price else "") + " (SerpApi, not a booking)."}


async def _serp(params: dict[str, Any]) -> dict | None:
    from app.core.config import settings
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get("https://serpapi.com/search.json", params={**params, "api_key": settings.SERPAPI_KEY})
            response.raise_for_status()
            body = response.json()
    except Exception as exc:
        logger.info("Lookup failed: %s", exc)
        return None
    return body if isinstance(body, dict) else None


async def _weather(snapshot: TripSnapshot) -> dict | None:
    if not snapshot.destination or not snapshot.start_date or not snapshot.duration_days:
        return None
    try:
        start = date.fromisoformat(snapshot.start_date)
    except ValueError:
        return None
    from app.services.enrichment import draft_conditions
    data = await draft_conditions(snapshot.destination, start, snapshot.duration_days, snapshot.currency or "USD")
    forecast = (data or {}).get("forecast") or []
    if not forecast:
        return None
    bits = [f"day {row.get('day')}: {row.get('sky') or 'forecast'} {row.get('temp_c') or ''}".strip() for row in forecast[:4]]
    return {"text": "Forecast: " + "; ".join(bits)}


async def _fx(currency: str) -> dict | None:
    code = (currency or "").upper()
    if len(code) != 3:
        return None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.get("https://api.frankfurter.dev/v1/latest", params={"from": code, "to": "USD,EUR,JPY"})
            response.raise_for_status()
            body = response.json()
    except Exception as exc:
        logger.info("Exchange rates unavailable: %s", exc)
        return None
    rates = body.get("rates") if isinstance(body, dict) else None
    if not isinstance(rates, dict) or not rates:
        return None
    shown = ", ".join(f"1 {code} = {rate} {name}" for name, rate in list(rates.items())[:3])
    return {"text": f"Exchange reference ({body.get('date') or 'latest'}): {shown}"}


async def _route_note(snapshot: TripSnapshot) -> dict | None:
    if not snapshot.origin or not snapshot.destination:
        return None
    return {"text": f"No timetable was looked up for {snapshot.origin} to {snapshot.destination}. Say so if you mention the journey."}
