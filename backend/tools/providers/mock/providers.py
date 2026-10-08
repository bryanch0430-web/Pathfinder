"""Mock providers for the five tool families, backed by the static dataset in `data.py`.

They are the only concrete providers (team decision 5: vendors are provisional), so they aim to
be realistic enough for the agents, the planner and the evaluation harness: real place names
and coordinates, plausible prices in the destination currency, and deterministic output for a
given input. Every call first consults the optional `FaultPlan`, which is how tests and the
evaluation harness simulate timeouts, rate limits, closures, typhoon signals and transit delays.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from backend.schemas.common import GeoPoint, Money
from backend.schemas.tools import (
    FieldIssue,
    ForecastDay,
    ForecastRequest,
    GeocodeRequest,
    GeocodeResult,
    PlaceRecord,
    PlacesSearchRequest,
    ReservationCheckRequest,
    ReservationRecord,
    SearchHit,
    TicketRecord,
    TicketSearchRequest,
    ToolOperation,
    WebSearchRequest,
)
from backend.tools.providers.base import (
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
    ProviderUnavailable,
)
from backend.tools.providers.mock.data import (
    USD_PER_UNIT,
    Attraction,
    City,
    convert,
    find_attraction,
    find_hotel,
    haversine_km,
    is_city_name,
    matches_place,
    normalize,
    offset_point,
    place_id,
    resolve_city,
    round_money,
    slugify,
    stable_rng,
    stable_unit,
)
from backend.tools.providers.mock.faults import FaultKind, FaultPlan

RATE_LIMIT_RETRY_AFTER_S = 0.05


class _MockProvider:
    """Shared fault/latency handling. `call_count` counts every call (including failed ones)."""

    def __init__(self, name: str, faults: FaultPlan | None) -> None:
        self.name = name
        self._faults = faults
        self.call_count = 0

    async def _enter(self, operation: ToolOperation) -> None:
        self.call_count += 1
        if self._faults is None:
            return
        fault = self._faults.next_fault(operation, self.name)
        latency = self._faults.latency(operation)
        if latency > 0:
            await asyncio.sleep(latency)
        if fault is not None:
            raise self._error(fault, operation)

    def _error(self, fault: FaultKind, operation: ToolOperation) -> ProviderError:
        where = f"{self.name} {operation.value}"
        match fault:
            case FaultKind.TIMEOUT:
                return ProviderTimeout(f"{where}: simulated timeout")
            case FaultKind.RATE_LIMIT:
                return ProviderRateLimited(
                    f"{where}: simulated rate limit", retry_after_s=RATE_LIMIT_RETRY_AFTER_S
                )
            case FaultKind.SERVER_FAULT:
                return ProviderServerError(f"{where}: simulated server fault")
            case FaultKind.BAD_REQUEST:
                return ProviderBadRequest(
                    [
                        FieldIssue(
                            loc="",
                            message=f"{where}: request rejected by provider (simulated)",
                            kind="provider_bad_request",
                        )
                    ]
                )
            case FaultKind.UNAVAILABLE:
                return ProviderUnavailable(f"{where}: simulated outage")


# ------------------------------------------------------------------------------------------------
# Web search
# ------------------------------------------------------------------------------------------------

_RAIN_WORDS = frozenset({"rain", "rainy", "wet", "typhoon", "storm", "indoor", "indoors"})


def _tokens(text: str) -> set[str]:
    return {t[:-1] if len(t) > 3 and t.endswith("s") else t for t in normalize(text).split()}


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


class MockSearchProvider(_MockProvider):
    """Travel-notes search. One hit per attraction whose title is EXACTLY the place name (so
    the attraction agent can join hits to places records), ranked by overlap with the query."""

    async def search(self, request: WebSearchRequest) -> list[SearchHit]:
        await self._enter(ToolOperation.WEB_SEARCH)
        city = resolve_city(request.destination)
        query = _tokens(request.query)
        wants_indoor = bool(query & _RAIN_WORDS)

        def score(a: Attraction) -> int:
            place_tokens = _tokens(a.name) | {a.category, "indoor" if a.indoor else "outdoor"}
            points = len(query & place_tokens)
            if wants_indoor and a.indoor:
                points += 2
            return points

        ranked = sorted(city.attractions, key=lambda a: -score(a))[: request.limit]
        hits = [
            SearchHit(
                title=a.name,
                url=f"https://notes.example/{city.key}/{slugify(a.name)}",
                snippet=self._snippet(city, a),
            )
            for a in ranked
        ]
        for i, text in enumerate(self._faults.injected_texts(city.key) if self._faults else []):
            hits.append(
                SearchHit(
                    title="Traveller forum post",
                    url=f"https://forum.example/{city.key}/post-{i + 1}",
                    snippet=text,
                )
            )
        return hits

    @staticmethod
    def _snippet(city: City, a: Attraction) -> str:
        where = "an indoor" if a.indoor else "an outdoor"
        cost = "free entry" if a.price == 0 else f"about {a.price:,.0f} {city.currency} per person"
        booking = " Book ahead." if a.needs_reservation else ""
        category = a.category.replace("_", " ")
        return (
            f"{a.name} is {_article(category)} {category} in {city.name}: {where} stop, "
            f"rated {a.rating}/5, {cost}.{booking}"
        )


# ------------------------------------------------------------------------------------------------
# Maps
# ------------------------------------------------------------------------------------------------


class MockMapsProvider(_MockProvider):
    """Geocoder. Two instances (mock-google, mock-amap) form the fallback chain."""

    async def geocode(self, request: GeocodeRequest) -> GeocodeResult:
        await self._enter(ToolOperation.GEOCODE)
        city = resolve_city(request.city)
        attraction = find_attraction(city, request.query)
        hotel = None if attraction else find_hotel(city, request.query)
        if attraction is not None:
            name, lat, lng = attraction.name, attraction.lat, attraction.lng
            address = f"{name}, {city.name}"
        elif hotel is not None:
            name, lat, lng = hotel.name, hotel.lat, hotel.lng
            address = f"{name}, {city.name}"
        elif is_city_name(city, request.query):
            lat, lng = city.lat, city.lng
            address = f"{city.name}, {city.country}" if city.country else city.name
        else:
            # Unknown query: deterministic point within 3 km of the centre.
            u_dist = stable_unit("geocode-dist", city.key, normalize(request.query))
            u_theta = stable_unit("geocode-theta", city.key, normalize(request.query))
            lat, lng = offset_point(city.lat, city.lng, 0.3 + 2.5 * u_dist, 2 * math.pi * u_theta)
            address = f"{request.query.strip()}, {city.name}"
        return GeocodeResult(
            query=request.query, location=GeoPoint(lat=lat, lng=lng), formatted_address=address
        )


# ------------------------------------------------------------------------------------------------
# Weather
# ------------------------------------------------------------------------------------------------


def _summary(precip: float, t_max: float) -> str:
    if precip >= 0.8:
        return "Heavy rain"
    if precip >= 0.6:
        return "Showers"
    if precip >= 0.4:
        return "Cloudy with a chance of showers"
    if precip >= 0.2:
        return "Partly cloudy"
    return "Hot and sunny" if t_max >= 30 else "Sunny"


def _warning_summary(signal: str) -> str:
    upper = signal.upper()
    if upper.startswith("T") and upper[1:].isdigit():
        return f"Tropical cyclone warning signal {upper} in force"
    if upper in {"AMBER", "RED", "BLACK"}:
        return f"{upper.title()} rainstorm warning in force"
    return f"Weather warning {signal} in force"


class MockWeatherProvider(_MockProvider):
    """Daily forecast, deterministic per (city, date); seasonal (northern hemisphere) normals."""

    async def forecast(self, request: ForecastRequest) -> list[ForecastDay]:
        await self._enter(ToolOperation.FORECAST)
        city = resolve_city(request.location)
        days: list[ForecastDay] = []
        current = request.start_date
        while current <= request.end_date:
            days.append(self._day(city, current))
            current += timedelta(days=1)
        return days

    def _day(self, city: City, on: date) -> ForecastDay:
        rng = stable_rng("weather", city.key, on.isoformat())
        t_min, t_max = city.monthly_temps[on.month - 1]
        shift = rng.uniform(-2.0, 2.0)
        spread = rng.uniform(-1.0, 1.0)
        low = round(t_min + shift, 1)
        high = round(max(t_max + shift + spread, low + 1.0), 1)
        precip = round(min(0.95, max(0.02, city.monthly_rain[on.month - 1] + rng.uniform(-0.15, 0.15))), 2)
        warning = self._faults.warning_for(city.key, on) if self._faults else None
        if warning is not None:
            precip = max(precip, 0.9)
            summary = _warning_summary(warning)
        else:
            summary = _summary(precip, high)
        return ForecastDay(
            date=on,
            summary=summary,
            temp_min_c=low,
            temp_max_c=high,
            precipitation_chance=precip,
            warning_signal=warning,
        )


# ------------------------------------------------------------------------------------------------
# Places
# ------------------------------------------------------------------------------------------------


class MockPlacesProvider(_MockProvider):
    """Attractions and hotels for a destination."""

    async def search(self, request: PlacesSearchRequest) -> list[PlaceRecord]:
        await self._enter(ToolOperation.PLACES_SEARCH)
        city = resolve_city(request.destination)
        if request.category == "attraction":
            return [self._attraction(city, a) for a in city.attractions][: request.limit]
        return self._hotels(city, request)

    def _attraction(self, city: City, a: Attraction) -> PlaceRecord:
        pid = place_id(city, a.name)
        return PlaceRecord(
            place_id=pid,
            name=a.name,
            category=a.category,
            location=GeoPoint(lat=a.lat, lng=a.lng),
            address=f"{a.name}, {city.name}",
            rating=a.rating,
            indoor=a.indoor,
            price=Money(amount=a.price, currency=city.currency),
            needs_reservation=a.needs_reservation,
            closed_dates=self._faults.closed_dates(a.name, pid) if self._faults else [],
            opening_hours=a.opening_hours,
        )

    def _hotels(self, city: City, request: PlacesSearchRequest) -> list[PlaceRecord]:
        hotels = list(city.hotels)
        if request.max_price is not None:
            cap = convert(request.max_price, request.currency or city.currency, city.currency)
            fitting = [h for h in hotels if h.nightly_price <= cap]
            # Nothing fits: return the two cheapest anyway; the hotel agent decides (and the
            # budget check flags it) rather than the tool silently returning nothing.
            hotels = fitting or sorted(hotels, key=lambda h: h.nightly_price)[:2]
        if request.style:
            wanted = normalize(request.style)
            hotels.sort(key=lambda h: 0 if h.style == wanted or h.style in wanted.split() else 1)
        return [
            PlaceRecord(
                place_id=place_id(city, h.name),
                name=h.name,
                category="hotel",
                location=GeoPoint(lat=h.lat, lng=h.lng),
                address=f"{h.name}, {city.name}",
                rating=h.rating,
                indoor=True,
                price=Money(amount=h.nightly_price, currency=city.currency),
                style=h.style,
                opening_hours="Check-in 15:00, check-out 11:00",
            )
            for h in hotels[: request.limit]
        ]


# ------------------------------------------------------------------------------------------------
# Tickets and reservations
# ------------------------------------------------------------------------------------------------

# Known rail routes: (minutes, JPY per person, carrier).
_TRAIN_ROUTES: dict[frozenset[str], tuple[int, float, str]] = {
    frozenset({"tokyo", "kyoto"}): (135, 14170, "JR Central Tokaido Shinkansen"),
    frozenset({"tokyo", "osaka"}): (150, 14720, "JR Central Tokaido Shinkansen"),
    frozenset({"kyoto", "osaka"}): (29, 580, "JR West Special Rapid"),
}
_DEPARTURES: tuple[time, ...] = (time(7, 30), time(12, 30), time(18, 0))  # morning/midday/evening
_FLIGHT_SLOT_FACTOR: tuple[float, ...] = (1.1, 0.95, 0.9)
_MIN_FLIGHT_KM = 150.0
_MAX_TRAIN_KM = 1500.0


@dataclass(frozen=True, slots=True)
class _Leg:
    minutes: int
    price_usd: float
    carriers: tuple[str, ...]
    slot_factors: tuple[float, ...] = (1.0, 1.0, 1.0)


def _round5(minutes: float) -> int:
    return max(5, int(5 * round(minutes / 5)))


def _train_leg(origin: City, dest: City, km: float) -> _Leg | None:
    known = _TRAIN_ROUTES.get(frozenset({origin.key, dest.key}))
    if known is not None:
        minutes, jpy, carrier = known
        return _Leg(minutes, convert(jpy, "JPY", "USD"), (carrier,))
    japan_link = (origin.region == "japan") != (dest.region == "japan")  # no rail across the sea
    if japan_link or km > _MAX_TRAIN_KM:
        return None
    carrier = "MTR Intercity" if "hong-kong" in {origin.key, dest.key} else "Intercity Rail"
    return _Leg(_round5(20 + km / 200 * 60), 5 + 0.17 * km, (carrier,))


def _flight_leg(origin: City, dest: City, km: float) -> _Leg | None:
    if km < _MIN_FLIGHT_KM:
        return None
    regions = {origin.region, dest.region}
    if regions == {"japan"}:
        carriers: tuple[str, ...] = ("ANA", "JAL", "Peach")
    elif regions == {"japan", "greater-china"}:
        carriers = ("Cathay Pacific", "Japan Airlines", "HK Express")
    else:
        carriers = ("Skyline Air", "Northwind Airways", "Blue Meridian Air")
    return _Leg(_round5(45 + km / 750 * 60), 60 + 0.11 * km, carriers, _FLIGHT_SLOT_FACTOR)


class MockTicketProvider(_MockProvider):
    """Train/flight options (3 per mode per day) and reservation lead times."""

    async def search(self, request: TicketSearchRequest) -> list[TicketRecord]:
        await self._enter(ToolOperation.TICKET_SEARCH)
        origin, dest = resolve_city(request.origin), resolve_city(request.destination)
        if origin.key == dest.key:
            return []
        km = haversine_km(origin.lat, origin.lng, dest.lat, dest.lng)
        records: list[TicketRecord] = []
        for mode in dict.fromkeys(request.modes):  # de-duplicate, keep order
            leg = _train_leg(origin, dest, km) if mode == "train" else _flight_leg(origin, dest, km)
            if leg is None:  # mode does not serve this route (e.g. no train Hong Kong -> Tokyo)
                continue
            records.extend(self._options(mode, origin, dest, request.travel_date, leg, request.currency))
        return records

    def _options(
        self,
        mode: Literal["train", "flight"],
        origin: City,
        dest: City,
        on: date,
        leg: _Leg,
        currency: str | None = None,
    ) -> list[TicketRecord]:
        delay = self._faults.transit_delay(mode, on, origin.key, dest.key) if self._faults else None
        origin_tz = timezone(timedelta(hours=origin.utc_offset_h))
        dest_tz = timezone(timedelta(hours=dest.utc_offset_h))
        # The requested currency when the mock knows its rate, else the arriving city's currency.
        ccy = currency.upper() if currency and currency.upper() in USD_PER_UNIT else dest.currency
        out: list[TicketRecord] = []
        for n, (dep, factor) in enumerate(zip(_DEPARTURES, leg.slot_factors, strict=True), start=1):
            ticket_id = f"{mode}-{origin.key}-{dest.key}-{on:%Y%m%d}-{n}"
            jitter = 1.0
            if mode == "flight":
                jitter = 0.95 + 0.1 * stable_unit("fare", ticket_id)
            amount = convert(leg.price_usd * factor * jitter, "USD", ccy)
            depart_at = datetime.combine(on, dep, tzinfo=origin_tz)
            out.append(
                TicketRecord(
                    ticket_id=ticket_id,
                    mode=mode,
                    carrier=leg.carriers[(n - 1) % len(leg.carriers)],
                    origin=origin.name,
                    destination=dest.name,
                    depart_at=depart_at,
                    arrive_at=(depart_at + timedelta(minutes=leg.minutes)).astimezone(dest_tz),
                    price=Money(amount=round_money(amount, ccy), currency=ccy),
                    status="delayed" if delay else "scheduled",
                    delay_minutes=delay or 0,
                )
            )
        return out

    async def reservations(self, request: ReservationCheckRequest) -> list[ReservationRecord]:
        await self._enter(ToolOperation.RESERVATION_CHECK)
        city = resolve_city(request.destination)
        needing = [a for a in city.attractions if a.needs_reservation]
        if request.place_names:
            needing = [
                a
                for a in needing
                if any(
                    matches_place(a.name, place_id(city, a.name), wanted)
                    or find_attraction(city, wanted) is a
                    for wanted in request.place_names
                )
            ]
        return [
            ReservationRecord(
                place_name=a.name,
                lead_time_days=a.lead_time_days,
                booking_url=f"https://booking.example/{city.key}/{slugify(a.name)}",
            )
            for a in needing
        ]


# ------------------------------------------------------------------------------------------------
# Bundle
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MockProviders:
    search: MockSearchProvider
    maps: list[MockMapsProvider]  # fallback order: mock-google, then mock-amap
    weather: MockWeatherProvider
    places: MockPlacesProvider
    tickets: MockTicketProvider


def build_mock_providers(faults: FaultPlan | None = None) -> MockProviders:
    return MockProviders(
        search=MockSearchProvider("mock-search", faults),
        maps=[MockMapsProvider("mock-google", faults), MockMapsProvider("mock-amap", faults)],
        weather=MockWeatherProvider("mock-weather", faults),
        places=MockPlacesProvider("mock-places", faults),
        tickets=MockTicketProvider("mock-tickets", faults),
    )
