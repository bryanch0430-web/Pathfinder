"""Checks and evidence the planning graph can apply without a second router.

Workers, locks, flight windows, transfers, meals, a post-draft review, a far
stop, several cities, and a SerpApi key ring are ordinary functions. A node
calls them and writes the result onto planning state.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

KNOWN_PLACES = {
    "japan", "kyoto", "tokyo", "osaka", "nara", "hiroshima", "hong kong", "hk",
    "delhi", "taipei", "taiwan", "seoul", "korea", "london", "paris", "france",
    "india", "singapore", "bangkok", "thailand",
}
_NEW_TRIP = re.compile(r"\b(plan|trip|itinerary|days?)\b", re.I)
_DEICTIC = re.compile(r"\b(what is this|what's this|what is that|this|that|it|here)\b", re.I)
_PARK = re.compile(r"\b(park|hike|hiking|trail|theme park)\b", re.I)
_QUOTE = re.compile(r"run out of searches|out of searches|quota|invalid api key", re.I)


def fill_asked_slot(message: str, last_reply: str | None, *, destination: str | None, origin: str | None) -> dict[str, str]:
    """A short reply fills the slot the last question asked. A new trip sentence does not."""
    text = (message or "").strip()
    asked = last_reply or ""
    if not text or _NEW_TRIP.search(text) or len(text.split()) > 6:
        return {}
    if re.search(r"where you're travelling from|where you're leaving", asked, re.I) and not origin:
        return {"origin": text}
    if re.search(r"where you're going|which city", asked, re.I) and not destination:
        return {"destination": text}
    return {}


def map_hit(name: str) -> str | None:
    """A live lookup can replace this. The default keeps the typed name when no map is asked."""
    del name
    return None


def needs_place_confirmation(name: str | None, current: str | None) -> bool:
    text = (name or "").strip()
    if not text or text.casefold() in KNOWN_PLACES:
        return False
    if current and text.casefold() == current.casefold():
        return False
    return True


def place_decision(proposed: str, current: str | None, hit: str | None) -> tuple[str | None, str | None]:
    """Return the name to store, and a question when the map hit is not stored yet.

    A failed lookup keeps the typed name. A different hit waits for yes.
    """
    if not needs_place_confirmation(proposed, current):
        return proposed, None
    if not hit:
        return proposed, None
    if hit.casefold() == proposed.casefold():
        return proposed, None
    return current, f"I found {hit} for {proposed}. Is that the one?"


def selection_line(message: str, selected: str | None) -> str:
    name = (selected or "").strip()
    if not name or not _DEICTIC.search(message or ""):
        return ""
    return f"The traveller means the selected stop {name}."


def mask_for_model(message: str, selected: str | None = None) -> str:
    from app.security.pii import mask_pii
    masked = mask_pii(message or "")
    line = selection_line(message or "", selected)
    return f"{line}\n{masked}".strip() if line else masked


def flight_window(arrival: str | None, departure: str | None) -> dict[str, str]:
    """The first day opens two hours after landing. The last day closes 3.5 hours before departure."""
    window: dict[str, str] = {}
    if arrival:
        window["arrival"] = arrival
        window["day1_open"] = _shift(arrival, 120)
    if departure:
        window["last_close"] = _shift(departure, -210)
    return window


def apply_flight_window(days: list[dict], window: dict[str, str]) -> list[dict]:
    """A stop scheduled before landing moves to two hours after it. A stop that ends after the airport close is left out."""
    if not days or not window:
        return days
    copied = [dict(day, items=[dict(item) for item in day.get("items") or []]) for day in days]
    arrival = window.get("arrival")
    open_at = window.get("day1_open")
    close_at = window.get("last_close")
    if arrival and open_at and copied[0]["items"]:
        for item in copied[0]["items"]:
            if item.get("start") and _minutes(item["start"]) < _minutes(arrival):
                item["start"] = open_at
    if close_at and copied[-1]["items"]:
        copied[-1]["items"] = [
            item for item in copied[-1]["items"]
            if not item.get("end") or _minutes(item["end"]) <= _minutes(close_at)
        ]
    return copied


def airport_transfer(airport: str, stay: str) -> dict[str, str]:
    return {
        "kind": "transfer",
        "name": f"{airport} to {stay}",
        "mode": "drive",
        "note": "Estimated drive between the airport and the stay. It is not a booking.",
    }


def lock_lines(locks: list[dict] | None) -> str:
    rows = [row for row in locks or [] if row.get("name")]
    if not rows:
        return ""
    shown = "; ".join(f"{row.get('kind', 'stop')} {row['name']} on day {row.get('day', '?')}" for row in rows)
    return f"LOCKED: leave these as they are: {shown}.\n"


def restore_locks(before: list[dict], after: list[dict], locks: list[dict] | None) -> list[dict]:
    """Put a locked stop back if a revision renamed or dropped it."""
    wanted = {(int(row.get("day") or 0), str(row.get("name") or "").casefold()) for row in locks or [] if row.get("name")}
    if not wanted:
        return after
    by_day = {int(day.get("day") or 0): day for day in before}
    restored = []
    for day in after:
        number = int(day.get("day") or 0)
        items = [dict(item) for item in day.get("items") or []]
        names = {str(item.get("name") or "").casefold() for item in items}
        original = by_day.get(number) or {}
        for item in original.get("items") or []:
            key = (number, str(item.get("name") or "").casefold())
            if key in wanted and key[1] not in names:
                items.append(dict(item))
        restored.append({**day, "items": items})
    return restored


def worker_block(*, hotels: list[dict] | None = None, meals: list[dict] | None = None, tickets: list[dict] | None = None) -> str:
    """What the draft is allowed to use. A missing source is a gap, not a guess."""
    lines = ["EVIDENCE:"]
    if hotels:
        for row in hotels:
            bits = [f"{row.get('rank', '?')}. {row.get('name')}"]
            if row.get("url"):
                bits.append(row["url"])
            if row.get("picture"):
                bits.append(f"picture: {row['picture']}")
            if row.get("rate"):
                bits.append(f"rate {row['rate']}")
            lines.append("- hotel " + " · ".join(bits))
    else:
        lines.append("- hotels: no sourced stay. Do not invent a rate.")
    if meals:
        for meal in meals:
            backups = ", ".join(meal.get("backups") or []) or "none"
            lines.append(f"- meal {meal.get('slot')}: {meal.get('name')} (backups: {backups})")
    if tickets:
        for ticket in tickets:
            lines.append(f"- ticket {ticket.get('name')} · {ticket.get('source') or 'no source'}")
    else:
        lines.append("- tickets: no fare was returned. Do not invent a price.")
    return "\n".join(lines)


def visit_minutes(name: str, pace_minutes: int = 90) -> int:
    if _PARK.search(name or ""):
        return max(pace_minutes, 180)
    return pace_minutes


_GENERIC_STOP = re.compile(
    r"^(breakfast|lunch|dinner|overnight|rest|free window|check out|check-in|snack|wander)\b",
    re.I,
)


def review_days(days: list[dict], *, known: set[str], city: str) -> list[str]:
    """Internal checks. They are not pasted onto a draft: a first plan names places the brief did not list."""
    del known
    notes: list[str] = []
    seen: set[str] = set()
    city_folded = (city or "").casefold()
    for day in days:
        meals = [item for item in day.get("items") or [] if item.get("category") in {"meal", "food", "restaurant"}]
        if not meals:
            notes.append(f"Day {day.get('day')} has no meal.")
        for item in day.get("items") or []:
            name = str(item.get("name") or "")
            folded = name.casefold()
            if folded in seen and not _GENERIC_STOP.search(name):
                notes.append(f"{name} is repeated.")
            seen.add(folded)
            if item.get("price") and not item.get("source"):
                notes.append(f"{name} has a price with no source.")
            item_city = str(item.get("city") or "")
            if city_folded and item_city and item_city.casefold() != city_folded:
                notes.append(f"{name} is in {item_city}, not {city}.")
            if _PARK.search(name):
                minutes = visit_minutes(name)
                if int(item.get("minutes") or 0) < minutes:
                    notes.append(f"{name} needs about {minutes} minutes, not a short stop.")
    return notes


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def move_far_stops(days: list[dict], *, km: float = 8) -> list[str]:
    """Move a sight that sits far from the rest of its day onto a closer day with room."""
    notes: list[str] = []
    for day in days:
        items = day.get("items") or []
        located = [item for item in items if item.get("lat") is not None and item.get("category") != "meal"]
        if len(located) < 2:
            continue
        for item in list(located):
            others = [other for other in located if other is not item]
            here = (_km((item["lat"], item["lon"]), (other["lat"], other["lon"])) for other in others)
            distance = min(here)
            if distance <= km:
                continue
            for other_day in days:
                if other_day is day:
                    continue
                peers = [peer for peer in other_day.get("items") or [] if peer.get("lat") is not None]
                if not peers or len(other_day.get("items") or []) >= 6:
                    continue
                closer = min(_km((item["lat"], item["lon"]), (peer["lat"], peer["lon"])) for peer in peers)
                if distance - closer >= km:
                    items.remove(item)
                    other_day.setdefault("items", []).append(item)
                    notes.append(f"Moved {item.get('name')} from day {day.get('day')} to day {other_day.get('day')}.")
                    break
    return notes


def stitch_cities(plans: list[dict]) -> list[dict]:
    """One itinerary from a plan per city. Days are renumbered in route order."""
    days: list[dict] = []
    number = 1
    for plan in plans:
        for day in plan.get("days") or []:
            days.append({**day, "day": number, "city": plan.get("city") or day.get("city")})
            number += 1
    return days


def render_plan(markdown: str, language: str, names: dict[str, str] | None = None) -> str:
    """English stays the source. Transport words change. A place changes only when a source named it."""
    if language in {"en", "english", ""}:
        return markdown
    gloss = {
        "zh-Hant": {"Walk": "步行", "Transit": "港鐵", "Bus": "巴士", "Taxi": "的士", "Drive": "駕車"},
        "zh-Hans": {"Walk": "步行", "Transit": "地铁", "Bus": "公共汽车", "Taxi": "出租车", "Drive": "驾车"},
    }.get(language)
    if not gloss:
        return markdown
    text = markdown
    for english, translated in (names or {}).items():
        text = re.sub(rf"\b{re.escape(english)}\b", translated, text)
    for english, translated in gloss.items():
        text = re.sub(rf"\b{english}\b", translated, text)
    return text


def translate_request(message: str) -> str | None:
    text = message or ""
    if "简体" in text or "簡體" in text or re.search(r"\bsimplified\b", text, re.I):
        return "zh-Hans"
    if "繁體" in text or "繁体" in text or re.search(r"traditional|用中文|translate.*chinese", text, re.I):
        return "zh-Hant"
    return None


def move_stop(days: list[dict], *, day: int, name: str, start: str) -> list[dict]:
    """Drag one stop to a new clock time. Every other stop stays."""
    copied = [dict(row, items=[dict(item) for item in row.get("items") or []]) for row in days]
    for row in copied:
        if int(row.get("day") or 0) != int(day):
            continue
        for item in row["items"]:
            if str(item.get("name") or "").casefold() == name.casefold():
                item["start"] = start
                return copied
    raise ValueError(f"{name} is not on day {day}.")


@dataclass
class SerpApiRing:
    """One key is in use. A quota failure retires it and the next ready key is tried."""

    keys: list[str]
    index: int = 0
    exhausted: list[str] = field(default_factory=list)

    def current(self) -> str:
        if self.index >= len(self.keys):
            return ""
        return self.keys[self.index]

    def retire_if_quota(self, detail: str) -> str:
        if not _QUOTE.search(detail or ""):
            return self.current()
        used = self.current()
        if used:
            self.exhausted.append(used)
        self.index += 1
        return self.current()


def apply_plan_upgrades(days: list[dict], preferences: dict | None, *, known: set[str], city: str) -> tuple[list[dict], list[str]]:
    """Flight window, a far stop, and the post-draft notes. The graph's extract node owns the plan; this adjusts it."""
    prefs = preferences or {}
    flight = prefs.get("flight") if isinstance(prefs.get("flight"), dict) else {}
    window = flight_window(flight.get("arrival"), flight.get("departure"))
    updated = apply_flight_window(days, window)
    notes = move_far_stops(updated)
    return updated, notes


def _minutes(clock: str) -> int:
    hour, minute = [int(part) for part in clock.split(":", 1)]
    return hour * 60 + minute


def _shift(clock: str, minutes: int) -> str:
    hour, minute = [int(part) for part in clock.split(":", 1)]
    total = (hour * 60 + minute + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _hotel_rows(looked_up: str | None) -> list[dict] | None:
    rows = []
    for index, line in enumerate((looked_up or "").splitlines()):
        name = line.strip().lstrip("-").strip()
        if name:
            rows.append({"rank": index + 1, "name": name.split(":")[0].strip()})
    return rows or None


def evidence_from_result(planning_result: dict[str, Any] | None) -> str:
    result = planning_result or {}
    if result.get("workers"):
        return str(result["workers"])
    hotels = result.get("hotel_options")
    return worker_block(hotels=hotels, meals=result.get("meals"), tickets=result.get("tickets"))
