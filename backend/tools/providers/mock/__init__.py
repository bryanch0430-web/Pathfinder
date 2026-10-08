"""Offline mock providers (the only concrete providers; every vendor is provisional)."""

from __future__ import annotations

from backend.tools.providers.mock.faults import FaultKind, FaultPlan
from backend.tools.providers.mock.providers import (
    MockMapsProvider,
    MockPlacesProvider,
    MockProviders,
    MockSearchProvider,
    MockTicketProvider,
    MockWeatherProvider,
    build_mock_providers,
)

__all__ = [
    "FaultKind",
    "FaultPlan",
    "MockMapsProvider",
    "MockPlacesProvider",
    "MockProviders",
    "MockSearchProvider",
    "MockTicketProvider",
    "MockWeatherProvider",
    "build_mock_providers",
]
