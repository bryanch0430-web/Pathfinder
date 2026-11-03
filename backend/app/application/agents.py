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
from app.domain.errors import ProviderError, ToolBudgetExceeded
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
    NoteItem,
    PlaceCandidate,
    SourceRef,
    TripContext,
    utcnow,
)
from app.infrastructure.http import classify_exception, haversine_km


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
    #: What the agent has found so far. The runner keeps it, so an agent cut off by the
    #: pre-planning budget still returns what it had. A row is a located place (its JSON), or
    #: `{"meal": ...}`, `{"note": ...}` or `{"wish": ...}` for what `AttractionAgent` records besides.
    progress: list[dict[str, Any]] | None = None


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
            tracer=getattr(toolbox, "tracer", None),
        )
        if not official_empty:
            return found.references, None
        if found.found:
            return found.references, inadequate_gap(section, city)
        return found.references, found.gap


#: The most rows an agent leaves in its progress. A real run records about forty at most; the
#: bound keeps a runaway agent from making the partial result slow to build.
MAX_PROGRESS_ROWS = 60

#: Lookups in a row that may fail before the agent stops asking the geocoder. Each can take the
#: whole lookup timeout, so asking a dead service about every guide place would outlast the
#: pre-planning budget before its failure was ever reported.
GEOCODER_FAILURE_LIMIT = 2


@dataclass
class Located:
    """What geocoding the places of one run came to."""

    #: Asked, and answered with nothing or with a place too far from the destination.
    unplaced: list[str] = field(default_factory=list)
    #: Not asked, each with the failure that explains it: the geocoder call failed, or it had
    #: failed too often in a row to be asked again.
    unchecked: dict[str, ProviderError] = field(default_factory=dict)
    #: Lookups that failed since the geocoder last answered, and the latest of those failures.
    streak: int = 0
    latest: ProviderError | None = None

    def stopped(self) -> ProviderError | None:
        """The failure that made the agent stop asking, once the geocoder failed too often in a row."""
        return self.latest if self.streak >= GEOCODER_FAILURE_LIMIT else None


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
                    self._keep(request, [place for place in places if place.geo is not None])
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
        restaurants = [r for r in restaurants if r.geo is not None][:6]
        self._record(request, [{"meal": restaurant.model_dump(mode="json")} for restaurant in restaurants])

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
                    self._record(request, [{"note": notes[-1]}])
                sources.extend(
                    SourceRef(title=note.title, url=note.url, source=note.source, fetched_at=note.fetched_at)
                    for note in results
                )
            except (ProviderError, asyncio.TimeoutError, Exception):
                # Community notes are an optional enrichment, not a required plan
                # section. A Bilibili rate-limit must not become a visible data gap.
                continue

        # Every guide entry without coordinates is geocoded. One the geocoder
        # cannot place near the destination is left out, never pinned to a guess.
        missing = sum(1 for place in places if place.geo is None)
        located = Located()
        await self._locate(request, places, located)
        if missing:
            # Two spellings of one place may only meet once both have coordinates.
            places = _merge_places([place for place in places if place.geo is not None])

        # A place the traveller named is searched by name, so a generic
        # "attractions in X" list that misses it cannot drop it.
        wishes: dict[str, str | None] = {}
        #: Why a wish is None when the cause is a failed lookup, not a place nobody knows: the plan's
        #: check for it says so. `lookups` are the ones the place sources failed on (the gap below);
        #: a place found but not placed is already a geocoder gap.
        wish_failures: dict[str, str] = {}
        lookups: dict[str, ProviderError] = {}
        wished: list[PlaceCandidate] = []
        for wish in place_wishes(request.context):
            match, failure = await self._find_wish(request, wish)
            if match is not None and match.geo is None:
                # A place already located above is not geocoded a second time.
                known = next((place for place in places if _same_place(place, match)), None)
                match.geo = known.geo if known is not None else None
            if match is not None:
                await self._locate(request, [match], located)
                if match.geo is None:
                    failure = located.unchecked.get(match.name)
                    match = None
            elif failure is not None:
                lookups[wish] = failure
            wishes[wish] = match.name if match else None
            if match is None and failure is not None:
                wish_failures[wish] = _lookup_evidence(failure)
            if match:
                match.tags = ["requested", *match.tags]
                wished.append(match)
                sources.extend(match.source_refs)
            self._keep_wish(request, wish, match, wish_failures.get(wish))

        places = _choose_places(places, wished)

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
            "wish_failures": wish_failures,
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
            # The summary the agent's chip shows: a guide that answered but whose places the map could
            # not place or check is not "no source".
            named = len(located.unplaced) + len(located.unchecked)
            if located.unplaced:
                said = f"The guide named {_noun(named, 'place')}, but none could be located on the map."
                failures.append(DataGap(section="attractions", kind=FailureKind.NO_DATA, message=said))
                note_text = [said]
            elif located.unchecked:
                note_text = [f"{_noun(named, 'guide place')} could not be checked on the map."]
            else:
                note_text = ["No attraction source returned usable data."]
        if located.unplaced:
            note_text.append(
                f"Left out {_noun(len(located.unplaced), 'guide place')} the geocoder could not locate: "
                + ", ".join(located.unplaced[:5])
            )
        if located.unchecked:
            # The geocoder did not say these places are unknown; it could not be asked.
            note_text.append(
                f"Left out {_noun(len(located.unchecked), 'guide place')} that could not be checked: "
                + ", ".join(list(located.unchecked)[:5])
            )
            failures.extend(_failure_gaps(located.unchecked, lambda names, error: _unchecked_message(len(names), error)))
        failures.extend(_failure_gaps(lookups, _lookup_message))
        return self._result(
            request,
            status=status,
            payload=payload,
            notes=note_text,
            sources=sources,
            failures=failures,
            started=started,
        )

    async def _locate(self, request: AgentContext, places: list[PlaceCandidate], found: Located) -> None:
        """Geocode each place without coordinates (a logged tool call per place), recording the outcome in `found`.

        `unplaced` are the names the geocoder answered for with nothing, or with a place
        farther from the destination than a guide listing may be. `unchecked` are the names
        it could not be asked about because the call failed, or because it had just failed
        GEOCODER_FAILURE_LIMIT times in a row, each with its error. A name is in one of them
        once, and in neither after a later lookup locates it.
        """
        centre = request.destination.geo
        for place in places:
            if place.geo is not None:
                continue
            stopped = found.stopped()
            if stopped is not None:
                found.unchecked.setdefault(place.name, stopped)
                continue
            try:
                location = await self._call(
                    request,
                    "geocode",
                    self.registry.geocoder.geocode,
                    name=f"{place.name}, {request.context.destination}",
                    timeout=10.0,
                )
            except Exception as exc:
                error = classify_exception(exc, "geocoder")
                if error.kind is not FailureKind.NO_DATA:
                    found.unchecked.setdefault(place.name, error)
                    found.latest = error
                    found.streak += 1
                    continue
                location = None  # a no-result answer: the geocoder knows no such place. It did not fail.
            found.streak = 0
            # 80 km: the radius the guide source keeps a listing within.
            if location is not None and haversine_km(centre.lat, centre.lng, location.geo.lat, location.geo.lng) <= 80:
                place.geo = location.geo
                self._keep(request, [place])
                # Asked about again (a place the traveller named): an earlier miss no longer stands.
                found.unchecked.pop(place.name, None)
                if place.name in found.unplaced:
                    found.unplaced.remove(place.name)
            else:
                found.unchecked.pop(place.name, None)
                if place.name not in found.unplaced:
                    found.unplaced.append(place.name)

    @staticmethod
    def _record(request: AgentContext, rows: list[dict[str, Any]]) -> None:
        """Add rows to the progress the runner reads if the budget cuts this agent off (up to MAX_PROGRESS_ROWS)."""
        if request.progress is not None:
            request.progress.extend(rows[: max(MAX_PROGRESS_ROWS - len(request.progress), 0)])

    @classmethod
    def _keep(cls, request: AgentContext, places: list[PlaceCandidate]) -> None:
        """Record located places where the runner can find them if the budget cuts this agent off."""
        cls._record(request, [place.model_dump(mode="json") for place in places])

    @classmethod
    def _keep_wish(cls, request: AgentContext, wish: str, match: PlaceCandidate | None, failure: str | None) -> None:
        """Record what became of a place the traveller named, and the place itself (tagged) when it was found."""
        rows: list[dict[str, Any]] = [{"wish": wish, "found_as": match.name if match else None, "failure": failure}]
        if match is not None:
            rows.append(match.model_dump(mode="json"))
        cls._record(request, rows)

    def partial_result(
        self, progress: list[dict[str, Any]], *, centre: Any, elapsed_ms: int, context: TripContext | None = None
    ) -> AgentResult | None:
        """What the agent had found when the pre-planning budget ran out; None when no place was located.

        It is what a run that ended there would have returned: the located places, the dining
        candidates and community notes already fetched, and an answer for each place the traveller
        named. A place the agent had not got to is reported as not checked, with the budget as the reason.
        """
        located: list[PlaceCandidate] = []
        meals: list[PlaceCandidate] = []
        notes: list[dict[str, Any]] = []
        looked_up: dict[str, dict[str, Any]] = {}
        for row in progress:
            if "meal" in row:
                meals.append(PlaceCandidate.model_validate(row["meal"]))
            elif "note" in row:
                notes.append(row["note"])
            elif "wish" in row:
                looked_up[row["wish"]] = row
            else:
                located.append(PlaceCandidate.model_validate(row))
        wished = [place for place in located if "requested" in place.tags]
        rest = _merge_places([place for place in located if place.geo and "requested" not in place.tags])
        found = [place for place in _choose_places(rest, wished) if place.geo]
        if not found:
            return None
        meals = [meal for meal in _merge_places(meals) if meal.geo][:6]
        wishes: dict[str, str | None] = {}
        wish_failures: dict[str, str] = {}
        for wish in place_wishes(context) if context is not None else list(looked_up):
            row = looked_up.get(wish)
            if row is None:
                budget = ProviderError("the pre-planning time budget ran out", kind=FailureKind.TIMEOUT, source=self.name.value)
                wishes[wish], wish_failures[wish] = None, _lookup_evidence(budget)
                continue
            wishes[wish] = row.get("found_as")
            if row.get("failure"):
                wish_failures[wish] = row["failure"]
        kept = f"{len(found)} attractions" + (f" and {len(meals)} dining candidates" if meals else "")
        note_sources = [NoteItem.model_validate(note) for note in notes]
        return AgentResult(
            agent=self.name,
            status=AgentStatus.PARTIAL,
            payload={
                "places": [{"id": f"p{index + 1}", **place.model_dump(mode="json")} for index, place in enumerate(found)],
                "meals": [{"id": f"m{index + 1}", **meal.model_dump(mode="json")} for index, meal in enumerate(meals)],
                "notes": notes,
                "centre": centre.model_dump() if centre is not None else None,
                "web_references": [],
                "wishes": wishes,
                "wish_failures": wish_failures,
            },
            notes=[f"{kept} were located before the pre-planning time budget ran out."],
            sources=_dedupe_sources(
                [ref for place in [*found, *meals] for ref in place.source_refs]
                + [SourceRef(title=n.title, url=n.url, source=n.source, fetched_at=n.fetched_at) for n in note_sources]
            ),
            failures=[
                DataGap(
                    section="attractions",
                    kind=FailureKind.TIMEOUT,
                    message=f"The attraction search ran out of its time budget; the {len(found)} places located so far are kept.",
                    retryable=True,
                )
            ],
            elapsed_ms=elapsed_ms,
            partial=True,
        )

    async def _find_wish(
        self, request: AgentContext, wish: str
    ) -> tuple[PlaceCandidate | None, ProviderError | None]:
        """The place a traveller named, or None when no source answers with that name.

        When none does and a source failed, the first failure comes with the None: the place
        may exist, and the plan must not say that no source knows it.
        """
        failure: ProviderError | None = None
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
            except Exception as exc:
                error = classify_exception(exc, getattr(source, "name", "places"))
                if error.kind is not FailureKind.NO_DATA:  # a no-result answer is not a failure
                    failure = failure or error
                continue
            trusted = getattr(source, "ranks_by_relevance", False)
            for index, place in enumerate(results or []):
                if wish_matches(place, wish) or (trusted and index == 0):
                    return place, None
        return None, failure


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
        if not days and not failures:
            # A source that failed already said why (a timeout, a rate limit); this explains an empty answer.
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
    if isinstance(exc, ToolBudgetExceeded):
        # Our own limit, not a source's failure: there is no source to name.
        return DataGap(section=section, kind=exc.kind, message=str(exc), retryable=False)
    if isinstance(exc, ProviderError):
        return DataGap(
            section=section,
            kind=exc.kind,
            message=f"{exc.source or 'source'}: {exc}",
            retryable=exc.retryable,
        )
    if isinstance(exc, asyncio.TimeoutError):
        return DataGap(section=section, kind=FailureKind.TIMEOUT, message="source exceeded its deadline", retryable=True)
    # An unexpected exception's own text can hold anything; the gap says only that the source failed.
    return DataGap(section=section, kind=FailureKind.SERVER_ERROR, message="source failed unexpectedly")


def _lookup_evidence(failure: ProviderError) -> str:
    """What the plan's check for a named place says when the lookup failed rather than found nothing."""
    if failure.kind is FailureKind.BLOCKED:
        return "This turn reached its limit on data lookups, so this place was not checked."
    text = f"The place lookup failed ({failure.kind.value}), so this place was not checked."
    return f"{text} Ask again to retry." if failure.retryable else text


def _noun(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _lookup_message(names: list[str], failure: ProviderError) -> str:
    """Why places the traveller named could not be looked up, and whether asking again could help."""
    subject = f"The place you named, {names[0]}," if len(names) == 1 else f"The places you named, {', '.join(names[:4])},"
    if failure.kind is FailureKind.BLOCKED:
        return f"{subject} could not be looked up because this turn reached its limit on data lookups."
    cause = f"{subject} could not be looked up because a place source failed ({failure.kind.value})"
    return f"{cause}; ask again to retry." if failure.retryable else f"{cause}."


def _failure_gaps(failed: dict[str, ProviderError], describe: Callable[[list[str], ProviderError], str]) -> list[DataGap]:
    """One gap for each distinct failure among `failed`, worded by `describe` for the names it hit."""
    groups: dict[tuple[FailureKind, bool], list[str]] = {}
    first: dict[tuple[FailureKind, bool], ProviderError] = {}
    for name, error in failed.items():
        key = (error.kind, error.retryable)
        groups.setdefault(key, []).append(name)
        first.setdefault(key, error)
    return [
        DataGap(section="attractions", kind=kind, message=describe(names, first[(kind, retryable)]), retryable=retryable)
        for (kind, retryable), names in groups.items()
    ]


def _unchecked_message(count: int, failure: ProviderError) -> str:
    """Why `count` guide places could not be checked: what failed, and whether asking again could help."""
    left = f"{_noun(count, 'guide place')} could not be checked on the map"
    if failure.kind is FailureKind.BLOCKED:
        # This turn's own cap on lookups was spent; the geocoder was never asked.
        return f"{left} because this turn reached its limit on data lookups."
    cause = f"{left} because the geocoder failed ({failure.kind.value})"
    return f"{cause}; ask again to retry." if failure.retryable else f"{cause}."


def _choose_places(places: list[PlaceCandidate], wished: list[PlaceCandidate]) -> list[PlaceCandidate]:
    """The places that go to the planner: the ten best rated with coordinates, the ones the traveller named first."""
    places = sorted(places, key=lambda p: (p.rating or 0, p.review_count or 0), reverse=True)
    places = [p for p in places if p.geo is not None][:10] or places[:10]
    if wished:
        places = _merge_places(wished + places)[: max(10, len(wished))]
    return places


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
        for locale, localized in place.names.items():
            match.names.setdefault(locale, localized)
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
