"""Facts a traveler already wrote, read from the words rather than a model.

A day count is never a party size. A budget symbol sets the currency. Place
names after "in" or "at" are stops. The focus of the sentence is the interests.
"""

from __future__ import annotations

import re

_DAYS = re.compile(r"\b(\d{1,3})\s*-\s*days?\b|\b(\d{1,3})\s+days?\b", re.I)
_TO = re.compile(r"\bto\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\b")
_IN = re.compile(r"\b(?:in|at)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\b")
_FROM = re.compile(r"\bfrom\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\b")
_FOCUS = re.compile(
    r"\b(?:focus(?:ing|ed)? on|interested in|including)\s+(.+?)(?:\s+with\s+(?:a\s+)?budget\b|[.]|$)",
    re.I | re.S,
)
_ADULTS = re.compile(r"\b(\d{1,2})\s+adults?\b|\bfor\s+(\d{1,2})\s+(?:people|travelers|travellers)\b", re.I)
_CHILDREN = re.compile(r"\b(\d{1,2})\s+(?:children|kids|child)\b", re.I)
_AVOID = re.compile(r"\b(?:avoid|no|without)\s+([^,.]{2,80})", re.I)
_MONEY = re.compile(
    r"(HK\$|US\$|A\$|C\$|S\$|NT\$|\$|£|€|¥)\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
    r"|\bbudget(?:\s+of)?\s+(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([A-Z]{3})\b",
    re.I,
)
_SYMBOLS = {
    "HK$": "HKD", "US$": "USD", "A$": "AUD", "C$": "CAD", "S$": "SGD", "NT$": "TWD",
    "$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY",
}
_COMFORT = (
    (re.compile(r"\bmid[-\s]?range\b", re.I), "mid_range"),
    (re.compile(r"\b(?:comfortable|luxury|nicer stays)\b", re.I), "comfortable"),
    (re.compile(r"\b(?:on a budget|budget travel|hostels)\b", re.I), "budget"),
)
_PACE = (
    (re.compile(r"\brelaxed\b", re.I), "relaxed"),
    (re.compile(r"\b(?:packed|full days)\b", re.I), "packed"),
)
_MODE = (
    (re.compile(r"\b(?:walk|walking)\b", re.I), "walk"),
    (re.compile(r"\b(?:taxi|rideshare)\b", re.I), "taxi"),
    (re.compile(r"\b(?:drive|rental car|driving)\b", re.I), "drive"),
    (re.compile(r"\b(?:public transit|transit|train|metro)\b", re.I), "transit"),
)


def _number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _first(match: re.Match[str] | None) -> str:
    if not match:
        return ""
    return next(group for group in match.groups() if group).strip()


def _unique(items: list[str], *, limit: int) -> list[str]:
    seen: list[str] = []
    for item in items:
        cleaned = " ".join(item.split()).strip(" .")
        if len(cleaned) < 2 or len(cleaned) > 80:
            continue
        if any(cleaned.casefold() == kept.casefold() for kept in seen):
            continue
        seen.append(cleaned)
        if len(seen) == limit:
            break
    return seen


def read_brief(text: str) -> dict:
    """Fields the sentence actually states. Missing fields are left out."""
    source = text or ""
    found: dict = {}
    day = _DAYS.search(source)
    if day:
        found["duration_days"] = int(_first(day))
    destination = _TO.search(source)
    if destination:
        found["destination"] = destination.group(1).strip()
    origin = _FROM.search(source)
    if origin:
        found["origin"] = origin.group(1).strip()
    places = [match.group(1).strip() for match in _IN.finditer(source)]
    if found.get("destination"):
        places = [place for place in places if place.casefold() != found["destination"].casefold()]
    focus = _FOCUS.search(source)
    interests: list[str] = []
    if focus:
        chunks = re.split(r",|\band\b", focus.group(1))
        for chunk in chunks:
            place = _IN.search(chunk)
            if place:
                places.append(place.group(1).strip())
            interest = _IN.sub("", chunk).strip(" ,")
            if interest:
                interests.append(interest)
    found["places_to_visit"] = _unique(places, limit=20)
    found["interests"] = _unique(interests, limit=6)
    avoids = [match.group(1).strip() for match in _AVOID.finditer(source)]
    found["avoid"] = _unique(avoids, limit=6)
    money = _MONEY.search(source)
    if money:
        symbol, amount, plain, code = money.groups()
        number = _number(amount or plain or "")
        if number and number > 0:
            found["budget_amount"] = number
            found["currency"] = _SYMBOLS.get(symbol or "", (code or "").upper()) or None
    adults = _ADULTS.search(source)
    if adults:
        found["adults"] = int(_first(adults))
    children = _CHILDREN.search(source)
    if children:
        found["children"] = int(_first(children))
    for pattern, value in _COMFORT:
        if pattern.search(source):
            found["comfort"] = value
            break
    for pattern, value in _PACE:
        if pattern.search(source):
            found["pace"] = value
            break
    for pattern, value in _MODE:
        if pattern.search(source):
            found["travel_mode"] = value
            break
    return found
