"""Build the ToolGateway from settings.

Only the mock providers are concrete; every real vendor selection raises NotImplementedError
marked TODO(provisional) at build time (team decision 5), so a misconfiguration fails loudly at
startup instead of surfacing later as a retried "server fault" on every tool call.
"""

from __future__ import annotations

import asyncio
import random

from backend.observability.recorder import Observability
from backend.security.audit import SecurityAudit
from backend.settings import Settings
from backend.tools.allowlist import ToolAllowlist
from backend.tools.gateway import Sleep, ToolGateway
from backend.tools.providers.base import (
    MapsProvider,
    PlacesProvider,
    SearchProvider,
    TicketProvider,
    WeatherProvider,
)
from backend.tools.providers.mock import MockProviders, build_mock_providers
from backend.tools.providers.mock.faults import FaultPlan
from backend.tools.registry import ToolRegistry


def _provisional(what: str, selection: str) -> NotImplementedError:
    return NotImplementedError(
        f"{what} provider {selection!r} is not implemented yet "
        f"(TODO(provisional): only 'mock' is concrete; see backend/tools/providers/)"
    )


def _maps_chain(settings: Settings, mock: MockProviders) -> list[MapsProvider]:
    chain: list[MapsProvider] = []
    for selection in settings.maps_providers:
        if selection != "mock":
            # TODO(provisional): GoogleMapsProvider / AMapProvider (backend/tools/providers/).
            raise _provisional("maps", selection)
        for provider in mock.maps:  # "mock" stands for the whole mock chain google -> amap
            if provider not in chain:
                chain.append(provider)
    if not chain:
        raise ValueError("settings.maps_providers must name at least one provider")
    return chain


def build_tool_gateway(
    settings: Settings,
    observability: Observability,
    audit: SecurityAudit,
    *,
    faults: FaultPlan | None = None,
    sleep: Sleep | None = None,
    rng: random.Random | None = None,
) -> ToolGateway:
    mock = build_mock_providers(faults)

    if settings.search_provider != "mock":
        # TODO(provisional): WebSearchProvider (e.g. Xiaohongshu notes search).
        raise _provisional("search", settings.search_provider)
    search: SearchProvider = mock.search
    if settings.weather_provider != "mock":
        # TODO(provisional): LiveWeatherProvider.
        raise _provisional("weather", settings.weather_provider)
    weather: WeatherProvider = mock.weather
    if settings.places_provider != "mock":
        # TODO(provisional): GooglePlacesProvider.
        raise _provisional("places", settings.places_provider)
    places: PlacesProvider = mock.places
    if settings.ticket_provider != "mock":
        # TODO(provisional): LiveTicketProvider.
        raise _provisional("ticket", settings.ticket_provider)
    tickets: TicketProvider = mock.tickets
    maps = _maps_chain(settings, mock)

    registry = ToolRegistry(
        search=search,
        maps=maps,
        weather=weather,
        places=places,
        tickets=tickets,
        # Split one attempt's timeout across the chain so a hanging primary still leaves the
        # fallback time to answer inside the same attempt.
        fallback_timeout_s=settings.tool_call_timeout_s / len(maps) if len(maps) > 1 else None,
    )
    return ToolGateway(
        registry=registry,
        allowlist=ToolAllowlist(),
        settings=settings,
        observability=observability,
        audit=audit,
        sleep=sleep if sleep is not None else asyncio.sleep,
        rng=rng,
    )
