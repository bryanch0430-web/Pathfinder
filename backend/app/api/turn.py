"""The chat stream: POST /api/turn/stream as server-sent events."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.deps import Container, bearer_user, container
from app.core.store import Store
from app.domain.models import ChatRequest, TripContext
from app.turn.orchestrator import Dependencies, TurnEvent, run_turn

router = APIRouter()

KEEPALIVE = ": keepalive\n\n"


def sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def effective_settings(c: Container):
    overrides = await c.store.get_kv("runtime") or {}
    writer = overrides.get("writer") or {}
    model = overrides.get("writer_model") or writer.get("model")
    if not model:
        return c.settings
    return c.settings.model_copy(
        update={
            "llm_model": model,
            "openai_model": model,
            "llm_temperature": writer.get("temperature", c.settings.llm_temperature),
            "llm_max_tokens": writer.get("max_tokens", c.settings.llm_max_tokens),
        }
    )


@router.post("/api/turn/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    c = container(request)
    settings = await effective_settings(c)
    from app.api.llm import effective_writer

    settings = await effective_writer(c, settings)
    session_id = payload.session_id or ""
    if not session_id:
        token = (request.headers.get("X-Client-Token") or payload.client_token or "").strip()
        if not token and not bearer_user(request):
            raise HTTPException(status_code=401, detail="Sign in or send a client token.")
        session = await c.store.create_session(client_token=token, user_id=bearer_user(request))
        session_id = session["id"]
        context = TripContext(session_id=session_id, currency=settings.default_currency, language=payload.locale)
        await c.store.save_context(session_id, context.model_dump())
    else:
        session = await c.store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="That trip no longer exists.")
        if not await _may_chat(request, c.store, session):
            raise HTTPException(status_code=403, detail="That trip belongs to another traveller.")

    deps = Dependencies(settings=settings, store=c.store)
    turn_id = uuid.uuid4().hex

    async def stream():
        claimed = False
        for _ in range(40):  # wait for an earlier turn on the same trip, up to ~20 s
            claimed = await c.store.claim_seat(session_id, turn_id)
            if claimed:
                break
            await asyncio.sleep(0.5)
        if not claimed:
            yield sse_frame(
                "error",
                {"code": "planner_busy", "message": "The planner is still working on the previous message. Try again in a moment."},
            )
            return
        pending: asyncio.Future | None = None
        try:
            events = run_turn(deps, payload, session_id=session_id, user_message=payload.message)
            iterator = events.__aiter__()
            pending = asyncio.ensure_future(iterator.__anext__())
            while True:
                done, _ = await asyncio.wait({pending}, timeout=15.0)
                if not done:
                    # A comment frame keeps proxies and browsers from dropping a quiet plan.
                    # The pending __anext__ is NOT cancelled: cancelling it would kill the turn.
                    yield KEEPALIVE
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                pending = asyncio.ensure_future(iterator.__anext__())
                yield sse_frame(event.event, event.data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield sse_frame(
                "error",
                {"code": "stream_error", "message": "The stream ended unexpectedly.", "detail": str(exc)[:300]},
            )
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
            await c.store.release_seat(session_id, turn_id)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _may_chat(request: Request, store: Store, session: dict) -> bool:
    from app.api.deps import is_admin

    owner = session.get("client_token") or ""
    token = (request.headers.get("X-Client-Token") or "").strip()
    if owner and token and owner == token:
        return True
    user_id = bearer_user(request)
    if user_id and session.get("user_id") == user_id:
        return True
    if not owner and not session.get("user_id"):
        return True
    return is_admin(request)
