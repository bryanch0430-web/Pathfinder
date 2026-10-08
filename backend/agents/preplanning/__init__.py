"""The four pre-planning agents (Appendix A: attractions, hotels, weather, tickets)."""

from __future__ import annotations

from backend.agents.preplanning.attraction import AttractionAgent
from backend.agents.preplanning.base import PreplanningAgent
from backend.agents.preplanning.hotel import HotelAgent
from backend.agents.preplanning.ticket import TicketAgent
from backend.agents.preplanning.weather import WeatherAgent
from backend.agents.runtime.deps import RuntimeDeps
from backend.schemas.common import AgentName


def build_agents(deps: RuntimeDeps) -> dict[AgentName, PreplanningAgent]:
    return {
        AgentName.ATTRACTION: AttractionAgent(deps),
        AgentName.HOTEL: HotelAgent(deps),
        AgentName.WEATHER: WeatherAgent(deps),
        AgentName.TICKET: TicketAgent(deps),
    }


__all__ = [
    "AttractionAgent",
    "HotelAgent",
    "PreplanningAgent",
    "TicketAgent",
    "WeatherAgent",
    "build_agents",
]
