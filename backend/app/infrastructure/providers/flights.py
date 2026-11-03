"""Flight discovery with SerpApi Google Flights.

Only airport codes with known coverage are queried. An unknown city returns a
typed no-data result rather than an invented flight.
"""

from __future__ import annotations

from datetime import date, datetime

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FlightCandidate, Money, PriceKind, SourceRef, utcnow
from app.infrastructure.http import request_json

AIRPORTS: dict[str, list[str]] = {
    "hong kong": ["HKG"],
    "hk": ["HKG"],
    "hong kong sar": ["HKG"],
    "tokyo": ["HND", "NRT"],
    "kyoto": ["KIX", "ITM"],
    "osaka": ["KIX", "ITM"],
    "japan": ["HND", "NRT"],
    "taipei": ["TPE", "TSA"],
    "taiwan": ["TPE"],
    "singapore": ["SIN"],
    "bangkok": ["BKK", "DMK"],
    "thailand": ["BKK"],
    "seoul": ["ICN", "GMP"],
    "korea": ["ICN"],
    "shanghai": ["PVG", "SHA"],
    "beijing": ["PEK", "PKX"],
    "guangzhou": ["CAN"],
    "shenzhen": ["SZX"],
    "london": ["LHR", "LGW"],
    "paris": ["CDG", "ORY"],
    "rome": ["FCO"],
    "madrid": ["MAD"],
    "barcelona": ["BCN"],
    "amsterdam": ["AMS"],
    "frankfurt": ["FRA"],
    "new york": ["JFK", "EWR"],
    "los angeles": ["LAX"],
    "san francisco": ["SFO"],
    "sydney": ["SYD"],
    "melbourne": ["MEL"],
    "dubai": ["DXB"],
    "istanbul": ["IST"],
    "bali": ["DPS"],
    "denpasar": ["DPS"],
    "hanoi": ["HAN"],
    "ho chi minh": ["SGN"],
    "manila": ["MNL"],
    "kuala lumpur": ["KUL"],
    "vancouver": ["YVR"],
    "toronto": ["YYZ"],
}


def resolve_airport(name: str) -> str | None:
    key = name.strip().lower()
    if key.upper() in {code for codes in AIRPORTS.values() for code in codes}:
        return key.upper()
    if key in AIRPORTS:
        return AIRPORTS[key][0]
    for city, codes in AIRPORTS.items():
        if city in key or key in city:
            return codes[0]
    return None


class SerpApiFlightSource:
    name = "serpapi-flights"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = bool(settings.serpapi_key)

    async def search(
        self,
        *,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: date | None,
        party_size: int,
        currency: str,
    ) -> list[FlightCandidate]:
        if not self.available:
            raise ProviderNoData("SerpApi key is not configured", source=self.name)
        departure_id = resolve_airport(origin)
        arrival_id = resolve_airport(destination)
        if not departure_id or not arrival_id:
            raise ProviderNoData(
                f"No airport code is known for {origin or 'origin'} / {destination}",
                source=self.name,
            )
        params = {
            "engine": "google_flights",
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": depart_date.isoformat(),
            "currency": currency,
            "hl": "en",
            "gl": "us",
            "adults": max(party_size, 1),
            "type": "1" if return_date else "2",
            "api_key": self.settings.serpapi_key,
        }
        if return_date:
            params["return_date"] = return_date.isoformat()
        data = await request_json(
            "GET",
            "https://serpapi.com/search.json",
            source=self.name,
            params=params,
            timeout=max(self.settings.http_timeout_seconds, 25),
            retries=self.settings.http_retries,
            user_agent=self.settings.user_agent,
        )
        if data.get("error"):
            raise ProviderError(f"{self.name} returned an error", source=self.name, detail=str(data["error"])[:300])
        itineraries = _itineraries(data)
        if not return_date:
            out = [
                candidate
                for candidate in (
                    _candidate(itinerary, departure_id, arrival_id, currency, depart_date)
                    for itinerary in itineraries
                )
                if candidate
            ]
            return out[:5]

        # Round trip is a two-step SerpApi flow: the first response returns the
        # outbound options plus a `departure_token`; a second request with that
        # token returns the inbound options and the combined fare. Skipping the
        # second step is why the plan used to show a departure and no way home.
        out: list[FlightCandidate] = []
        seen: set[tuple[str, str]] = set()
        for outbound in itineraries[:3]:
            token = outbound.get("departure_token")
            if not token:
                continue
            try:
                inbound_data = await request_json(
                    "GET",
                    "https://serpapi.com/search.json",
                    source=self.name,
                    params={**params, "departure_token": token},
                    timeout=max(self.settings.http_timeout_seconds, 25),
                    retries=self.settings.http_retries,
                    user_agent=self.settings.user_agent,
                )
            except ProviderError:
                continue
            inbound_options = _itineraries(inbound_data)
            if not inbound_options:
                continue
            candidate = _candidate(
                outbound,
                departure_id,
                arrival_id,
                currency,
                depart_date,
                return_itinerary=inbound_options[0],
            )
            if not candidate:
                continue
            key = (candidate.flight_number, candidate.return_flight_number)
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
            if len(out) >= 5:
                break
        if out:
            return out

        # Last resort: one-way rows (no inbound data available for this route).
        out = [
            candidate
            for candidate in (
                _candidate(itinerary, departure_id, arrival_id, currency, depart_date)
                for itinerary in itineraries
            )
            if candidate
        ]
        return out[:5]


def _itineraries(data: dict) -> list[dict]:
    return [
        row
        for bucket in ("best_flights", "other_flights")
        for row in (data.get(bucket) or [])
        if isinstance(row, dict)
    ]


def _candidate(
    itinerary: dict,
    departure_id: str,
    arrival_id: str,
    currency: str,
    depart_date: date,
    return_itinerary: dict | None = None,
) -> FlightCandidate | None:
    legs = itinerary.get("flights") or []
    if not legs:
        return None
    first, last = legs[0], legs[-1]
    dep = (first.get("departure_airport") or {})
    arr = (last.get("arrival_airport") or {})
    depart_at = _datetime(dep.get("time"))
    arrive_at = _datetime(arr.get("time"))
    if not depart_at or not arrive_at:
        return None
    if return_itinerary is not None:
        return_legs = return_itinerary.get("flights") or []
        price_value = return_itinerary.get("price", itinerary.get("price"))
    else:
        return_legs = itinerary.get("return_flights") or []
        price_value = itinerary.get("price")
    price = float(price_value) if isinstance(price_value, (int, float)) else None
    url = (
        "https://www.google.com/travel/flights?q="
        f"Flights%20from%20{departure_id}%20to%20{arrival_id}%20on%20{depart_date.isoformat()}"
    )

    # The inbound leg belongs to the same option and the same fare.
    return_origin = return_destination = ""
    return_depart_at = return_arrive_at = None
    return_airline = return_flight_number = ""
    return_duration = None
    if return_legs:
        first_return, last_return = return_legs[0], return_legs[-1]
        return_dep = first_return.get("departure_airport") or {}
        return_arr = last_return.get("arrival_airport") or {}
        return_origin = str(return_dep.get("id") or arrival_id)
        return_destination = str(return_arr.get("id") or departure_id)
        return_depart_at = _datetime(return_dep.get("time"))
        return_arrive_at = _datetime(return_arr.get("time"))
        return_airline = str(first_return.get("airline") or "")
        return_flight_number = str(first_return.get("flight_number") or "")
        durations = [int(leg.get("duration") or 0) for leg in return_legs]
        return_duration = sum(durations) or None

    return FlightCandidate(
        airline=str(first.get("airline") or ""),
        flight_number=str(first.get("flight_number") or ""),
        origin=str(dep.get("id") or departure_id),
        destination=str(arr.get("id") or arrival_id),
        depart_at=depart_at,
        arrive_at=arrive_at,
        duration_minutes=int(itinerary["total_duration"]) if itinerary.get("total_duration") else None,
        return_origin=return_origin,
        return_destination=return_destination,
        return_depart_at=return_depart_at,
        return_arrive_at=return_arrive_at,
        return_duration_minutes=return_duration,
        return_airline=return_airline,
        return_flight_number=return_flight_number,
        price=Money(amount=price, currency=currency) if price is not None else None,
        price_kind=PriceKind.REFERENCE if price is not None else PriceKind.NONE,
        booking_url=url,
        source_refs=[
            SourceRef(
                title=f"Google Flights: {departure_id} to {arrival_id}",
                url=url,
                source="serpapi-flights",
                fetched_at=utcnow(),
            )
        ],
    )


def _datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
