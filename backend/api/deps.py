"""FastAPI dependency injection: the container lives on app.state, built in the lifespan."""

from __future__ import annotations

from fastapi import Request, WebSocket

from backend.agents.orchestrator import TurnOrchestrator
from backend.container import Container


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_orchestrator(request: Request) -> TurnOrchestrator:
    return get_container(request).orchestrator


def ws_orchestrator(websocket: WebSocket) -> TurnOrchestrator:
    container: Container = websocket.app.state.container
    return container.orchestrator
