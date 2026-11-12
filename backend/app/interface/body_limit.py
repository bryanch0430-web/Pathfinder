"""Refuse a request whose body is larger than anything the page sends.

The biggest body is the trip form at its limits (100 lines of 200 characters, which is under 100 KB even written in
UTF-32) or a chat message (6,000 characters); the rest are smaller. A JSON body is read whole before it is parsed, so
a client that sends more than that only costs memory and time, and a text that is stored and sent back with the
trip would make every later look at it as large. The limit is far above what is needed and far below what hurts.

A pure ASGI middleware, so a streamed answer is passed on as it comes. A body that declares its length is refused
before any of it is read; one that does not (chunked) is refused when it grows past the limit.
"""

from __future__ import annotations

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: 1 MiB: about ten times the largest body the page sends.
MAX_BODY_BYTES = 1_048_576
TOO_LARGE = "The request is too large."


def _too_large() -> HTTPException:
    """The refusal, as the application's own error: the route that is reading the body ends with it, and the
    answer passes the same middleware as any other (`Connection: close`, since the rest of the body is unread)."""
    return HTTPException(status_code=413, detail=TOO_LARGE, headers={"Connection": "close"})


class BodyLimit:
    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES, limits: dict[str, int] | None = None) -> None:
        self.app = app
        self.limit = limit
        #: Routes with a limit of their own, by exact path (the chat turn carries attached files).
        self.limits = dict(limits or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limits.get(scope.get("path", ""), self.limit)
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            refusal = _too_large()
            await JSONResponse({"detail": TOO_LARGE}, status_code=413, headers=refusal.headers)(scope, receive, send)
            return

        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _too_large()
            return message

        await self.app(scope, counted, send)
