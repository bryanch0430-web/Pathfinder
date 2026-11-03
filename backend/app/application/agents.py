"""The four pre-planning agents.

Each agent owns one question and a tool subset, runs under its own deadline, and
returns a typed `AgentResult`. Failures are data: they become a `DataGap` and the
plan continues. No agent may turn a model guess into a fact.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable

from app.application.toolbox import LoggedWebSource, Toolbox
from app.application.trip_rules import place_wishes, wish_matches
from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    DataGap,
    DataStatus,
    DateRange,
    FailureKind,
    GeoLocation,
    HotelCandidate,
    PlaceCandidate,
    SourceRef,
    TripContext,
    utcnow,
)
from app.infrastructure.http import haversine_km


@dataclass
class AgentContext:
    context: TripContext
    destination: GeoLocation
    date_range: DateRange
    request_id: str
    toolbox: Toolbox
    settings: Settings
    emit: Callable[[str, dict[str, Any]], None] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class BaseAgent:
    name: AgentName
    tools: frozenset[str] = frozenset()

    def __init__(self, registry: Any, settings: Settings):
        self.registry = registry
        self.settings = settings

    async def run(self, request: AgentContext) -> AgentResult:
        raise NotImplementedError

    def _result(
        self,
        request: AgentContext,
        *,
        status: AgentStatus,
        payload: dict[str, Any],
        notes: list[str] | None = None,
        sources: list[SourceRef] | None = None,
        failures: list[DataGap] | None = None,
        started: float,
    ) -> AgentResult:
        return AgentResult(
            agent=self.name,
            status=status,
            payload=payload,
            notes=notes or [],
            sources=_dedupe_sources(sources or []),
            failures=failures or [],
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            fetched_at=utcnow(),
        )

    async def _call(
        self,
        request: AgentContext,
        tool: str,
        fn: Callable[..., Awaitable[Any]],
        *,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        if tool not in self.tools:
            raise ProviderError(f"{self.name} attempted a tool it does not own: {tool}", source=self.name)
        return await asyncio.wait_for(
            request.toolbox.call(tool, fn, **kwargs),
            timeout=timeout or min(self.settings.agent_timeout_seconds, 25.0),
        )

    def _grok_source(self) -> Any | None:
        """The slow search, used only when DeepSeek relevance is zero.

        ``grok_search_enabled`` still gates the question-answering path. This
        fallback constructs the client from the xAI key when that flag is off,
        and returns None when the key is missing.
        """
        configured = list(getattr(self.registry, "web_deep", None) or [])
        if configured:
            return configured[0]
        from app.infrastructure.providers.websearch import GrokSearchSource

        source = GrokSearchSource(self.settings)
        return source if source.available else None

    async def _web_references(
        self,
        *,
        section: str,
        city: str,
        query: str,
        official_empty: bool,
        toolbox: Toolbox | None = None,
    ) -> tuple[list[dict[str, Any]], DataGap | None]:
        """Reference pages for a thin section. Never a price or a temperature."""
        web = list(getattr(self.registry, "web", None) or [])
        deepseek = web[0] if web else None
        grok = self._grok_source()
        if deepseek is None and grok is None:
            return [], None
        if toolbox is not None:
            # A web search is a tool call: it goes through the toolbox and its log.
            deepseek = LoggedWebSource(deepseek, toolbox) if deepseek is not None else None
            grok = LoggedWebSource(grok, toolbox) if grok is not None else None
        from app.application.web_lookup import inadequate_gap, lookup_web_references

        found = await lookup_web_references(
            section=section,
            city=city,
            query=query,
            deepseek=deepseek,
            grok=grok,
        )
        if not official_empty:
            return found.references, None
        if found.found:
            return found.references, inadequate_gap(section, city)
        return found.references, found.gap


class AttractionAgent(BaseAgent):
    name = AgentName.ATTRACTION
    tools = frozenset({"places.search", "places.notes", "geocode"})

    async def run(self, request: AgentContext) -> AgentResult:
        started = time.perf_counter()
        failures: list[DataGap] = []
        sources: list[SourceRef] = []
        places: list[PlaceCandidate] = []
        restaurants: list[PlaceCandidate] = []
        notes: list[dict[str, Any]] = []

        for source in self.registry.places:
            if not getattr(source, "available", True):
                continue
            try:
                results = await self._call(
                    request,
                    "places.search",
                    source.search,
                    destination=request.context.destination,
                    geo=request.destination.geo,
                    category="attraction",
                    limit=10,
                    query=request.extra.get("places_query"),
                )
                if results:
                    places = _merge_places(results)
                    sources.extend(ref for place in results for ref in place.source_refs)
                    break
            except (ProviderError, asyncio.TimeoutError, Exception) as exc:
                failures.append(_gap("attractions", exc))

        # Restaurants are only used when a source actually returns them.
        for source in self.registry.places:
            if not getattr(source, "available", True):
                continue
            try:
                results = await self._call(
                    request,
                    "places.search",
                    source.search,
                    destination=request.context.destination,
                    geo=request.destination.geo,
                    category="restaurant",
                    limit=6,
                    query=request.extra.get("meals_query"),
                )
                if results:
                    restaurants = _merge_places(restaurants + results)
                    sources.extend(ref for place in results for ref in place.source_refs)
                    break
            except (ProviderError, asyncio.TimeoutError, Exception) as exc:
                failures.append(_gap("meals", exc))

        for source in self.registry.notes:
            if not getattr(source, "available", True):
                continue
            try:
                results = await self._call(
                    request,
                    "places.notes",
                    source.search,
                    query=f"{request.context.destination} travel guide attractions",
                    limit=3,
                    window_months=self.settings.search_window_months,
                    timeout=min(self.settings.agent_timeout_seconds, 15.0),
                )
                for note in results:
                    notes.append(note.model_dump(mode="json"))
                sources.extend(
                    SourceRef(title=note.title, url=note.url, source=note.source, fetched_at=note.fetched_at)
                    for note in results
                )
            except (ProviderError, asyncio.TimeoutError, Exception):
                # Community notes are an optional enrichment, not a required plan
                # section. A Bilibili rate-limit must not become a visible data gap.
                continue

        # Fill coordinates for a few guide entries so they can appear on the map.
        missing_geo = [place for place in places if place.geo is None][:3]
        for place in missing_geo:
            try:
                location = await self._call(
                    request,
                    "geocode",
                    self.registry.geocoder.geocode,
                    name=f"{place.name}, {request.context.destination}",
                    timeout=10.0,
                )
                if location:
                    place.geo = location.geo
            except Exception:
                continue

        # A place the traveller named is searched by name, so a generic
        # "attractions in X" list that misses it cannot drop it.
        wishes: dict[str, str | None] = {}
        wished: list[PlaceCandidate] = []
        for wish in place_wishes(request.context):
            match = await self._find_wish(request, wish)
            wishes[wish] = match.name if match else None
            if match:
                match.tags = ["requested", *match.tags]
                wished.append(match)
                sources.extend(match.source_refs)

        places.sort(key=lambda p: (p.rating or 0, p.review_count or 0), reverse=True)
        places = [p for p in places if p.geo is not None][:10] or places[:10]
        if wished:
            places = _merge_places(wished + places)[: max(10, len(wished))]
        restaurants = [r for r in restaurants if r.geo is not None][:6]

        web_references: list[dict[str, Any]] = []
        if len(places) < 3:
            web_references, web_gap = await self._web_references(
                section="attraction",
                city=request.context.destination,
                query=f"things to do in {request.context.destination}",
                official_empty=not places,
                toolbox=request.toolbox,
            )
            if web_gap is not None:
                failures.append(web_gap)

        payload = {
            "places": [{"id": f"p{index + 1}", **place.model_dump(mode="json")} for index, place in enumerate(places)],
            "meals": [
                {"id": f"m{index + 1}", **place.model_dump(mode="json")} for index, place in enumerate(restaurants)
            ],
            "notes": notes,
            "centre": request.destination.geo.model_dump(),
            "web_references": web_references,
            "wishes": wishes,
        }
        if places:
            # A working fallback is not a degraded plan: do not surface the
            # failed higher-priority source as a user-facing data gap, but keep
            # a data note so the failure is still on the plan's record.
            recovered = [gap for gap in failures if gap.section == "attractions"]
            meal_failures = [gap for gap in failures if gap.section == "meals"]
            # A failed restaurant search stays a gap unless a fallback answered
            # (Proposal TC-02: a partial failure is always on the plan's record).
            failures = [] if restaurants else meal_failures
            status = AgentStatus.OK
            note_text = [f"{len(places)} attractions, {len(restaurants)} dining candidates"]
            if recovered:
                note_text.append(
                    f"A place source failed ({recovered[0].kind.value}); a fallback source answered."
                )
            if meal_failures and restaurants:
                note_text.append(
                    f"A restaurant source failed ({meal_failures[0].kind.value}); a fallback source answered."
                )
        else:
            status = AgentStatus.UNAVAILABLE
            note_text = ["No attraction source returned usable data."]
        return self._result(
            request,
            status=status,
            payload=payload,
            notes=note_text,
            sources=sources,
            failures=failures,
            started=started,
        )

    async def _find_wish(self, request: AgentContext, wish: str) -> PlaceCandidate | None:
        """The place a traveller named, or None when no source answers with that name."""
        for source in self.registry.places:
            if not getattr(source, "available", True):
                continue
            try:
                results = await self._call(
                    request,
                    "places.search",
                    source.search,
                    destination=request.context.destination,
                    geo=request.destination.geo,
                    category="attraction",
                    limit=3,
                    query=wish,
                )
            except (ProviderError, asyncio.TimeoutError, Exception):
                continue
            trusted = getattr(source, "ranks_by_relevance", False)
            for index, place in enumerate(results or []):
                if wish_matches(place, wish) or (trusted and index == 0):
                    return place
        return None


class HotelAgent(BaseAgent):
    name = AgentName.HOTEL
    tools = frozenset({"hotels.search"})

    async def run(self, request: AgentContext) -> AgentResult:
        started = time.perf_counter()
        failures: list[DataGap] = []
        sources: list[SourceRef] = []
        nights = max((request.date_range.end - request.date_range.start).days, 1)
        if len(request.date_range.dates) < 2:
            return self._result(
                request,
                status=AgentStatus.UNAVAILABLE,
                payload={"hotels": [], "nights": 0},
                notes=["This is a one-day trip, so no overnight hotel is included."],
                failures=[DataGap(section="hotels", kind=FailureKind.NO_DATA, message="No overnight stay")],
                started=started,
            )
        candidates: list[HotelCandidate] = []
        for source in self.registry.hotels:
            if not getattr(source, "available", True):
                continue
            try:
                results = await self._call(
                    request,
                    "hotels.search",
                    source.search,
                    destination=request.context.destination,
                    check_in=request.date_range.start,
                    check_out=request.date_range.end,
                    party_size=request.context.party_size or 1,
                    budget=request.context.budget,
                    currency=request.context.currency,
                    timeout=max(self.settings.agent_timeout_seconds, 20.0),
                )
                if results:
                    candidates = results
                    sources.extend(ref for hotel in results for ref in hotel.source_refs)
                    break
            except (ProviderError, asyncio.TimeoutError, Exception) as exc:
                failures.append(_gap("hotels", exc))

        for hotel in candidates:
            if hotel.geo:
                hotel.distance_to_centre_km = round(
                    haversine_km(
                        request.destination.geo.lat,
                        request.destination.geo.lng,
                        hotel.geo.lat,
                        hotel.geo.lng,
                    ),
                    2,
                )
        candidates.sort(
            key=lambda h: (
                (h.rating or 0) - (h.distance_to_centre_km or 2) * 0.25,
                -(h.nightly_price.amount if h.nightly_price else 10**9),
            ),
            reverse=True,
        )
        priced = [hotel for hotel in candidates if hotel.nightly_price is not None]
        web_references: list[dict[str, Any]] = []
        if not priced:
            web_references, web_gap = await self._web_references(
                section="hotel",
                city=request.context.destination,
                query=f"hotels in {request.context.destination}",
                official_empty=True,
                toolbox=request.toolbox,
            )
            if web_gap is not None:
                failures.append(web_gap)
        payload = {
            "hotels": [
                {"id": f"h{index + 1}", **hotel.model_dump(mode="json")} for index, hotel in enumerate(candidates[:5])
            ],
            "nights": nights,
            "check_in": request.date_range.start.isoformat(),
            "check_out": request.date_range.end.isoformat(),
            "web_references": web_references,
        }
        status = AgentStatus.OK if candidates else AgentStatus.UNAVAILABLE
        notes = [f"{len(candidates)} hotel candidates; prices are reference rates."] if candidates else [
            "No hotel source returned usable data."
        ]
        return self._result(
            request,
            status=status,
            payload=payload,
            notes=notes,
            sources=sources,
            failures=failures,
            started=started,
        )


class WeatherAgent(BaseAgent):
    name = AgentName.WEATHER
    tools = frozenset({"weather.forecast"})

    async def run(self, request: AgentContext) -> AgentResult:
        started = time.perf_counter()
        failures: list[DataGap] = []
        sources: list[SourceRef] = []
        days: list[dict[str, Any]] = []
        fallback_days: list[dict[str, Any]] = []
        fallback_source: SourceRef | None = None
        for source in self.registry.weather:
            if not getattr(source, "available", True):
                continue
            try:
                results = await self._call(
                    request,
                    "weather.forecast",
                    source.forecast,
                    geo=request.destination,
                    start=request.date_range.start,
                    end=request.date_range.end,
                )
                if results:
                    rows = [day.model_dump(mode="json") for day in results]
                    ref = SourceRef(
                        title=f"{source.name} weather",
                        url=(
                            "https://open-meteo.com/en/docs/historical-weather-api"
                            if source.name == "open-meteo-archive"
                            else "https://open-meteo.com/"
                        ),
                        source=source.name,
                        fetched_at=utcnow(),
                    )
                    if any(row.get("status") == DataStatus.OK.value for row in rows):
                        days = rows
                        sources.append(ref)
                        break
                    has_stale = any(row.get("status") == DataStatus.STALE.value for row in rows)
                    fallback_has_stale = any(
                        row.get("status") == DataStatus.STALE.value for row in fallback_days
                    )
                    if not fallback_days or (has_stale and not fallback_has_stale):
                        fallback_days = rows
                        fallback_source = ref
            except (ProviderError, asyncio.TimeoutError, Exception) as exc:
                failures.append(_gap("weather", exc))

        if not days and fallback_days:
            days = fallback_days
            if fallback_source:
                sources.append(fallback_source)

        available = sum(1 for day in days if day.get("status") == DataStatus.OK.value)
        stale = sum(1 for day in days if day.get("status") == DataStatus.STALE.value)
        usable = available + stale
        if not days:
            status = AgentStatus.UNAVAILABLE
        elif available == len(days):
            status = AgentStatus.OK
        elif usable:
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.UNAVAILABLE
        if not days:
            failures = [
                DataGap(
                    section="weather",
                    kind=FailureKind.NO_DATA,
                    message=(
                        f"No weather data for {request.date_range.start} to {request.date_range.end}. "
                        "These dates are outside the 16-day forecast window and the seasonal "
                        "archive returned no usable rows."
                    ),
                )
            ]
        elif usable and not stale and available < len(days):
            failures.append(
                DataGap(
                    section="weather",
                    kind=FailureKind.NO_DATA,
                    message=(
                        f"{len(days) - available} of {len(days)} days are outside the 16-day "
                        "forecast window; no seasonal reference was returned for them."
                    ),
                )
            )
        note = f"{available} of {len(days)} dates have a forecast."
        if stale:
            note = f"{stale} of {len(days)} dates use a labelled seasonal reference, not a forecast."
        web_references: list[dict[str, Any]] = []
        if available == 0:
            month = request.date_range.start.strftime("%B")
            web_references, web_gap = await self._web_references(
                section="weather",
                city=request.context.destination,
                query=f"typical weather in {request.context.destination} in {month}",
                official_empty=not days,
                toolbox=request.toolbox,
            )
            if web_gap is not None:
                failures.append(web_gap)
        return self._result(
            request,
            status=status,
            payload={"days": days, "web_references": web_references},
            notes=[note],
            sources=sources,
            failures=failures,
            started=started,
        )


class TicketAgent(BaseAgent):
    name = AgentName.TICKET
    tools = frozenset({"flights.search"})

    async def run(self, request: AgentContext) -> AgentResult:
        started = time.perf_counter()
        failures: list[DataGap] = []
        sources: list[SourceRef] = []
        flights: list[dict[str, Any]] = []
        notes: list[str] = []

        if not request.context.origin:
            # A missing origin means flights were not requested, not that a source
            # failed. Keep it as a note rather than a data gap.
            notes.append("No origin city set; flight comparison was skipped.")
        elif request.context.origin.strip().casefold() == request.context.destination.strip().casefold():
            notes.append("Origin and destination are the same city; flights were skipped.")
        elif not self.settings.enable_flights:
            notes.append("Flight comparison is disabled for this deployment.")
        else:
            for source in self.registry.flights:
                if not getattr(source, "available", True):
                    continue
                try:
                    results = await self._call(
                        request,
                        "flights.search",
                        source.search,
                        origin=request.context.origin,
                        destination=request.context.destination,
                        depart_date=request.date_range.start,
                        return_date=request.date_range.end,
                        party_size=request.context.party_size or 1,
                        currency=request.context.currency,
                        timeout=max(self.settings.agent_timeout_seconds, 25.0),
                    )
                    if results:
                        flights = [flight.model_dump(mode="json") for flight in results]
                        sources.extend(ref for flight in results for ref in flight.source_refs)
                        break
                except (ProviderError, asyncio.TimeoutError, Exception) as exc:
                    failures.append(_gap("tickets", exc))

        web_references: list[dict[str, Any]] = []
        searched = bool(request.context.origin) and request.context.origin.strip().casefold() != request.context.destination.strip().casefold() and self.settings.enable_flights
        if searched and not flights:
            web_references, web_gap = await self._web_references(
                section="flight",
                city=request.context.destination,
                query=f"{request.context.origin} to {request.context.destination} flights",
                official_empty=True,
                toolbox=request.toolbox,
            )
            if web_gap is not None:
                failures.append(web_gap)
        notes.append("Rail schedules are not connected to a live source in this build; fares and times need operator confirmation.")
        # Empty because it was skipped/optional is PARTIAL, not a failed source.
        status = AgentStatus.OK if flights else AgentStatus.PARTIAL
        if flights:
            notes.insert(0, f"{len(flights)} flight options with reference fares.")
        return self._result(
            request,
            status=status,
            payload={
                "flights": [{"id": f"f{index + 1}", **flight} for index, flight in enumerate(flights[:4])],
                "web_references": web_references,
            },
            notes=notes,
            sources=sources,
            failures=failures,
            started=started,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _gap(section: str, exc: Exception) -> DataGap:
    if isinstance(exc, ProviderError):
        return DataGap(
            section=section,
            kind=exc.kind,
            message=f"{exc.source or 'source'}: {exc}",
            retryable=exc.retryable,
        )
    if isinstance(exc, asyncio.TimeoutError):
        return DataGap(section=section, kind=FailureKind.TIMEOUT, message="source exceeded its deadline", retryable=True)
    return DataGap(section=section, kind=FailureKind.SERVER_ERROR, message=str(exc)[:200])


def _norm_name(name: str) -> str:
    """A comparable place name in any script: accents, punctuation and case dropped.

    "Sensō-ji" and "Senso-ji" both become "sensoji"; 伏見稲荷大社 keeps its
    characters (an ASCII-only filter turned every Japanese name into "", so two
    different temples compared as identical).
    """
    text = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", name or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]|_", "", text)).strip()


def _place_names(place: PlaceCandidate) -> set[str]:
    return {norm for norm in (_norm_name(value) for value in [place.name, *place.aliases]) if norm}


_CHAIN_CATEGORIES = {"restaurant", "cafe", "food", "bar"}


def _same_place(existing: PlaceCandidate, place: PlaceCandidate) -> bool:
    """Whether two records describe one place. Names are compared with aliases, so
    伏見稲荷大社 meets Fushimi Inari Taisha through either source's other name.

    - the same name: one place unless far apart (10 km for a landmark, where a
      source's coordinate is wrong; 1 km for a restaurant, which may be a chain);
    - nearly the same name ("Kinkaku-ji" / "Ginkaku-ji" are 0.89 alike): within 1 km,
      so both records need coordinates;
    - a looser match or one name inside the other ("Senso-ji" / "Senso-ji Temple"):
      within 300 m.
    """
    names_a, names_b = _place_names(existing), _place_names(place)
    if not names_a or not names_b:
        return False
    if existing.geo and place.geo:
        km: float | None = haversine_km(existing.geo.lat, existing.geo.lng, place.geo.lat, place.geo.lng)
    else:
        km = None
    near = km is not None and km <= 1.0  # an unplaced record merges on an exact name only
    close = km is not None and km < 0.3
    if names_a & names_b:
        chain = {existing.category.lower(), place.category.lower()} & _CHAIN_CATEGORIES
        return km is None or km <= (1.0 if chain else 10.0)
    ratio = max(SequenceMatcher(None, a, b).ratio() for a in names_a for b in names_b)
    inside = any(set(a.split()) < set(b.split()) or set(b.split()) < set(a.split()) for a in names_a for b in names_b)
    return (ratio > 0.86 and near) or ((ratio > 0.7 or inside) and close)


def _agreed_geo(points: list[Any]) -> Any:
    """Where sources disagree, the point most others agree with (within 0.5 km); ties keep
    the first, i.e. the higher-priority source."""
    if len(points) < 3:
        return points[0] if points else None
    support = [
        sum(1 for other in points if haversine_km(point.lat, point.lng, other.lat, other.lng) <= 0.5)
        for point in points
    ]
    return points[support.index(max(support))]


def _merge_places(places: list[PlaceCandidate]) -> list[PlaceCandidate]:
    merged: list[PlaceCandidate] = []
    points: dict[int, list[Any]] = {}
    for place in places:
        match = next((existing for existing in merged if _same_place(existing, place)), None)
        if match is None:
            merged.append(place)
            points[id(place)] = [place.geo] if place.geo else []
            continue
        # Keep the richer record, merge provenance and the other names.
        if len(place.description) > len(match.description):
            match.description = place.description
        match.rating = match.rating if match.rating is not None else place.rating
        match.review_count = match.review_count or place.review_count
        match.address = match.address or place.address
        match.website = match.website or place.website
        match.geo = match.geo or place.geo
        match.opening_hours = match.opening_hours or place.opening_hours
        match.source_refs = _dedupe_sources(match.source_refs + place.source_refs)
        for alias in [place.name, *place.aliases]:
            if alias and alias != match.name and alias not in match.aliases:
                match.aliases.append(alias)
        if place.geo:
            points[id(match)].append(place.geo)
    for place in merged:
        place.geo = _agreed_geo(points[id(place)]) or place.geo
    return merged


def _dedupe_sources(sources: list[SourceRef]) -> list[SourceRef]:
    seen: set[str] = set()
    out: list[SourceRef] = []
    for ref in sources:
        key = ref.url or f"{ref.source}:{ref.title}"
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out
