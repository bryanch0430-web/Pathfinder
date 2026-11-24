"""Travel evidence providers.

Each provider is a small adapter that turns one external answer into
:class:`~app.evidence.models.Evidence` or a typed gap.  The agents call them
concurrently and never let one failing source stop the others: a failed call
becomes a ``DataGap`` that the plan shows, which is the product rule ("mark
unavailable, do not fill with parametric knowledge").

``PATHFINDER_SANDBOX=1`` swaps every network call for the curated place corpus and a
deterministic weather/ticket model.  That makes offline demos and reproducible
evaluation run through the *same* pipeline as production.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import math
import random
import re
import socket
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import Settings
from app.core.trace import TurnTrace
from app.domain.models import DataGap, GeoPoint, ImageRef, Money, SourceRef, VideoNote, WeatherDay, now_iso
from app.evidence import place_corpus as corpus
from app.evidence.models import Evidence

IATA = {
    "hong kong": "HKG", "kyoto": "KIX", "osaka": "KIX", "tokyo": "NRT", "singapore": "SIN",
    "taipei": "TPE", "bangkok": "BKK", "seoul": "ICN", "new york": "JFK", "san francisco": "SFO",
    "paris": "CDG", "london": "LHR", "shanghai": "PVG", "beijing": "PEK", "sydney": "SYD",
    "shenzhen": "SZX", "guangzhou": "CAN", "macau": "MFM", "manila": "MNL", "kuala lumpur": "KUL",
    "hanoi": "HAN", "ho chi minh city": "SGN", "jakarta": "CGK", "bali": "DPS", "mumbai": "BOM",
}

FLIGHT_MINUTES = {
    ("HKG", "KIX"): 225, ("HKG", "NRT"): 250, ("HKG", "SIN"): 235, ("HKG", "TPE"): 100,
    ("HKG", "BKK"): 165, ("HKG", "ICN"): 210, ("HKG", "JFK"): 900, ("HKG", "SFO"): 750,
    ("LHR", "JFK"): 470, ("LHR", "SFO"): 660, ("SIN", "HKG"): 235, ("TPE", "HKG"): 100,
    ("KIX", "HKG"): 225, ("NRT", "HKG"): 250,
    ("SZX", "HKG"): 30, ("CAN", "HKG"): 45, ("MFM", "HKG"): 20, ("MNL", "HKG"): 130,
    ("KUL", "HKG"): 220, ("HAN", "HKG"): 110, ("SGN", "HKG"): 150, ("CGK", "HKG"): 260,
    ("DPS", "HKG"): 300, ("BOM", "HKG"): 350,
}

WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Rain showers", 82: "Violent rain showers",
    85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm", 96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}

# Units of each currency per 1 HKD, for estimate-only prices.
PER_HKD = {"HKD": 1.0, "JPY": 19.4, "USD": 0.128, "EUR": 0.118, "GBP": 0.101, "SGD": 0.172, "TWD": 4.1, "THB": 4.5, "KRW": 176.0, "CNY": 0.92}

PRICE_LEVEL = {"PRICE_LEVEL_INEXPENSIVE": "$", "PRICE_LEVEL_MODERATE": "$$", "PRICE_LEVEL_EXPENSIVE": "$$$", "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$"}


MAX_PAGE_BYTES = 2_000_000
MAX_PAGE_REDIRECTS = 3


def _ip_is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return False
    return ip.is_global


async def _resolve_host(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def url_is_public(url: str) -> bool:
    """True only for an http(s) URL on a default port whose host resolves solely to public addresses."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    if port not in (None, 80, 443):
        return False
    try:
        addresses = await _resolve_host(parts.hostname, port or (443 if parts.scheme == "https" else 80))
    except (OSError, UnicodeError):
        return False
    return bool(addresses) and all(_ip_is_public(address) for address in addresses)


def _peer_address(response: httpx.Response) -> str:
    try:
        stream = response.extensions.get("network_stream")
        server = stream.get_extra_info("server_addr") if stream is not None else None
        return str(server[0]) if server else ""
    except Exception:  # noqa: BLE001
        return ""


_ACRONYM = re.compile(r"\(([A-Z]{2,6})\)")


def _split_place_names(value: Any) -> list[str]:
    """One research field can name several places, joined by ";" or a newline."""
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = re.split(r"\s*(?:;|\n)\s*", str(value or ""))
    names: list[str] = []
    for part in parts:
        text = " ".join(part.split()).strip(" .;-")
        if len(text) >= 3 and text.lower() not in {name.lower() for name in names}:
            names.append(text[:160])
    return names[:6]


def _lookup_queries(value: Any) -> list[str]:
    """The searches to try for a research field.

    A model may join two places with "and" ("HKU (HKU) and HKUST (HKUST)"). Splitting
    on "and" alone is unsafe ("Science and Technology"), so when the text carries two
    bracketed acronyms each acronym becomes its own search, and the full phrases are
    kept as well.
    """
    queries: list[str] = []
    values = [str(item) for item in value] if isinstance(value, (list, tuple)) else [str(value or "")]
    for text in values:
        acronyms = _ACRONYM.findall(text)
        if len(acronyms) >= 2:
            queries.extend(acronyms)
        queries.extend(_split_place_names(text))
        if len(acronyms) == 1:
            queries.append(acronyms[0])
    unique: list[str] = []
    for query in queries:
        clean = " ".join(query.split()).strip()
        if clean and clean.lower() not in {item.lower() for item in unique}:
            unique.append(clean)
    return unique[:6]


class Providers:
    def __init__(
        self,
        settings: Settings,
        trace: TurnTrace | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        serp_keys: list[dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.trace = trace
        self._client = client
        self._owns_client = client is None
        self.last_video_error = ""
        self.sandbox = bool(settings.sandbox)
        self._serp_index = 0
        self.serp_keys = serp_keys or []

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                headers={"User-Agent": self.settings.wikimedia_user_agent},
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- destination ---------------------------------------------------

    async def locate(self, destination: str) -> dict[str, Any]:
        """The city's centre, country and timezone, from the place corpus or Google."""
        meta = corpus.city_meta(destination)
        if meta:
            return {
                "name": meta["name"],
                "country_code": meta["country_code"],
                "geo": GeoPoint(lat=meta["lat"], lng=meta["lng"]),
                "timezone": meta["timezone"],
            }
        if not self.sandbox and self.settings.google_places_api_key:
            hit = await self._google_place(f"{destination} city centre", max_results=1)
            if hit:
                first = hit[0]
                return {
                    "name": destination,
                    "country_code": (first.source_types or [""])[0],
                    "geo": first.geo,
                    "timezone": "UTC",
                }
        return {"name": destination, "country_code": "", "geo": None, "timezone": "UTC"}

    # -- places --------------------------------------------------------

    async def attractions(self, city: str, *, limit: int = 12, interests: list[str] | None = None) -> list[Evidence]:
        return await self._places(city, "attraction", limit=limit, interests=interests)

    async def hotels(self, city: str, *, limit: int = 6, style: str = "") -> list[Evidence]:
        if self.sandbox or not self.settings.google_places_api_key:
            entries = corpus.hotels(city, stale=not self.sandbox)
            return entries[:limit]
        query = f"hotels in {city}" + (f" {style}" if style else "")
        found = await self._google_place(query, max_results=limit, kinds=("lodging",))
        return found[:limit]

    async def meals(self, city: str, *, limit: int = 8) -> list[Evidence]:
        if self.sandbox or not self.settings.google_places_api_key:
            entries = corpus.meals(city, stale=not self.sandbox)
            return entries[:limit]
        queries = [f"best local restaurants in {city}", f"famous food in {city}"]
        batches = await asyncio.gather(
            *(self._google_place(query, max_results=max(4, limit // 2), kinds=("restaurant", "cafe")) for query in queries),
            return_exceptions=True,
        )
        found: list[Evidence] = []
        for batch in batches:
            if isinstance(batch, list):
                found.extend(batch)
        return found[:limit]

    async def _places(
        self, city: str, kind: str, *, limit: int, interests: list[str] | None = None
    ) -> list[Evidence]:
        if self.sandbox or not self.settings.google_places_api_key:
            entries = corpus.attractions(city, stale=not self.sandbox)
            return self._filter_interests(entries, interests)[:limit]
        queries = [f"top attractions in {city}", f"must see sights in {city}"]
        if interests:
            queries.append(f"{' '.join(interests[:3])} in {city}")
        batches = await asyncio.gather(
            *(self._google_place(query, max_results=max(5, limit // 2)) for query in queries),
            return_exceptions=True,
        )
        found: list[Evidence] = []
        for batch in batches:
            if isinstance(batch, list):
                found.extend(batch)
        deduped: dict[str, Evidence] = {}
        for item in found:
            deduped.setdefault(item.name.strip().lower(), item)
        # A named interest ("Tokyo Disneyland") can return a place in another
        # city. Do not put a Tokyo address into an Osaka plan.
        city_key = city.split(",")[0].strip().lower()
        in_city = [
            item for item in deduped.values()
            if not city_key or not item.address or city_key in item.address.lower()
        ]
        return in_city[:limit]

    @staticmethod
    def _filter_interests(entries: list[Evidence], interests: list[str] | None) -> list[Evidence]:
        if not interests:
            return entries
        words = {word.lower() for phrase in interests for word in str(phrase).split() if len(word) > 2}
        if not words:
            return entries
        scored = sorted(
            entries,
            key=lambda item: -sum(1 for tag in item.tags if tag.lower() in words or any(w in tag.lower() for w in words)),
        )
        return scored

    async def _google_place(
        self, query: str, *, max_results: int = 8, kinds: tuple[str, ...] = ()
    ) -> list[Evidence]:
        key = self.settings.google_places_api_key
        if not key:
            return []
        body: dict[str, Any] = {
            "textQuery": query,
            "maxResultCount": max(1, min(max_results, 20)),
            "languageCode": "en",
        }
        if kinds:
            body["includedType"] = kinds[0]
        headers = {
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,places.location,places.rating,"
                "places.userRatingCount,places.priceLevel,places.regularOpeningHours,places.editorialSummary,"
                "places.types,places.primaryTypeDisplayName,places.websiteUri,places.googleMapsUri,"
                "places.reservable,places.accessibilityOptions,places.photos,places.internationalPhoneNumber"
            ),
            "Content-Type": "application/json",
        }
        try:
            response = await self.client.post(
                "https://places.googleapis.com/v1/places:searchText",
                json=body,
                headers=headers,
                timeout=18.0,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            if self.trace:
                self.trace.tool(name="google_places", status="error", latency_ms=0, args={"q": query}, note=str(exc))
            return []
        out: list[Evidence] = []
        for index, place in enumerate(payload.get("places") or []):
            evidence = self._place_to_evidence(place, query, index)
            if evidence:
                out.append(evidence)
        return out

    def _place_to_evidence(self, place: dict[str, Any], query: str, index: int) -> Evidence | None:
        name = ((place.get("displayName") or {}).get("text") or "").strip()
        location = place.get("location") or {}
        lat, lng = location.get("latitude"), location.get("longitude")
        if not name or lat is None or lng is None:
            return None
        maps_url = place.get("googleMapsUri") or f"https://www.google.com/maps/search/?api=1&query={name.replace(' ', '+')}"
        types = [str(value) for value in (place.get("types") or [])]
        kind = "attraction"
        if "lodging" in types:
            kind = "hotel"
        elif any(value in types for value in ("restaurant", "cafe", "bar", "bakery", "food")):
            kind = "meal"
        hours = list((place.get("regularOpeningHours") or {}).get("weekdayDescriptions") or [])
        photo = None
        photos = place.get("photos") or []
        if photos and self.settings.google_places_api_key:
            photo_name = photos[0].get("name")
            if photo_name:
                photo = ImageRef(
                    url=(
                        f"https://places.googleapis.com/v1/{photo_name}/media"
                        f"?maxWidthPx=900&key={self.settings.google_places_api_key}"
                    ),
                    attribution="Google",
                    source="google",
                )
        price_level = PRICE_LEVEL.get(str(place.get("priceLevel") or ""))
        accessibility = place.get("accessibilityOptions") or {}
        summary = ((place.get("editorialSummary") or {}).get("text") or "").strip()
        cuisine = ((place.get("primaryTypeDisplayName") or {}).get("text") or "").strip()
        return Evidence(
            ref=f"g{index}",
            kind=kind,  # type: ignore[arg-type]
            name=name,
            address=place.get("formattedAddress") or "",
            geo=GeoPoint(lat=float(lat), lng=float(lng)),
            summary=summary or f"Found for '{query}'.",
            source_refs=[SourceRef(title=name, url=maps_url, source="Google Places", fetched_at=now_iso())],
            data_status="ok",
            fetched_at=now_iso(),
            rating=place.get("rating"),
            review_count=place.get("userRatingCount"),
            price_level=price_level,
            cuisine=cuisine if kind == "meal" else None,
            opening_hours=hours,
            hours_stated=bool(hours),
            reservable=place.get("reservable"),
            step_free=accessibility.get("wheelchairAccessibleEntrance"),
            price=None,
            price_kind="none",
            booking_url=place.get("websiteUri") or "",
            image=photo,
            tags=types[:8],
            source_types=types[:8],
            suggested_minutes=120 if kind == "attraction" else 75,
            area="",
        )

    # -- weather -------------------------------------------------------

    async def weather(
        self,
        city: str,
        geo: GeoPoint | None,
        start: date,
        end: date,
        *,
        scenario: dict[str, Any] | None = None,
    ) -> list[WeatherDay]:
        days = _date_range(start, end)
        typhoon_day = None
        if scenario and scenario.get("fault") == "typhoon":
            typhoon_day = (scenario.get("fault_detail") or {}).get("day")
        if self.sandbox or not geo:
            return [self._seasonal_weather(city, day, index + 1, typhoon_day) for index, day in enumerate(days)]
        if (start - date.today()).days > 15:
            # The forecast horizon is 16 days: say so instead of inventing a forecast.
            return [
                WeatherDay(
                    date=day.isoformat(),
                    status="unavailable",
                    summary="No forecast yet: the trip is beyond the 16-day forecast window.",
                    source="Open-Meteo",
                    fetched_at=now_iso(),
                    note="A forecast will be fetched closer to the trip.",
                )
                for day in days
            ]
        params = {
            "latitude": geo.lat,
            "longitude": geo.lng,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        try:
            response = await self.client.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=15.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return [
                WeatherDay(
                    date=day.isoformat(),
                    status="unavailable",
                    summary="Weather source did not answer.",
                    source="Open-Meteo",
                    fetched_at=now_iso(),
                )
                for day in days
            ]
        daily = payload.get("daily") or {}
        out: list[WeatherDay] = []
        for index, day_text in enumerate(daily.get("time") or []):
            code = _at(daily.get("weather_code"), index)
            out.append(
                WeatherDay(
                    date=day_text,
                    status="ok",
                    summary=WMO.get(int(code), "Forecast") if code is not None else "Forecast",
                    high_c=_at(daily.get("temperature_2m_max"), index),
                    low_c=_at(daily.get("temperature_2m_min"), index),
                    precipitation_probability=_at(daily.get("precipitation_probability_max"), index),
                    source="Open-Meteo",
                    fetched_at=now_iso(),
                )
            )
        return out

    def _seasonal_weather(self, city: str, day: date, day_number: int, typhoon_day: int | None = None) -> WeatherDay:
        meta = corpus.city_meta(city) or {}
        lat = float(meta.get("lat", 22.3))
        # A smooth seasonal curve from latitude: hot near the equator, cold at high latitude.
        base = 30.0 - abs(lat) * 0.42
        seasonal = 4.5 * math.cos((day.timetuple().tm_yday - 200) / 365.0 * 2 * math.pi)
        high = round(base + seasonal + random.Random(f"{city}{day.isoformat()}").uniform(-1.5, 1.5), 1)
        low = round(high - random.Random(f"{day.isoformat()}{city}").uniform(4, 7), 1)
        if typhoon_day is not None and day_number == typhoon_day:
            return WeatherDay(
                date=day.isoformat(),
                status="ok",
                summary="Typhoon Signal No. 8 expected",
                high_c=high - 3,
                low_c=low,
                precipitation_probability=95,
                source="Sandbox scenario",
                fetched_at=now_iso(),
                note="Outdoor stops are not advisable while the signal is up.",
            )
        return WeatherDay(
            date=day.isoformat(),
            status="ok",
            summary="Partly cloudy with a chance of showers",
            high_c=high,
            low_c=low,
            precipitation_probability=35,
            source="Sandbox forecast",
            fetched_at=now_iso(),
            note="Sandbox forecast (PATHFINDER_SANDBOX=1).",
        )

    # -- routes --------------------------------------------------------

    async def leg(self, a: GeoPoint, b: GeoPoint, mode: str = "walk") -> dict[str, Any]:
        straight = haversine_km(a, b)
        if self.sandbox or not self.settings.enable_routes:
            minutes = _minutes_for(straight, mode)
            return {"minutes": minutes, "km": round(straight * _detour(mode), 2), "mode": mode, "source": "estimate", "path": [[a.lat, a.lng], [b.lat, b.lng]]}
        profile = {"walk": "foot", "drive": "driving", "cycle": "bike", "transit": "driving"}.get(mode, "foot")
        url = f"https://router.project-osrm.org/route/v1/{profile}/{a.lng},{a.lat};{b.lng},{b.lat}"
        try:
            response = await self.client.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=10.0)
            response.raise_for_status()
            payload = response.json()
            route = (payload.get("routes") or [None])[0]
            if route:
                path = [[lat, lng] for lng, lat in (route.get("geometry") or {}).get("coordinates", [])]
                return {
                    "minutes": float(route.get("duration") or 0) / 60.0,
                    "km": float(route.get("distance") or 0) / 1000.0,
                    "mode": mode,
                    "source": "OSRM",
                    "path": path or [[a.lat, a.lng], [b.lat, b.lng]],
                }
        except (httpx.HTTPError, ValueError):
            pass
        return {
            "minutes": _minutes_for(straight, mode),
            "km": round(straight * _detour(mode), 2),
            "mode": mode,
            "source": "estimate",
            "path": [[a.lat, a.lng], [b.lat, b.lng]],
        }

    # -- tickets -------------------------------------------------------

    async def tickets(
        self,
        origin: str,
        destination: str,
        start: date,
        end: date,
        party: int,
        *,
        currency: str = "",
        scenario: dict[str, Any] | None = None,
    ) -> list[Evidence]:
        code = (currency or self.settings.default_currency or "HKD").upper()
        if _same_city(origin, destination):
            # A local trip needs no flight; a transfer belongs to the plan, not a fare.
            return []
        if self.sandbox or not (self.settings.enable_flights and self._serp_key()):
            return [self._estimated_flight(origin, destination, start, end, party, code)]
        origin_code = _iata(origin)
        destination_code = _iata(destination)
        if not (origin_code and destination_code):
            return [self._estimated_flight(origin, destination, start, end, party)]
        params = {
            "engine": "google_flights",
            "departure_id": origin_code,
            "arrival_id": destination_code,
            "outbound_date": start.isoformat(),
            "return_date": end.isoformat(),
            "currency": code,
            "adults": max(1, party),
            "hl": "en",
        }
        payload = await self._serp(params, purpose="flights")
        return_info: dict[str, Any] = {}
        if end > start:
            # The round-trip payload shows the outbound legs; ask once more for the return
            # so the plan can end the last day before the flight leaves.
            return_payload = await self._serp(
                {
                    "engine": "google_flights",
                    "departure_id": destination_code,
                    "arrival_id": origin_code,
                    "outbound_date": end.isoformat(),
                    "type": 2,
                    "currency": code,
                    "adults": max(1, party),
                    "hl": "en",
                },
                purpose="flights",
            )
            return_flights = (return_payload or {}).get("best_flights") or []
            if return_flights:
                return_legs = return_flights[0].get("flights") or []
                if return_legs:
                    first_return, last_return = return_legs[0], return_legs[-1]
                    return_info = {
                        "return_depart_at": _leg_time(first_return, "departure_airport"),
                        "return_arrive_at": _leg_time(last_return, "arrival_airport"),
                        "return_airline": first_return.get("airline") or "",
                        "return_flight_number": first_return.get("flight_number") or "",
                    }
        out: list[Evidence] = []
        for index, flight in enumerate((payload or {}).get("best_flights") or []):
            price = flight.get("price")
            if price is None:
                continue
            legs = flight.get("flights") or []
            if not legs:
                continue
            first, last = legs[0], legs[-1]
            airline = first.get("airline") or ""
            flight_number = first.get("flight_number") or ""
            depart_at = _leg_time(first, "departure_airport")
            arrive_at = _leg_time(last, "arrival_airport")
            outbound = f"{airline} {flight_number}".strip()
            if depart_at and arrive_at:
                outbound = f"{outbound} {depart_at.replace('T', ' ')} → {arrive_at.replace('T', ' ')}"
            summary = f"Outbound {outbound}."
            if return_info:
                summary += (
                    f" Return {return_info['return_airline']} {return_info['return_flight_number']}"
                    f" {str(return_info['return_depart_at']).replace('T', ' ')} →"
                    f" {str(return_info['return_arrive_at']).replace('T', ' ')}."
                )
            summary += f" {party} traveller(s)."
            out.append(
                Evidence(
                    ref=f"t{index + 1}",
                    kind="ticket",
                    name=f"{origin} to {destination} · {airline} {flight_number}".strip(),
                    summary=summary,
                    source_refs=[
                        SourceRef(
                            title=f"Google Flights {origin_code}-{destination_code}",
                            url=f"https://www.google.com/travel/flights?q=Flights%20from%20{origin_code}%20to%20{destination_code}",
                            source="Google Flights",
                            fetched_at=now_iso(),
                        )
                    ],
                    data_status="ok",
                    fetched_at=now_iso(),
                    price=Money(amount=float(price), currency=code),
                    price_kind="live",
                    booking_url=f"https://www.google.com/travel/flights?q=Flights%20from%20{origin_code}%20to%20{destination_code}",
                    tags=["flight"],
                    depart_at=depart_at,
                    arrive_at=arrive_at,
                    airline=airline,
                    flight_number=flight_number,
                    **return_info,
                )
            )
        return out or [self._estimated_flight(origin, destination, start, end, party, code)]

    def _estimated_flight(self, origin: str, destination: str, start: date, end: date, party: int, currency: str = "HKD") -> Evidence:
        minutes = _flight_minutes(origin, destination)
        price_hkd = (900 + minutes * 3.2) * max(1, party)
        price = round(price_hkd * PER_HKD.get(currency, 1.0), -1)
        return Evidence(
            ref="t1",
            kind="ticket",
            name=f"{origin} to {destination}",
            summary=f"Estimated return fare for {party}; no live fare was available, so this is a planning estimate.",
            source_refs=[
                SourceRef(
                    title=f"Estimate: {origin} to {destination}",
                    url="",
                    source="Pathfinder estimate",
                    fetched_at=now_iso(),
                )
            ],
            data_status="stale",
            fetched_at=now_iso(),
            price=Money(amount=float(price), currency=currency),
            price_kind="estimate",
            booking_url="",
            tags=["flight"],
        )

    # -- search --------------------------------------------------------

    async def search(self, query: str, *, limit: int = 4) -> list[dict[str, Any]]:
        if not self.settings.web_search_enabled:
            return []
        if self._serp_key():
            payload = await self._serp({"engine": "google", "q": query, "num": limit, "hl": "en"}, purpose="search")
            results = [
                {
                    "title": row.get("title", ""),
                    "url": row.get("link", ""),
                    "snippet": row.get("snippet", ""),
                }
                for row in (payload or {}).get("organic_results", [])[:limit]
            ]
            if self.trace:
                self.trace.search(provider="SerpAPI", query=query, results=results)
            if results:
                return results
        results = await self._wikipedia_search(query, limit)
        if self.trace:
            self.trace.search(provider="Wikipedia", query=query, results=results)
        if results:
            return results
        # Final fallback: DeepSeek's own search API, used only when both
        # SerpAPI and Wikipedia return nothing. It is not an LLM browsing call.
        results = await self._deepseek_search(query, limit)
        if self.trace:
            self.trace.search(provider="DeepSeek Search", query=query, results=results)
        return results

    async def _deepseek_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        key = (self.settings.deepseek_search_api_key or self.settings.verifier_api_key or "").strip()
        if not key:
            return []
        base = (self.settings.deepseek_search_base_url or "https://api.deepseek.com").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        try:
            response = await self.client.post(
                f"{base}/v1/tools/search",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"query": query[:128], "count": max(1, min(limit, 50)), "need_content": False},
                timeout=20.0,
            )
            response.raise_for_status()
            rows = (response.json() or {}).get("results") or []
        except (httpx.HTTPError, ValueError):
            return []
        results: list[dict[str, Any]] = []
        for row in rows:
            url = str(row.get("url") or "")
            if not url:
                continue
            results.append(
                {
                    "title": str(row.get("title") or url),
                    "url": url,
                    "snippet": str(row.get("summary") or row.get("snippet") or "")[:500],
                }
            )
        return results[:limit]

    async def _wikipedia_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        try:
            response = await self.client.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": limit},
                timeout=12.0,
            )
            response.raise_for_status()
            rows = ((response.json().get("query") or {}).get("search")) or []
            return [
                {
                    "title": row.get("title", ""),
                    "url": f"https://en.wikipedia.org/wiki/{str(row.get('title', '')).replace(' ', '_')}",
                    "snippet": _strip_tags(row.get("snippet", "")),
                }
                for row in rows
            ]
        except (httpx.HTTPError, ValueError):
            return []

    async def page_text(self, url: str) -> str:
        """Readable text of a public web page; "" for anything unsafe or unreachable (SSRF guard)."""
        current = url
        try:
            for _hop in range(MAX_PAGE_REDIRECTS + 1):
                if not await url_is_public(current):
                    return ""
                async with self.client.stream("GET", current, follow_redirects=False, timeout=12.0) as response:
                    peer = _peer_address(response)
                    if peer and not _ip_is_public(peer):
                        return ""
                    location = response.headers.get("location")
                    if response.is_redirect and location:
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= MAX_PAGE_BYTES:
                            break
                    raw = b"".join(chunks)[:MAX_PAGE_BYTES]
                    return _strip_html(raw.decode(response.encoding or "utf-8", errors="replace"))[:6000]
        except (httpx.HTTPError, ValueError, OSError):
            return ""
        return ""

    # -- community videos ---------------------------------------------

    async def videos(self, query: str, *, limit: int = 3) -> list[VideoNote]:
        """YouTube (and optionally Bilibili) notes for a destination. Never a fact source."""
        notes: list[VideoNote] = []
        self.last_video_error = ""
        if self.settings.enable_notes and self.settings.enable_youtube and self.settings.youtube_api_key:
            params = {
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": max(1, min(limit, 5)),
                "order": "relevance",
                "videoDuration": "medium",
                "videoEmbeddable": "true",
                "relevanceLanguage": "en",
                "key": self.settings.youtube_api_key,
            }
            try:
                response = await self.client.get(
                    "https://www.googleapis.com/youtube/v3/search", params=params, timeout=12.0
                )
                response.raise_for_status()
                for item in (response.json().get("items") or [])[:limit]:
                    snippet = item.get("snippet") or {}
                    video_id = (item.get("id") or {}).get("videoId")
                    if not video_id:
                        continue
                    notes.append(
                        VideoNote(
                            title=html.unescape(str(snippet.get("title") or "Video")),
                            url=f"https://www.youtube.com/watch?v={video_id}",
                            source="youtube",
                            snippet=html.unescape(str(snippet.get("description") or ""))[:400],
                            video_id=str(video_id),
                            thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                            published_at=snippet.get("publishedAt"),
                        )
                    )
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    self.last_video_error = "YouTube's daily video-search quota is exhausted."
                else:
                    self.last_video_error = f"YouTube video search failed ({exc.response.status_code if exc.response is not None else 'HTTP error'})."
            except (httpx.HTTPError, ValueError):
                self.last_video_error = "YouTube video search is unavailable."
        if self.settings.enable_notes and self.settings.enable_bilibili:
            try:
                response = await self.client.get(
                    "https://api.bilibili.com/x/web-interface/search/type",
                    params={"search_type": "video", "keyword": query, "page": 1},
                    headers={"Referer": "https://www.bilibili.com/"},
                    timeout=12.0,
                )
                response.raise_for_status()
                for item in ((response.json().get("data") or {}).get("result") or [])[: max(0, limit - len(notes))]:
                    bvid = item.get("bvid")
                    if not bvid:
                        continue
                    notes.append(
                        VideoNote(
                            title=html.unescape(str(item.get("title") or "Video")).replace("<em class=\"keyword\">", "").replace("</em>", ""),
                            url=f"https://www.bilibili.com/video/{bvid}",
                            source="bilibili",
                            snippet=str(item.get("description") or "")[:400],
                            video_id=str(bvid),
                            thumbnail_url=("https:" + str(item.get("pic"))) if item.get("pic") else "",
                        )
                    )
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 412:
                    self.last_video_error = "Bilibili blocked the search request (HTTP 412)."
                else:
                    self.last_video_error = f"Bilibili video search failed ({exc.response.status_code if exc.response is not None else 'HTTP error'})."
            except (httpx.HTTPError, ValueError):
                self.last_video_error = "Bilibili video search is unavailable."
        if not notes and self.settings.enable_notes and self._serp_key():
            # YouTube's search endpoint has a small daily quota (100 searches per
            # project) and returns 429 when it is spent. SerpAPI can still find
            # public YouTube watch pages through normal web search.
            try:
                rows = await self.search(f"{query} youtube", limit=max(6, limit * 2))
            except Exception:  # noqa: BLE001 - video notes are optional
                rows = []
            for row in rows:
                video_id = _youtube_watch_id(str(row.get("url") or ""))
                if not video_id:
                    continue
                notes.append(
                    VideoNote(
                        title=(str(row.get("title") or "").strip() or "YouTube video")[:200],
                        url=f"https://www.youtube.com/watch?v={video_id}",
                        source="youtube",
                        snippet=str(row.get("snippet") or "")[:400],
                        video_id=video_id,
                        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    )
                )
                if len(notes) >= limit:
                    break
            if notes and self.trace:
                self.trace.search(
                    provider="SerpAPI YouTube",
                    query=query,
                    results=[{"title": note.title, "url": note.url} for note in notes],
                )
        if self.trace and notes:
            self.trace.search(provider=notes[0].source, query=query, results=[{"title": note.title, "url": note.url} for note in notes])
        if not notes and not self.last_video_error:
            self.last_video_error = "No video notes were found for this destination."
        return notes[:limit]

    # -- named place lookup -------------------------------------------

    async def lookup_places(self, names: Any, city: str = "") -> list[Evidence]:
        """Look up named places; a joined string is turned into separate searches."""
        found: list[Evidence] = []
        seen: set[str] = set()
        for query in _lookup_queries(names):
            hits = await self.lookup_place(query, city)
            for evidence in hits:
                key = evidence.name.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    found.append(evidence)
        return found

    async def lookup_place(self, name: str, city: str = "") -> list[Evidence]:
        """Look up a place the traveller named, even when it is not in the place corpus.

        Live mode asks Google Places for the exact name. Every mode then falls back to
        Wikipedia, which gives coordinates and a citable summary. The record is marked
        ``stale`` because a reference page is not a live business listing: hours,
        prices and tickets stay unconfirmed and are shown as a gap.
        """
        wanted = (name or "").strip()
        if len(wanted) < 3:
            return []
        if not self.sandbox and self.settings.google_places_api_key:
            found = await self._google_place(f"{wanted} {city}".strip(), max_results=3)
            matches = [item for item in found if _names_related(wanted, item.name, city)]
            if matches:
                return matches[:2]
            if found:
                return found[:1]
        wiki = await self._wikipedia_place(wanted, city)
        return [wiki] if wiki else []

    async def _wikipedia_place(self, name: str, city: str = "") -> Evidence | None:
        fields = {
            "action": "query",
            "prop": "coordinates|extracts|pageimages",
            "exintro": 1,
            "explaintext": 1,
            "piprop": "thumbnail",
            "pithumbsize": 900,
            "format": "json",
            "redirects": 1,
        }
        collected: dict[str, dict[str, Any]] = {}
        for params in (
            {**fields, "titles": name},
            {**fields, "generator": "search", "gsrsearch": name, "gsrlimit": 5},
            {**fields, "generator": "search", "gsrsearch": f"{name} {city}".strip(), "gsrlimit": 5},
        ):
            for key, page in (await self._wiki(params)).items():
                if page.get("missing") is not None:
                    continue
                collected.setdefault(key, page)
        def rank(row: dict[str, Any]) -> tuple[int, int]:
            # An exact-title page (or a redirect from it) is the strongest match;
            # then Wikipedia's own search order.
            return (0 if row.get("index") is None else 1, row.get("index") or 0)

        for page in sorted(collected.values(), key=rank):
            title = str(page.get("title") or "").strip()
            extract = str(page.get("extract") or "").strip()
            if not title or "(disambiguation)" in title.lower():
                continue
            if not _page_matches(name, title, extract, city):
                continue
            if not page.get("coordinates") and len(extract) < 80:
                continue
            coordinates = (page.get("coordinates") or [{}])[0]
            lat, lng = coordinates.get("lat"), coordinates.get("lon")
            meta = corpus.city_meta(city)
            if meta and lat is not None and lng is not None:
                if haversine_km(GeoPoint(lat=float(lat), lng=float(lng)), GeoPoint(lat=meta["lat"], lng=meta["lng"])) > 120:
                    continue
            elif city and city.lower() not in title.lower() and city.lower() not in extract.lower():
                continue
            thumbnail = (page.get("thumbnail") or {}).get("source")
            url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
            return Evidence(
                ref="wiki",
                kind="attraction",
                name=title,
                address=city,
                geo=GeoPoint(lat=float(lat), lng=float(lng)) if lat is not None and lng is not None else None,
                summary=extract[:600] or f"Reference page for {title}.",
                source_refs=[SourceRef(title=title, url=url, source="Wikipedia", fetched_at=now_iso())],
                data_status="stale",
                fetched_at=now_iso(),
                image=ImageRef(url=thumbnail, attribution="Wikipedia", source="wikimedia") if thumbnail else None,
                tags=["reference", "unconfirmed hours"],
                source_types=["reference"],
                suggested_minutes=180,
                best_time="morning",
            )
        return None

    async def _wiki(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.get("https://en.wikipedia.org/w/api.php", params=params, timeout=15.0)
            response.raise_for_status()
            query = response.json().get("query") or {}
        except (httpx.HTTPError, ValueError):
            return {}
        pages = query.get("pages") or {}
        return {str(key): value for key, value in pages.items() if isinstance(value, dict)}

    # -- serpapi -------------------------------------------------------

    def _serp_key(self) -> str:
        for row in self.serp_keys:
            if row.get("status") in {"active", "ready"} and str(row.get("_key") or "").strip():
                return str(row["_key"]).strip()
        pool = self.settings.serpapi_key_pool
        if pool:
            entries = []
            for chunk in pool.split(";"):
                parts = chunk.split("|")
                if len(parts) == 3 and parts[0] in {"active", "ready"}:
                    entries.append(parts[2].strip())
            if entries:
                return entries[self._serp_index % len(entries)]
        return self.settings.serpapi_key

    async def _serp(self, params: dict[str, Any], *, purpose: str) -> dict[str, Any] | None:
        key = self._serp_key()
        if not key:
            return None
        tried = 0
        while tried < 3:
            try:
                response = await self.client.get(
                    "https://serpapi.com/search.json", params={**params, "api_key": key}, timeout=25.0
                )
                if response.status_code in {401, 429}:
                    tried += 1
                    self._serp_index += 1
                    key = self._serp_key()
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError):
                return None
        return None


# -- helpers -----------------------------------------------------------


def _at(values: Any, index: int) -> float | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    return float(value) if value is not None else None


def _date_range(start: date, end: date) -> list[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(max(1, days + 1))]


def _iata(city: str) -> str:
    key = (city or "").strip().lower()
    if key in IATA:
        return IATA[key]
    for name, code in IATA.items():
        if name in key:
            return code
    return ""


def _youtube_watch_id(url: str) -> str:
    """The v= id of a YouTube watch URL, or an empty string."""
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    if "youtu.be" in parsed.netloc:
        return parsed.path.strip("/")[:20]
    if "youtube.com" in parsed.netloc:
        value = (parse_qs(parsed.query).get("v") or [""])[0]
        return str(value)[:20]
    return ""


def _same_city(origin: str, destination: str) -> bool:
    first, second = (origin or "").strip().lower(), (destination or "").strip().lower()
    if not first or not second:
        return False
    if first == second:
        return True
    a, b = _iata(origin), _iata(destination)
    return bool(a and a == b)


def _leg_time(leg: dict[str, Any], key: str) -> str | None:
    value = ((leg.get(key) or {}).get("time") or "").strip()
    return value.replace(" ", "T") or None


def _flight_minutes(origin: str, destination: str) -> int:
    a, b = _iata(origin), _iata(destination)
    return FLIGHT_MINUTES.get((a, b), 420)


def _minutes_for(km: float, mode: str) -> float:
    speed = {"walk": 4.5, "cycle": 14.0, "transit": 18.0, "drive": 32.0}.get(mode, 4.5)
    return round(km * _detour(mode) / speed * 60.0, 1)


def _detour(mode: str) -> float:
    return {"walk": 1.25, "cycle": 1.3, "transit": 1.35, "drive": 1.3}.get(mode, 1.25)


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    radius = 6371.0
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


GENERIC_NAME_WORDS = {
    "university", "college", "school", "museum", "park", "hotel", "restaurant",
    "temple", "market", "station", "island", "garden", "palace", "centre", "center",
    "city", "district", "road", "avenue", "harbour", "harbor", "peak", "bay",
    "tower", "square", "beach", "resort", "theatre", "theater", "art", "history",
    "science", "hall", "building", "street", "mall", "zoo", "aquarium", "church",
}


def _page_matches(name: str, title: str, extract: str, city: str = "") -> bool:
    """A page matches the named place, including an acronym the page spells out.

    "HKU" is not in the title "University of Hong Kong", but the first sentence says
    "(HKU)"; that is a match.
    """
    if _names_related(name, title, city):
        return True
    acronym = name.strip().lower().rstrip(".")
    return 2 <= len(acronym) <= 6 and acronym.isalpha() and acronym in extract.lower()


def _names_related(a: str, b: str, city: str = "") -> bool:
    """Name match for a lookup, ignoring city and generic place words.

    "Hong Kong Disneyland" must match a page about Disneyland, not any Hong Kong
    page; "University of Hong Kong" must not match Hong Kong Baptist University.
    """
    city_words = {word.strip(".,()").lower() for word in (city or "").split()}
    words = [word.strip(".,()").lower() for word in (a or "").split() if len(word) > 2]
    distinctive = [word for word in words if word not in city_words and word not in GENERIC_NAME_WORDS]
    other = (b or "").lower().replace("-", " ")
    if distinctive:
        return all(word.replace("-", " ") in other for word in distinctive)
    full = " ".join(word.replace("-", " ") for word in words)
    other_clean = " ".join(word for word in other.split() if word not in {"of", "the", "and"})
    return bool(full) and (full in other or full in other_clean)


def _strip_tags(text: str) -> str:
    out: list[str] = []
    inside = False
    for char in text or "":
        if char == "<":
            inside = True
        elif char == ">":
            inside = False
        elif not inside:
            out.append(char)
    return "".join(out).strip()


def _strip_html(text: str) -> str:
    plain = _strip_tags(text)
    return " ".join(plain.split())
