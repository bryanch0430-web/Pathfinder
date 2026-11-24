"""The chat stream: POST /api/turn/stream as server-sent events."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.deps import Container, bearer_user, container, rate_limit
from app.core.store import Store
from app.domain.models import ChatRequest, TripContext
from app.turn.orchestrator import Dependencies, TurnEvent, run_turn

router = APIRouter()

KEEPALIVE = ": keepalive\n\n"


MIN_TURN_BUDGET = 60.0
MAX_TURN_BUDGET = 900.0


def turn_budget(settings) -> float:
    """The whole-turn execution budget; never below 60 s, never above 15 min."""
    return max(MIN_TURN_BUDGET, min(MAX_TURN_BUDGET, float(getattr(settings, "turn_deadline_seconds", 300.0))))


MAX_ATTACHMENTS = 6
MAX_ATTACHMENT_CHARS = 8 * 1024 * 1024  # base64 characters per attachment
MAX_ATTACHMENT_TOTAL_CHARS = 20 * 1024 * 1024
_IMAGE_TYPES = ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif")


def _is_real_image(data: str) -> bool:
    """True when the base64 payload starts with a PNG, JPEG, GIF or WebP signature."""
    import base64

    try:
        head = base64.b64decode("".join(data[:64].split())[:64], validate=False)
    except ValueError:
        return False
    return (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8\xff")
        or head[:6] in (b"GIF87a", b"GIF89a")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


def check_attachments(attachments: list) -> list:
    """Enforce the upload caps (413) and neutralise images that are not real images."""
    if len(attachments) > MAX_ATTACHMENTS:
        raise HTTPException(status_code=413, detail=f"Attach at most {MAX_ATTACHMENTS} files per message.")
    total = 0
    for attachment in attachments:
        total += len(attachment.data)
        if len(attachment.data) > MAX_ATTACHMENT_CHARS:
            raise HTTPException(status_code=413, detail="A file is too large (limit 8 MB per file).")
    if total > MAX_ATTACHMENT_TOTAL_CHARS:
        raise HTTPException(status_code=413, detail="The files are too large together (limit 20 MB).")
    clean = []
    for attachment in attachments:
        if attachment.media_type.lower().startswith("image/") and (
            attachment.media_type.lower() not in _IMAGE_TYPES or not _is_real_image(attachment.data)
        ):
            # Unreadable: the intake reports it as a failed source and sends nothing to the vision model.
            attachment = attachment.model_copy(update={"media_type": "application/x-unreadable", "data": ""})
        clean.append(attachment)
    return clean


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
    purpose_max_tokens = overrides.get("purpose_max_tokens")
    if isinstance(purpose_max_tokens, dict):
        clean_tokens = {}
        for key, value in purpose_max_tokens.items():
            try:
                clean_tokens[str(key)] = max(16, min(32000, int(value)))
            except (TypeError, ValueError):
                continue
        update["purpose_max_tokens"] = clean_tokens
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
    if mode in {"live", "corpus", "atlas"}:
        # "atlas" is the legacy persisted value; normalize it to corpus.
        update["sandbox"] = mode in {"corpus", "atlas"}
    try:
        if "analyst_confidence" in thresholds:
            update["analyst_confidence_threshold"] = max(0.0, min(1.0, float(thresholds["analyst_confidence"])))
        if "jev_confidence" in thresholds:
            update["jev_confidence_threshold"] = max(0.0, min(1.0, float(thresholds["jev_confidence"])))
        if "router" in thresholds:
            # Legacy runtime value: preserve the old shared behaviour for both
            # signals until the operator saves the two new fields separately.
            legacy = max(0.0, min(1.0, float(thresholds["router"])))
            update.setdefault("analyst_confidence_threshold", legacy)
            update.setdefault("jev_confidence_threshold", legacy)
        if "max_repairs" in thresholds:
            update["max_repairs"] = max(0, min(3, int(thresholds["max_repairs"])))
        if "turn_deadline" in thresholds:
            update["turn_deadline_seconds"] = max(60.0, min(900.0, float(thresholds["turn_deadline"])))
        if "max_message_chars" in thresholds:
            update["max_message_chars"] = max(200, min(8000, int(thresholds["max_message_chars"])))
    except (TypeError, ValueError):
        pass
    if not update:
        return c.settings
    return c.settings.model_copy(update=update)


@router.post("/api/turn/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    c = container(request)
    payload = payload.model_copy(update={"attachments": check_attachments(payload.attachments)})
    # Anonymous guests share the operator's model budget; cap how fast they can
    # spend it from one address without getting in the way of normal use.
    rate_limit(request, "turn", 40, 600.0)
    settings = await effective_settings(c)
    # The box stops at this length too; a script or an old page can still send more.
    if len(payload.message) > settings.max_message_chars:
        raise HTTPException(
            status_code=422,
            detail=f"Keep the message under {settings.max_message_chars} characters.",
        )
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
        # A live turn refreshes its seat on every keepalive (15 s). A seat that
        # has not been touched for 60 s belongs to a turn that died, so it is
        # taken over instead of blocking this trip forever.
        stale_after = 60.0
        for _ in range(40):  # wait for an earlier turn on the same trip, up to ~20 s
            claimed = await c.store.claim_seat(session_id, turn_id, stale_after=stale_after)
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
        iterator = None
        loop = asyncio.get_running_loop()
        # This is the execution budget, not a per-model timeout. It is deliberately
        # generous (default 300 s) so a complex plan can complete; the harness stops
        # only when the whole turn has spent its budget.
        budget = turn_budget(deps.settings)
        deadline = loop.time() + budget
        timed_out = False
        try:
            events = run_turn(deps, payload, session_id=session_id, user_message=payload.message)
            iterator = events.__aiter__()
            pending = asyncio.ensure_future(iterator.__anext__())
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    timed_out = True
                    break
                done, _ = await asyncio.wait({pending}, timeout=min(15.0, remaining))
                if not done:
                    if loop.time() >= deadline:
                        timed_out = True
                        break
                    # A comment frame keeps proxies and browsers from dropping a quiet plan.
                    # The pending __anext__ is NOT cancelled for a keepalive: that would
                    # kill the turn while it is still working.
                    await c.store.touch_seat(session_id, turn_id)
                    yield KEEPALIVE
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                pending = asyncio.ensure_future(iterator.__anext__())
                yield sse_frame(event.event, event.data)
            if timed_out:
                # Cancel the work before telling the client; the seat is released in
                # the finally block, so another turn can start immediately.
                pending.cancel()
                try:
                    await pending
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
                if iterator is not None:
                    try:
                        await iterator.aclose()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        pass
                yield sse_frame(
                    "error",
                    {
                        "code": "turn_timeout",
                        "message": (
                            f"This turn reached its {int(budget)}-second execution budget. "
                            "The plan was stopped; send the request again or raise the budget in Settings."
                        ),
                        "detail": "turn_deadline_seconds",
                    },
                )
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
