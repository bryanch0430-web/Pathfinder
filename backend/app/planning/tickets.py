"""The opening and closing tickets of a trip.

The outbound flight is the start and the return flight is the end. Each one
carries a Google Flights search for those dates. The traveler books it;
nothing here is reserved.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import quote

_FLIGHT = re.compile(r"\b(flight|fly)\b", re.I)


def flight_search_url(origin: str, destination: str, when: date) -> str:
    # "One way" plus a city opens a bookable list. A country such as "Japan" opens an explore page.
    query = f"One way flights from {origin} to {destination} on {when.isoformat()}"
    return "https://www.google.com/travel/flights?q=" + quote(query)


def _ticket(origin: str, destination: str, when: date, *, home: bool) -> dict:
    url = flight_search_url(origin, destination, when)
    return {
        "name": f"Flight {origin} → {destination}",
        "category": "transit",
        "source_url": url,
        "tip": "Return ticket. Book this yourself on Google Flights. Nothing is reserved."
        if home else "Outbound ticket. Book this yourself on Google Flights. Nothing is reserved.",
    }


def _city(value: str) -> str:
    return re.split(r"\s*[—(]", value or "", maxsplit=1)[0].strip()


def _ends(days: list[dict], home: str, away: str) -> tuple[str, str]:
    """Arrival city for the outbound flight, and the city the return flight leaves."""
    skip = {home.casefold(), "departure", "arrival"}
    found = []
    for day in days:
        city = _city(str(day.get("base") or ""))
        if city and city.casefold() not in skip:
            found.append(city)
    specific = [city for city in found if city.casefold() != away.casefold()]
    pool = specific or found or [away]
    return pool[0], pool[-1]


def _stamp(item: dict, ticket: dict) -> None:
    item["source_url"] = ticket["source_url"]
    item["tip"] = item.get("tip") or ticket["tip"]
    item["category"] = item.get("category") or "transit"
    name = str(item.get("name") or "")
    if name.startswith("Flight ") and "→" in name:
        item["name"] = ticket["name"]


def attach_trip_tickets(
    days: list[dict], *, origin: str | None, destination: str | None, start: date | None, duration_days: int | None,
) -> tuple[list[dict], str]:
    """Put the outbound ticket on day 1 and the return ticket on the last day.

    The second value is a short reminder with the two booking links, or empty when
    there is no trip to fly.
    """
    home = (origin or "").strip()
    away = (destination or "").strip()
    if not days or not home or not away or not start or home.casefold() == away.casefold():
        return days, ""
    end = start + timedelta(days=max((duration_days or len(days)) - 1, 0))
    copied = [{**day, "items": [dict(item) for item in day.get("items") or []]} for day in days]
    arrive, leave = _ends(copied, home, away)
    outbound = _ticket(home, arrive, start, home=False)
    inbound = _ticket(leave, home, end, home=True)

    first = copied[0]["items"]
    opening = next((item for item in first if _FLIGHT.search(str(item.get("name") or ""))), None)
    if opening:
        _stamp(opening, outbound)
    else:
        first.insert(0, outbound)

    last = copied[-1]["items"]
    closing = next((item for item in reversed(last) if _FLIGHT.search(str(item.get("name") or "")) and item is not opening), None)
    if closing:
        _stamp(closing, inbound)
    else:
        last.append(inbound)

    reminder = (
        f"Ticket outbound: [{home} → {arrive} on {start.strftime('%d %b')}]({outbound['source_url']})\n\n"
        f"Ticket inbound: [{leave} → {home} on {end.strftime('%d %b')}]({inbound['source_url']})\n\n"
        "Nothing is reserved."
    )
    return copied, reminder
