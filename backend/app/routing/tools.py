"""Allowlisted lookups for a turn. Missing keys and failed calls return nothing; they never invent a price."""

from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from typing import Any

import httpx

from app.routing.models import TripSnapshot
from app.routing.toolbox import Toolbox

logger = logging.getLogger(__name__)
_QUOTE = re.compile(r"run out of searches|out of searches|quota|invalid api key", re.I)


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


def serp_keys() -> list[str]:
    """The primary search key, then ready keys from the pool. Paused and exhausted keys stay out."""
    from app.core.config import settings
    found: list[str] = []
    primary = (settings.SERPAPI_KEY or "").strip()
    if primary:
        found.append(primary)
    for chunk in (settings.SERPAPI_KEY_POOL or "").split(";"):
        parts = [part.strip() for part in chunk.split("|")]
        if len(parts) < 3 or parts[0].lower() not in {"active", "ready"}:
            continue
        key = parts[-1]
        if key and key not in found:
            found.append(key)
    return found


def _sample_stay() -> tuple[date, date]:
    start = date.today() + timedelta(days=21)
    return start, start + timedelta(days=1)


async def hotel_listings(destination: str) -> list[dict]:
    """Named hotels from Google Hotels, for the stay list. Empty when the search key is off."""
    from app.core.config import settings
    if not serp_keys() or not settings.ENABLE_HOTELS or not (destination or "").strip():
        return []
    check_in, check_out = _sample_stay()
    data = await _serp({
        "engine": "google_hotels", "q": f"hotels in {destination}", "hl": "en", "gl": "us",
        "check_in_date": check_in.isoformat(), "check_out_date": check_out.isoformat(),
        "adults": 2, "currency": "HKD",
    }, timeout=20)
    found = []
    for prop in (data or {}).get("properties") or []:
        if not isinstance(prop, dict) or not prop.get("name"):
            continue
        rate = (prop.get("rate_per_night") or {}).get("lowest") if isinstance(prop.get("rate_per_night"), dict) else ""
        gps = prop.get("gps_coordinates") if isinstance(prop.get("gps_coordinates"), dict) else {}
        lat, lon = gps.get("latitude"), gps.get("longitude")
        name = str(prop["name"]).strip()
        found.append({
            "id": f"hotel:{name.casefold()}",
            "name": name,
            "alt": None,
            "kind": "sleep",
            "tab": "stay",
            "blurb": f"Sample nightly rate {rate}. This is not a booking." if rate else None,
            "price": rate or None,
            "hours": None,
            "address": None,
            "website": prop.get("link") or None,
            "lat": float(lat) if isinstance(lat, (int, float)) else None,
            "lon": float(lon) if isinstance(lon, (int, float)) else None,
            "distance_km": None,
            "image": None,
            "image_credit": None,
            "source": "Google Hotels",
            "source_url": prop.get("link") or None,
            "wikipedia_url": None,
        })
        if len(found) >= 8:
            break
    return found


async def _hotels(destination: str) -> dict | None:
    from app.core.config import settings
    if not serp_keys() or not settings.ENABLE_HOTELS:
        return None
    check_in, check_out = _sample_stay()
    data = await _serp({
        "engine": "google_hotels", "q": f"hotels in {destination}", "hl": "en", "gl": "us",
        "check_in_date": check_in.isoformat(), "check_out_date": check_out.isoformat(),
        "adults": 2, "currency": "HKD",
    }, timeout=20)
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
    if not serp_keys() or not settings.ENABLE_FLIGHTS or not origin:
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


async def web_snippets(query: str) -> list[dict]:
    """A few Google results for a factual question. Titles and snippets only."""
    text = (query or "").strip()
    if not text:
        return []
    data = await _serp({"engine": "google", "q": text[:180], "hl": "en", "num": 4})
    if not data:
        return []
    rows: list[dict] = []
    box = data.get("answer_box") if isinstance(data.get("answer_box"), dict) else {}
    snippet = box.get("answer") or box.get("snippet") or box.get("result")
    if isinstance(snippet, str) and snippet.strip():
        rows.append({"title": str(box.get("title") or "Search answer"), "text": snippet.strip()[:400], "url": box.get("link") or ""})
    graph = data.get("knowledge_graph") if isinstance(data.get("knowledge_graph"), dict) else {}
    from app.routing.facts import plain_text
    bits = [plain_text(graph.get(key)) for key in ("hours", "description") if graph.get(key)]
    bits = [bit for bit in bits if bit]
    if bits:
        rows.append({"title": str(graph.get("title") or "Place"), "text": " ".join(bits)[:500], "url": graph.get("website") or ""})
    for item in data.get("organic_results") or []:
        if not isinstance(item, dict) or not item.get("snippet"):
            continue
        rows.append({
            "title": str(item.get("title") or "Result"),
            "text": str(item["snippet"])[:400],
            "url": item.get("link") or "",
        })
        if len(rows) >= 4:
            break
    return rows[:4]


async def _serp(params: dict[str, Any], timeout: float = 8.0) -> dict | None:
    keys = serp_keys()
    if not keys:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for key in keys:
                response = await client.get("https://serpapi.com/search.json", params={**params, "api_key": key})
                if response.status_code in {401, 403, 429}:
                    logger.info("Search key declined (%s); trying the next one.", response.status_code)
                    continue
                response.raise_for_status()
                body = response.json()
                if isinstance(body, dict) and _QUOTE.search(str(body.get("error") or "")):
                    logger.info("Search key is out of quota; trying the next one.")
                    continue
                return body if isinstance(body, dict) else None
    except Exception as exc:
        logger.info("Lookup failed: %s", type(exc).__name__)
        return None
    return None


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


def hotels_from_wikitext(wikitext: str) -> list[str]:
    """Names under a Wikivoyage Sleep heading. Empty when that section is missing."""
    match = re.search(r"==\s*Sleep\s*==\s*(.*?)(?:\n==[^=]|\Z)", wikitext or "", re.S | re.I)
    if not match:
        return []
    names: list[str] = []
    for line in match.group(1).splitlines():
        found = re.search(r"\[\[([^|\]]+)", line)
        if not found:
            continue
        name = found.group(1).strip()
        if not name or name.lower().startswith("wikivoyage") or name.lower() == "sleep":
            continue
        if name not in names:
            names.append(name)
        if len(names) == 3:
            break
    return names


def draft_lookups_enabled() -> bool:
    """Tests stay offline unless a test opts in. A normal run may call Wikivoyage."""
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("PATHFINDER_DRAFT_LOOKUPS") != "1":
        return False
    return True


async def _wikivoyage_hotel_names(destination: str) -> list[str]:
    page = destination.strip().replace(" ", "_")
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.get(
                "https://en.wikivoyage.org/w/api.php",
                params={"action": "parse", "page": page, "prop": "wikitext", "format": "json", "redirects": 1},
                headers={"User-Agent": "Pathfinder/1.0 (university project)"},
            )
            response.raise_for_status()
            text = ((response.json().get("parse") or {}).get("wikitext") or {}).get("*") or ""
    except Exception as exc:
        logger.info("Wikivoyage hotels unavailable: %s", exc)
        return []
    return hotels_from_wikitext(text)


async def hotel_context(destination: str) -> str:
    """A few real hotel names for a draft. SerpApi when keyed, otherwise Wikivoyage. Never a price we did not fetch."""
    if not draft_lookups_enabled() or not (destination or "").strip():
        return ""
    live = await _hotels(destination)
    if live and live.get("text"):
        return str(live["text"])
    names = await _wikivoyage_hotel_names(destination)
    if not names:
        return ""
    return "Hotel references (Wikivoyage, check before booking): " + "; ".join(names)
