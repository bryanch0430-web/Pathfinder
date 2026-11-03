"""Pacing for server-sent event streams: keepalive comments and a turn deadline.

The source (a turn) runs in its own task, so a quiet spell never interrupts it
and its context variables (the per-turn tracer) stay in one task for the whole
turn. Whatever ends the stream (the source finishing, the deadline, a client
disconnect) cancels that task and waits for it to unwind.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import anyio

logger = logging.getLogger(__name__)

#: An SSE comment line: proxies see traffic, EventSource-style parsers ignore it.
KEEPALIVE_FRAME = ": keepalive\n\n"

#: Yielded by `paced` after `keepalive_seconds` without an item.
KEEPALIVE = object()
#: Yielded once by `paced` when the deadline passes; nothing follows it.
TIMED_OUT = object()

#: How long a cancelled source may take to unwind before we stop waiting for it.
CANCEL_GRACE_SECONDS = 5.0

_ITEM, _ERROR, _END = "item", "error", "end"


def sse_frame(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def paced(
    source: AsyncIterator[Any],
    *,
    keepalive_seconds: float,
    deadline_seconds: float,
) -> AsyncIterator[Any]:
    """Yield `source`'s items, KEEPALIVE after each quiet spell, and TIMED_OUT at the deadline.

    An exception from `source` is re-raised here. The deadline counts from the
    first iteration and covers the whole source.
    """
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for item in source:
                queue.put_nowait((_ITEM, item))
        except Exception as exc:
            queue.put_nowait((_ERROR, exc))
        else:
            queue.put_nowait((_END, None))

    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    task = asyncio.create_task(pump())
    getter: asyncio.Future[tuple[str, Any]] | None = None
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                await _stop(task)
                yield TIMED_OUT
                return
            if getter is None:
                getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({getter}, timeout=min(keepalive_seconds, remaining))
            if not done:
                if loop.time() < deadline:
                    yield KEEPALIVE
                continue
            kind, value = getter.result()
            getter = None
            if kind == _END:
                return
            if kind == _ERROR:
                raise value
            yield value
    finally:
        if getter is not None:
            getter.cancel()
        await _stop(task)


async def _stop(task: asyncio.Task[None]) -> None:
    """Cancel the source task and wait for it, even while our own task is being cancelled."""
    if not task.done():
        task.cancel()
        # Starlette cancels a disconnected stream through an anyio scope, which
        # would re-cancel this wait; the shield lets the source finish unwinding.
        with anyio.CancelScope(shield=True):
            await asyncio.wait({task}, timeout=CANCEL_GRACE_SECONDS)
        if not task.done():
            logger.warning("turn task did not stop within %.0f s of cancellation", CANCEL_GRACE_SECONDS)
            return
    if not task.cancelled() and task.exception() is not None:
        logger.warning("turn task failed while stopping: %r", task.exception())
