"""Provider interfaces for the five tool families.

Every concrete third-party vendor (Google Maps, AMap, a weather API, a ticket API, a web search
API) is provisional; agents never see a vendor, only these protocols behind the ToolGateway.
Providers raise the typed `ProviderError` subclasses below so the gateway can classify a failure
BEFORE deciding whether to retry.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

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
    WebSearchRequest,
)


class ProviderError(Exception):
    """Base class for every provider failure."""


class ProviderTimeout(ProviderError):
    """The provider did not answer in time. Transient: retried with backoff + jitter."""


class ProviderRateLimited(ProviderError):
    """HTTP 429 or equivalent. Transient: retried with backoff + jitter (honouring retry_after)."""

    def __init__(self, message: str = "rate limited", retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ProviderServerError(ProviderError):
    """5xx or a malformed provider response. Transient: retried with backoff + jitter."""


class ProviderBadRequest(ProviderError):
    """The provider rejected the request as invalid. NOT retried unchanged: returned to the
    model as a structured validation error for bounded repair."""

    def __init__(self, issues: list[FieldIssue]) -> None:
        super().__init__("; ".join(f"{i.loc}: {i.message}" for i in issues) or "bad request")
        self.issues = issues


class ProviderUnavailable(ProviderError):
    """Permanent for this turn (auth failure, not found, provider down). Not retried."""


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    async def search(self, request: WebSearchRequest) -> list[SearchHit]: ...


@runtime_checkable
class MapsProvider(Protocol):
    name: str

    async def geocode(self, request: GeocodeRequest) -> GeocodeResult: ...


@runtime_checkable
class WeatherProvider(Protocol):
    name: str

    async def forecast(self, request: ForecastRequest) -> list[ForecastDay]: ...


@runtime_checkable
class PlacesProvider(Protocol):
    name: str

    async def search(self, request: PlacesSearchRequest) -> list[PlaceRecord]: ...


@runtime_checkable
class TicketProvider(Protocol):
    name: str

    async def search(self, request: TicketSearchRequest) -> list[TicketRecord]: ...

    async def reservations(self, request: ReservationCheckRequest) -> list[ReservationRecord]: ...
