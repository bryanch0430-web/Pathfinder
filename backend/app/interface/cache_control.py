"""Keep the answers that depend on who is asking out of every cache.

The guest token and the bearer travel in headers, not in the URL, so two callers send the same URL and a
cache that stores a response by URL would answer one caller's profile, saved trips or trip to the next.
`private, no-store` forbids shared and private caches alike, which is simpler to get right than a `Vary` list.
A pure ASGI middleware, so a streamed answer is passed on as it comes.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Everything under these paths is answered for one caller: an account, a trip, a profile, its saved trips
#: and recommendations, a turn, a caller's LLM keys, or an operator. The public routes (runtime, rates,
#: shared trips) are not here.
PRIVATE_PREFIXES = (
    "/api/account",
    "/api/journeys",
    "/api/traveller",
    "/api/recall",
    "/api/wishlist",
    "/api/popular",
    "/api/turn",
    "/api/eval",
    "/api/admin",
    "/api/llm",
)
PRIVATE = "private, no-store"


class PrivateResponses:
    """Adds `Cache-Control: private, no-store` to the answers under `PRIVATE_PREFIXES`, errors included.

    A route that sets its own `Cache-Control` (the turn stream says `no-cache`) keeps it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PRIVATE_PREFIXES):
            await self.app(scope, receive, send)
            return

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "cache-control" not in headers:
                    headers["Cache-Control"] = PRIVATE
            await send(message)

        await self.app(scope, receive, send_private)
