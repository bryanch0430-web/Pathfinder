"""Admin list of SerpAPI keys. The key itself is never returned."""

from __future__ import annotations

import re
import secrets
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from app.container import Container
from app.infrastructure.serpapi_keys import STATUSES, current_ring, public_rows

_KEY = re.compile(r"[!-~]{8,512}")


class SerpKeyIn(BaseModel):
    api_key: str = ""
    label: str = Field(default="", max_length=80)
    status: str = "ready"


class SerpStatusIn(BaseModel):
    status: str


def register_serpapi_routes(
    app: FastAPI,
    *,
    container: Callable[[Request], Container],
    require_admin: Callable[..., None],
) -> None:
    def _apply(c: Container) -> None:
        ring = current_ring()
        if ring is not None and ring.store is c.store:
            ring.apply()

    @app.get("/api/admin/serpapi-keys", dependencies=[Depends(require_admin)])
    async def list_keys(request: Request) -> dict[str, Any]:
        return {"keys": public_rows(container(request).store)}

    @app.post("/api/admin/serpapi-keys", dependencies=[Depends(require_admin)])
    async def add_key(payload: SerpKeyIn, request: Request) -> dict[str, Any]:
        key = payload.api_key.strip()
        status = payload.status.strip().lower()
        if not _KEY.fullmatch(key):
            raise HTTPException(status_code=422, detail="The key must be 8 to 512 characters, with no spaces.")
        if status not in STATUSES:
            raise HTTPException(status_code=422, detail="Status must be active, ready, exhausted, or paused.")
        c = container(request)
        if c.store.serpapi_known(c.box, key):
            raise HTTPException(status_code=409, detail="That key is already in the list.")
        c.store.serpapi_add(
            key_id="s" + secrets.token_hex(8),
            ciphertext=c.box.encrypt(key),
            key_tail=key[-4:],
            label=payload.label.strip(),
            status=status,
        )
        _apply(c)
        return {"keys": public_rows(c.store)}

    @app.patch("/api/admin/serpapi-keys/{key_id}", dependencies=[Depends(require_admin)])
    async def set_status(key_id: str, payload: SerpStatusIn, request: Request) -> dict[str, Any]:
        status = payload.status.strip().lower()
        if status not in STATUSES:
            raise HTTPException(status_code=422, detail="Status must be active, ready, exhausted, or paused.")
        if not re.fullmatch(r"s[0-9a-f]{16}", key_id):
            raise HTTPException(status_code=404, detail="Unknown key")
        c = container(request)
        if not c.store.serpapi_set_status(key_id, status):
            raise HTTPException(status_code=404, detail="Unknown key")
        _apply(c)
        return {"keys": public_rows(c.store)}
