"""FastAPI application. Run locally with:

    uv run uvicorn backend.api.main:app --reload --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import JsonValue

from backend.agents.plan_ops import PlanRequestError
from backend.api.routes import router
from backend.container import Container, build_container
from backend.memory.session import SessionNotFound
from backend.schemas.turn import TurnEvent
from backend.settings import get_settings


def create_app(container_factory: Callable[[], Container] | None = None) -> FastAPI:
    factory = container_factory or (lambda: build_container(get_settings()))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = factory()
        app.state.container = container
        await container.start()
        try:
            yield
        finally:
            await container.stop()

    app = FastAPI(
        title="Pathfinder API",
        version="0.1.0",
        description="State-aware agentic trip planner. The frontend's only coupling is this "
        "contract (and the shared TripPlan JSON Schema in contracts/).",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.exception_handler(SessionNotFound)
    async def _not_found(_: Request, exc: SessionNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"session not found: {exc}"})

    @app.exception_handler(LookupError)
    async def _conflict(_: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(PlanRequestError)
    async def _plan_request(_: Request, exc: PlanRequestError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    def openapi() -> dict[str, JsonValue]:
        """OpenAPI plus the WebSocket event schema (WebSockets are not described by OpenAPI, so
        TurnEvent is added to components for frontend type generation)."""
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes
        )
        event_schema = TurnEvent.model_json_schema(ref_template="#/components/schemas/{model}")
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for name, definition in event_schema.pop("$defs", {}).items():
            components.setdefault(name, definition)
        components["TurnEvent"] = event_schema
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


app = create_app()
