"""ToolRegistry: maps a typed ToolRequest to the provider that serves it.

Maps uses an ordered fallback chain (proposal: "maps with Google and AMap as fallback"): try each
provider in order and fall through to the next on any ProviderError except ProviderBadRequest;
if all fail, re-raise the last error. A bad request is re-raised at once because the next
provider would reject the same malformed request too — it goes back to the model for repair.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from typing import TypeVar

from backend.schemas.tools import (
    ForecastPayload,
    ForecastRequest,
    GeocodePayload,
    GeocodeRequest,
    GeocodeResult,
    PlacesPayload,
    PlacesSearchRequest,
    ReservationCheckRequest,
    ReservationsPayload,
    SearchPayload,
    TicketSearchRequest,
    TicketsPayload,
    ToolPayload,
    ToolRequest,
    WebSearchRequest,
)
from backend.tools.providers.base import (
    MapsProvider,
    PlacesProvider,
    ProviderBadRequest,
    ProviderError,
    ProviderTimeout,
    SearchProvider,
    TicketProvider,
    WeatherProvider,
)

_T = TypeVar("_T")


class ToolRegistry:
    def __init__(
        self,
        *,
        search: SearchProvider,
        maps: Sequence[MapsProvider],
        weather: WeatherProvider,
        places: PlacesProvider,
        tickets: TicketProvider,
        fallback_timeout_s: float | None = None,
    ) -> None:
        """`fallback_timeout_s` (optional) bounds each provider in the maps chain, so a hanging
        primary falls through to the fallback instead of consuming the whole attempt."""
        if not maps:
            raise ValueError("at least one maps provider is required")
        self._search = search
        self._maps: tuple[MapsProvider, ...] = tuple(maps)
        self._weather = weather
        self._places = places
        self._tickets = tickets
        self._fallback_timeout_s = fallback_timeout_s

    @property
    def maps_chain(self) -> tuple[str, ...]:
        """Provider names of the maps fallback chain, in order."""
        return tuple(p.name for p in self._maps)

    async def execute(self, request: ToolRequest) -> tuple[ToolPayload, str]:
        """Run the request on its provider. Returns (payload, provider_name). Raises
        `ProviderError` subclasses unchanged for the gateway to classify."""
        if isinstance(request, WebSearchRequest):
            hits = await self._search.search(request)
            return SearchPayload(hits=list(hits)), self._search.name
        if isinstance(request, GeocodeRequest):
            result, name = await self._geocode(request)
            return GeocodePayload(result=result), name
        if isinstance(request, ForecastRequest):
            days = await self._weather.forecast(request)
            return ForecastPayload(days=list(days)), self._weather.name
        if isinstance(request, PlacesSearchRequest):
            places = await self._places.search(request)
            return PlacesPayload(places=list(places)), self._places.name
        if isinstance(request, TicketSearchRequest):
            tickets = await self._tickets.search(request)
            return TicketsPayload(tickets=list(tickets)), self._tickets.name
        if isinstance(request, ReservationCheckRequest):
            reservations = await self._tickets.reservations(request)
            return ReservationsPayload(reservations=list(reservations)), self._tickets.name
        raise TypeError(f"unsupported tool request type: {type(request).__name__}")

    async def _geocode(self, request: GeocodeRequest) -> tuple[GeocodeResult, str]:
        last_error: ProviderError | None = None
        chained = len(self._maps) > 1
        for provider in self._maps:
            try:
                result = await self._bounded(provider.geocode(request), provider.name, chained)
            except ProviderBadRequest:
                raise
            except ProviderError as exc:
                last_error = exc
                continue
            return result, provider.name
        assert last_error is not None  # the loop ran at least once (maps is non-empty)
        raise last_error

    async def _bounded(self, call: Awaitable[_T], name: str, chained: bool) -> _T:
        if not chained or self._fallback_timeout_s is None:
            return await call
        try:
            return await asyncio.wait_for(call, timeout=self._fallback_timeout_s)
        except TimeoutError as exc:
            raise ProviderTimeout(f"{name} did not answer within {self._fallback_timeout_s}s") from exc
