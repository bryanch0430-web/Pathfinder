"""Bind each HTTP request to its caller, whose own LLM keys then serve it.

The owner is a valid bearer's `user:<id>`, else the `X-Client-Token` guest token, else nobody. Only the
identity is bound here: the caller's key is read the first time the request uses the writer or search
(see `app.application.llm_selection`). A pure ASGI middleware, so a streamed answer, its background
work and the tasks it starts all run inside the binding.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.application.auth import decode_token
from app.application.llm_selection import bind_caller
from app.config import Settings
from app.domain.credentials import SERVER_OWNER
from app.domain.models import USER_PREFIX
from app.interface.identity_format import TOKEN_FORMAT


class CallerBinding:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with bind_caller(self.owner(Headers(scope=scope))):
            await self.app(scope, receive, send)

    def owner(self, headers: Headers) -> str | None:
        """Signature validity is enough for a bearer: the user row is not read on every request."""
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            payload = decode_token(authorization[7:].strip(), self.settings.auth_secret)
            subject = str(payload.get("sub", "")) if payload else ""
            if subject:
                return f"{USER_PREFIX}{subject}"
        token = headers.get("x-client-token", "").strip()
        # "server" is the operator's own key, and a `user:` token belongs only to that user's bearer.
        if not TOKEN_FORMAT.fullmatch(token) or token == SERVER_OWNER or token.startswith(USER_PREFIX):
            return None
        return token
