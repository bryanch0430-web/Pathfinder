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


def body_limits(settings: Settings) -> dict[tuple[str, str], int]:
    return {
        ("POST", "/api/turn/stream"): settings.max_turn_body_bytes,
        ("POST", "/api/journeys/import"): settings.max_import_body_bytes,
    }


class BodyLimitMiddleware:
    """Refuse an oversized body with 413 before the route parses it into memory.

    A declared Content-Length over the cap is answered at once; a chunked body
    (or one that lies about its length) is cut off as soon as it passes the cap.
    """

    def __init__(self, app, limits: dict[tuple[str, str], int]) -> None:
        self.app = app
        self.limits = limits

    async def __call__(self, scope, receive, send) -> None:
        limit = self.limits.get((scope.get("method", ""), scope.get("path", ""))) if scope["type"] == "http" else None
        if limit is None:
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await self._refuse(scope, receive, send, limit)
            return
        seen = 0
        refused = False

        async def counted():
            nonlocal seen, refused
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit and not refused:
                    refused = True
                    await self._refuse(scope, receive, send, limit)
                if refused:
                    # The route sees a dropped connection and never gets the body.
                    return {"type": "http.disconnect"}
            return message

        async def guarded(message):
            if not refused:  # whatever the route says after the 413 is dropped
                await send(message)

        await self.app(scope, counted, guarded)

    @staticmethod
    async def _refuse(scope, receive, send, limit: int) -> None:
        megabytes = max(1, limit // (1024 * 1024))
        response = JSONResponse(
            status_code=413, content={"detail": f"That request is too large (limit {megabytes} MB)."}
        )
        await response(scope, receive, send)

FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    from app.core.prompts import set_canary

    set_canary(settings.canary_token)
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
    app.add_middleware(BodyLimitMiddleware, limits=body_limits(settings))
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
        # Keep the embedding site's origin for cross-origin iframes: YouTube
        # refuses to play an embed that arrives with no referrer (error 153).
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
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
