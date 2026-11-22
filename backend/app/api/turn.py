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
    thresholds = overrides.get("thresholds") or {}
    model = overrides.get("writer_model") or writer.get("model")
    update: dict = {}
    if model:
        update["llm_model"] = model
        update["openai_model"] = model
        update["llm_temperature"] = writer.get("temperature", c.settings.llm_temperature)
        update["llm_max_tokens"] = writer.get("max_tokens", c.settings.llm_max_tokens)
    orchestrator = overrides.get("orchestrator") or {}
    turn_mode = orchestrator.get("mode")
    if turn_mode in {"analyst", "jev"}:
        update["turn_orchestrator"] = turn_mode
    jev = orchestrator.get("jev") or {}
    if jev.get("model"):
        update["decision_model"] = str(jev["model"])
    if jev.get("url"):
        update["decision_api_url"] = str(jev["url"])
    if jev.get("api_key"):
        update["decision_api_key"] = str(jev["api_key"])
    purpose_models = overrides.get("purpose_models")
    if isinstance(purpose_models, dict):
        update["purpose_models"] = {
            str(key): str(value).strip()
            for key, value in purpose_models.items()
            if str(value).strip()
        }
    purpose_temperatures = overrides.get("purpose_temperatures")
    if isinstance(purpose_temperatures, dict):
        clean = {}
        for key, value in purpose_temperatures.items():
            try:
                clean[str(key)] = max(0.0, min(2.0, float(value)))
            except (TypeError, ValueError):
                continue
        update["purpose_temperatures"] = clean
    purpose_reasoning = overrides.get("purpose_reasoning")
    if isinstance(purpose_reasoning, dict):
        update["purpose_reasoning"] = {
            str(key): str(value).strip().lower()
            for key, value in purpose_reasoning.items()
            if str(value).strip().lower() in {"none", "low", "medium", "high"}
        }
    if writer.get("reasoning"):
        update["llm_reasoning"] = str(writer["reasoning"])
    mode = overrides.get("data_mode")
    if mode in {"live", "atlas"}:
        update["sandbox"] = mode == "atlas"
    try:
        if "router" in thresholds:
            update["router_threshold"] = max(0.0, min(1.0, float(thresholds["router"])))
        if "max_repairs" in thresholds:
            update["max_repairs"] = max(0, min(3, int(thresholds["max_repairs"])))
        if "turn_deadline" in thresholds:
            update["turn_deadline_seconds"] = max(30.0, float(thresholds["turn_deadline"]))
    except (TypeError, ValueError):
        pass
    if not update:
        return c.settings
    return c.settings.model_copy(update=update)


@router.post("/api/turn/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    c = container(request)
    settings = await effective_settings(c)
    from app.api.llm import effective_writer
    from app.api.deps import identity_for

    settings = await effective_writer(c, settings)
    # The memory owner is the signed-in user or the guest token; never the body alone.
    payload = payload.model_copy(update={"identity": identity_for(request, payload.client_token)})
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
