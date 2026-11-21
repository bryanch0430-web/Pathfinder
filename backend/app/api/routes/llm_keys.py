"""Bring-your-own writer key. The raw key is accepted once and never returned."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.admin import require_admin
from app.core.auth import AuthenticatedUser
from app.core.database import get_db
from app.services import byok

router = APIRouter(prefix="/api/llm", tags=["llm-keys"])


class KeyIn(BaseModel):
    provider: str
    api_key: str = Field(min_length=8, max_length=512)
    model: str = ""
    temperature: str | None = None
    reasoning: str | None = None


class ChoiceIn(BaseModel):
    key_id: str | None = None


class ServerWriterIn(BaseModel):
    writer: str
    temperature: str | None = None
    reasoning: str | None = None


class SamplingIn(BaseModel):
    temperature: str | None = None
    reasoning: str | None = None


@router.get("/efforts")
async def efforts(provider: str, model: str = "", _admin: AuthenticatedUser = Depends(require_admin)):
    return {"efforts": byok.effort_options(provider, model)}


@router.get("/providers")
async def providers(_admin: AuthenticatedUser = Depends(require_admin)):
    return {"providers": byok.provider_rows()}


@router.get("/server")
async def server_writer(_admin: AuthenticatedUser = Depends(require_admin)):
    return byok.server_view()


@router.put("/server")
async def choose_server(body: ServerWriterIn, _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if body.writer not in {"deepseek", "poe", "xai"}:
        raise HTTPException(status_code=422, detail="Choose DeepSeek, Poe, or Grok.")
    picked = next(item for item in byok.server_catalog() if item["id"] == body.writer)
    if not picked["ready"]:
        raise HTTPException(status_code=422, detail="That server key is empty. Add it to backend/.env and restart.")
    try:
        return await byok.save_server_writer(db, body.writer, body.temperature, body.reasoning)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/keys")
async def keys(_admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    saved = await byok.list_keys(db)
    return {"keys": [byok.public_key(row) for row in saved], "status": await byok.public_status(db)}


@router.post("/keys/test")
async def test_key(body: KeyIn, _admin: AuthenticatedUser = Depends(require_admin)):
    spec = byok.PROVIDERS.get(body.provider)
    if spec is None:
        raise HTTPException(status_code=422, detail="Choose one of the listed providers.")
    if not byok.valid_key(body.api_key.strip()):
        raise HTTPException(status_code=422, detail="The key must be 8 to 512 printable characters.")
    model = body.model.strip() or spec.default_model
    ok, message = await byok.probe(body.provider, body.api_key.strip(), model)
    return {"ok": ok, "message": message, "model": model}


@router.post("/keys")
async def add_key(body: KeyIn, _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    spec = byok.PROVIDERS.get(body.provider)
    if spec is None:
        raise HTTPException(status_code=422, detail="Choose one of the listed providers.")
    secret = body.api_key.strip()
    if not byok.valid_key(secret):
        raise HTTPException(status_code=422, detail="The key must be 8 to 512 printable characters.")
    model = body.model.strip() or spec.default_model
    ok, message = await byok.probe(body.provider, secret, model)
    if not ok:
        raise HTTPException(status_code=422, detail=message)
    try:
        saved = await byok.save_key(db, body.provider, secret, model, body.temperature, body.reasoning)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"key": byok.public_key(saved), "status": await byok.public_status(db)}


@router.put("/choice")
async def choose(body: ChoiceIn, _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if body.key_id is not None and await db.get(byok.LlmKey, body.key_id) is None:
        raise HTTPException(status_code=404, detail="No such key.")
    await byok.choose_key(db, body.key_id)
    return {"status": await byok.public_status(db)}


@router.put("/keys/{key_id}/sampling")
async def sampling(key_id: str, body: SamplingIn, _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        saved = await byok.save_sampling(db, key_id, body.temperature, body.reasoning)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if saved is None:
        raise HTTPException(status_code=404, detail="No such key.")
    return {"key": byok.public_key(saved), "status": await byok.public_status(db)}


@router.delete("/keys/{key_id}")
async def remove(key_id: str, _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if not await byok.delete_key(db, key_id):
        raise HTTPException(status_code=404, detail="No such key.")
    return {"status": await byok.public_status(db)}
