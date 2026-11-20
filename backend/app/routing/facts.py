"""Answers about weather, opening, and booking. They do not rewrite the plan.

A live forecast or a place listing is quoted. When a source does not say, the
reply says that. Pathfinder does not make a booking.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_WEATHER = re.compile(r"weather|forecast|rain|溫度|温度|天氣|天气|落雨", re.I)
_HOURS = re.compile(r"開唔開|开不开|開門|开门|opening|hours|營業|营业|schedule|幾時|几点|幾點|\bopen\b|\bclosed?\b", re.I)
_BOOK = re.compile(r"\bbook(?:ing|ings|ed)?\b|reserv|訂位|订位|預約|预约|洗唔洗", re.I)
_DAY = re.compile(r"\bday\s*(\d+)\b|第\s*(\d+)\s*[日天]", re.I)
_DATE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WEEKDAY_KEYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]+")


def fact_topics(message: str) -> set[str]:
    text = message or ""
    topics: set[str] = set()
    if _WEATHER.search(text):
        topics.add("weather")
    if _HOURS.search(text):
        topics.add("hours")
    if _BOOK.search(text):
        topics.add("booking")
    return topics


def asked_day(message: str, duration: int | None) -> int | None:
    match = _DAY.search(message or "")
    if not match:
        return None
    number = int(next(group for group in match.groups() if group))
    if number < 1 or (duration and number > duration):
        return None
    return number


def asked_calendar_date(message: str, today: date | None = None) -> date | None:
    match = _DATE.search(message or "")
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    # 1/1 is New Year either way. Other pairs use day/month, which is how these notes are written.
    if day <= 12 and month <= 12 and not (day == month):
        day, month = month, day
    year = (today or date.today()).year
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    if found < (today or date.today()):
        found = found.replace(year=year + 1)
    return found


_ACROSS = re.compile(
    r"\bwhich\b|from day\s*\d+|day\s*\d+\s*(?:to|through|-|–|—)\s*(?:day\s*)?\d+|邊[啲間]|哪些",
    re.I,
)
_SPAN = re.compile(r"day\s*(\d+)\s*(?:to|through|-|–|—)\s*(?:day\s*)?(\d+)", re.I)
_MEAL = {"food", "meal", "restaurant"}
_NOT_A_VENUE = re.compile(
    r"\b(breakfast|lunch|dinner|overnight|wander|crawl|stroll|rest|free window|check[- ]?in|check[- ]?out|street[- ]?food|your pick)\b",
    re.I,
)


def asks_across_days(message: str) -> bool:
    return bool(_ACROSS.search(message or ""))


def day_span(message: str, duration: int | None) -> tuple[int, int] | None:
    match = _SPAN.search(message or "")
    if match:
        start, end = int(match.group(1)), int(match.group(2))
    elif asks_across_days(message) and duration:
        start, end = 1, duration
    else:
        return None
    if start > end:
        start, end = end, start
    if start < 1:
        return None
    if duration:
        end = min(end, duration)
    return start, end


def venue_name(name: str) -> str | None:
    """A specific place inside a stop title. A wander or a bare meal word is not one."""
    text = (name or "").strip()
    found = re.search(r"\b(?:at|in)\s+(.+)$", text, re.I)
    venue = (found.group(1) if found else text).strip(" .—-")
    if not venue or _NOT_A_VENUE.search(venue):
        return None
    return venue


def named_meals(days: list[dict] | None, span: tuple[int, int]) -> list[tuple[int, str]]:
    start, end = span
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    for day in days or []:
        number = int(day.get("day") or 0)
        if number < start or number > end:
            continue
        for item in day.get("items") or []:
            if item.get("category") not in _MEAL and not re.search(r"restaurant|餐廳", str(item.get("name") or ""), re.I):
                continue
            venue = venue_name(str(item.get("name") or ""))
            if not venue or venue.casefold() in seen:
                continue
            seen.add(venue.casefold())
            found.append((number, venue))
    return found


def booking_roll_call(span: tuple[int, int], rows: list[tuple[int, str, bool | None]]) -> str:
    start, end = span
    if not rows:
        return (
            f"Days {start}–{end} don't name a specific restaurant. "
            "A wander or a meal slot isn't a place I can check. I don't book anything."
        )
    need, silent, unknown = [], [], []
    for day, venue, reservable in rows:
        line = f"Day {day}: {venue}"
        if reservable is True:
            need.append(line)
        elif reservable is False:
            silent.append(line)
        else:
            unknown.append(line)
    parts = [f"Days {start}–{end}. I checked the named restaurants on the plan. I don't make any booking."]
    if need:
        parts.append("The listing says these take reservations:\n" + "\n".join(f"- {line}" for line in need))
    if silent:
        parts.append("The listing does not show reservations for:\n" + "\n".join(f"- {line}" for line in silent))
    if unknown:
        parts.append("The listing doesn't say whether these need a booking:\n" + "\n".join(f"- {line}" for line in unknown))
    return "\n\n".join(parts)


async def bookings_on_plan(days: list[dict] | None, span: tuple[int, int], destination: str) -> str:
    import asyncio
    venues = named_meals(days, span)[:8]
    if not venues:
        return booking_roll_call(span, [])
    listings = await asyncio.gather(*(google_listing(venue, destination) for _, venue in venues))
    rows = []
    for (day, venue), listing in zip(venues, listings):
        flag = listing.get("reservable") if listing else None
        rows.append((day, venue, flag if isinstance(flag, bool) else None))
    return booking_roll_call(span, rows)


def focused_snippets(rows: list[dict], name: str | None) -> list[dict]:
    """Keep a snippet only when it is about this place and about hours or booking."""
    needle = (name or "").casefold()
    kept = []
    for row in rows:
        blob = f"{row.get('title') or ''} {row.get('text') or ''}"
        if needle and needle.casefold() not in blob.casefold():
            continue
        if not re.search(r"reserv|book|hour|open|closed|營業|订", blob, re.I):
            continue
        kept.append(row)
    return kept


def place_in_question(message: str, selected: str | None, items: list[dict] | None) -> str | None:
    text = message or ""
    folded = text.casefold()
    names = sorted(
        {str(item.get("name") or "").strip() for item in items or [] if str(item.get("name") or "").strip()},
        key=len,
        reverse=True,
    )
    for name in names:
        if len(name) >= 4 and name.casefold() in folded:
            return name
    if selected and selected.strip():
        return selected.strip()
    if asks_across_days(text):
        return None
    meals = [
        str(item.get("name") or "").strip()
        for item in items or []
        if item.get("category") in {"food", "meal", "restaurant"} and item.get("name")
    ]
    if meals and re.search(r"餐廳|餐厅|restaurant|我間|我间", message or "", re.I):
        return meals[0]
    return None


def _weather_line(row, when: date, day_number: int) -> str:
    temps = ""
    if row.temp_min is not None and row.temp_max is not None:
        temps = f", {row.temp_min:.0f}–{row.temp_max:.0f}°C"
    rain = f", about {row.rain_mm:.0f} mm of rain" if row.rain_mm else ""
    sky = row.condition or "no sky reading"
    kind = "Forecast" if row.kind == "forecast" else row.label or "Typical"
    return f"Day {day_number} ({when.strftime('%d %b')}): {kind}, {sky}{temps}{rain}."


async def weather_answer(destination: str | None, start: date | None, day_number: int, currency: str) -> str:
    if not destination:
        return "I need a destination before I can check the weather."
    if not start:
        return f"Set a start date and I can check day {day_number}'s weather."
    from app.services.enrichment import get_enrichment
    when = start + timedelta(days=day_number - 1)
    found = await get_enrichment(destination, start, [(day_number, destination, [])], currency or "HKD", timeout=8.0)
    row = next((item for item in (found.weather if found else []) if item.day == day_number), None)
    holidays = [item for item in (found.holidays if found else []) if item.day == day_number]
    if row is None:
        return f"I couldn't read the weather for day {day_number} ({when.strftime('%d %b')})."
    text = _weather_line(row, when, day_number)
    if row.kind != "forecast":
        note = row.notes[0].text if row.notes else "That date is outside the live forecast, so this is the typical weather for the month, not that exact day."
        text += f" {note}"
    else:
        text += " Source: Open-Meteo."
    if holidays:
        names = ", ".join(item.name for item in holidays)
        text += f" It is also a public holiday: {names}."
    return text


def listing_answer(name: str | None, listing: dict | None, when: date | None, topics: set[str]) -> str:
    if not name:
        return "Select the restaurant on the plan, or tell me its name, and I can check booking and opening hours."
    if not listing:
        parts = [f"I don't have a live listing for {name}."]
        if "booking" in topics:
            parts.append("I can't see whether it takes reservations, and I don't book it for you.")
        if "hours" in topics:
            parts.append("I can't see whether it is open on that date.")
        return " ".join(parts)
    lines = [name]
    if "booking" in topics:
        reservable = listing.get("reservable")
        if reservable is True:
            lines.append("The listing says it takes reservations. I don't make the booking.")
        elif reservable is False:
            lines.append("The listing does not show reservations. I don't book it for you.")
        else:
            lines.append("The listing doesn't say whether you need to book. I don't book it for you.")
    if "hours" in topics:
        hours = listing.get("hours") or []
        if when and hours:
            usual = hours[when.weekday()] if when.weekday() < len(hours) else ""
            lines.append(
                f"Usual hours for {_WEEKDAYS[when.weekday()]}: {usual}. "
                "A public holiday can differ from the usual hours, so check before you go."
            )
        elif hours:
            lines.append("Usual hours: " + "; ".join(hours) + ".")
        else:
            lines.append("The listing doesn't publish opening hours.")
    return " ".join(lines)


def _unescape(text: str) -> str:
    """Turn source escapes and odd spaces into the characters a person reads."""
    cleaned = text.replace("\\u202f", " ").replace("\\u00a0", " ").replace("\u202f", " ").replace("\xa0", " ")
    if "\\u" in cleaned:
        try:
            cleaned = cleaned.encode("utf-8").decode("unicode_escape")
        except UnicodeError:
            pass
    return cleaned.replace("\u202f", " ").replace("\xa0", " ")


def _structure(text: str):
    """A dict or list written as text. None when the text is an ordinary sentence."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    import ast
    try:
        value = ast.literal_eval(stripped)
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, (dict, list)) else None


def _label(key: str) -> str:
    words = _unescape(key).replace("_", " ")
    words = " ".join(words.split())
    lower = words.casefold()
    for day in _WEEKDAY_KEYS:
        if lower == day or lower.startswith(day + " "):
            rest = words[len(day):].strip(" :,-")
            name = day.capitalize()
            return f"{name} ({rest})" if rest else name
    return words[:1].upper() + words[1:] if words else ""


def _hours_line(data: dict) -> str | None:
    """Day to open–close, when every value is an opens/closes pair. Other objects stay structured."""
    rows: list[tuple[str, str]] = []
    for key, value in data.items():
        if not isinstance(value, dict) or not ({"opens", "closes"} & set(value)):
            return None
        opens = " ".join(_unescape(str(value.get("opens") or "")).split())
        closes = " ".join(_unescape(str(value.get("closes") or "")).split())
        span = f"{opens} – {closes}" if opens and closes else opens or closes
        if span:
            rows.append((_label(str(key)), span))
    if not rows:
        return None
    grouped: list[list] = []
    for label, span in rows:
        if grouped and grouped[-1][1] == span:
            grouped[-1][0].append(label)
        else:
            grouped.append([[label], span])
    parts = []
    for labels, span in grouped:
        name = labels[0] if len(labels) == 1 else f"{labels[0]}–{labels[-1]}"
        parts.append(f"{name}: {span}")
    return "; ".join(parts)


def plain_text(value) -> str:
    """A search field as a sentence. Dicts and dumped objects are read, not printed."""
    if value is None:
        return ""
    if isinstance(value, dict):
        hours = _hours_line(value)
        if hours:
            return hours
        bits = []
        for key, item in value.items():
            shown = plain_text(item)
            if not shown:
                continue
            label = _label(str(key))
            bits.append(f"{label}: {shown}" if label else shown)
        return ". ".join(bits)
    if isinstance(value, list):
        return "; ".join(part for part in (plain_text(item) for item in value) if part)
    text = _unescape(str(value))
    parsed = _structure(text)
    if parsed is not None:
        return plain_text(parsed)
    text = _EMOJI.sub(" ", text)
    return " ".join(text.split())


def web_fact_lines(rows: list[dict]) -> str:
    """Search snippets in plain sentences. Empty when nothing readable came back."""
    lines = []
    seen: set[str] = set()
    for row in rows:
        title = plain_text(row.get("title"))
        text = plain_text(row.get("text"))
        if not text:
            continue
        if title and title.casefold() in text.casefold():
            line = text
        elif title:
            line = f"{title}: {text}"
        else:
            line = text
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {line}")
    if not lines:
        return ""
    return "From a web search:\n" + "\n".join(lines[:4])


async def web_facts(query: str) -> list[dict]:
    """SerpApi Google results for a hours or booking question. No key, no results."""
    from app.routing.tools import web_snippets
    try:
        return await web_snippets(query)
    except Exception:
        return []


async def google_listing(name: str, destination: str) -> dict | None:
    """Reservable and weekday hours from Google Places, when a key is set."""
    if not name:
        return None
    from app.services.place_search import search_places
    rows = await search_places(name, kind="place", limit=1, destination=destination)
    row = next((item for item in rows if item.get("source") == "google"), None)
    if not row:
        return None
    return {"name": row["name"], "reservable": row.get("reservable"), "hours": row.get("hours") or []}
