"""The FastAPI application: routers, lifecycle and the built UI.

One process serves both the API and (when ``frontend/dist`` exists) the React app,
so a demo needs one command. During development Vite proxies ``/api`` here.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import account, admin, evals, journeys, llm, misc, turn
from app.api.deps import Container
from app.config import PROJECT_DIR, Settings, get_settings
from app.core.store import Store

logger = logging.getLogger("pathfinder")
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = Store(settings.database_file)
    container = Container(settings=settings, store=store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.init()
        # A turn interrupted by a restart leaves its seat row behind. Drop the
        # stale ones now, so the first message on that trip is not refused.
        try:
            await store.clear_stale_seats(60.0)
        except Exception:  # noqa: BLE001 - a cleanup must not stop startup
            logger.exception("stale seat cleanup failed")
        logger.info("Pathfinder v5 ready: %s (sandbox=%s)", settings.environment, settings.sandbox)
        yield
        await store.close()

    app = FastAPI(
        title="Pathfinder v5",
        version="5.0.0",
        description="An LLM-first conversational trip planner.",
        lifespan=lifespan,
    )
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        # Only the app's own origins: a page on another site must not be able to
        # read this API, least of all its operator routes on a local install.
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong on the server. Please try again."},
        )

    for module in (journeys, turn, misc, account, admin, llm, evals):
        app.include_router(module.router)

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="ui")

    return app


app = create_app()
