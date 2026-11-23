"""Trace where a turn stalls: print each event as it arrives, with a per-event timeout."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.domain.models import ChatRequest, TripContext  # noqa: E402
from app.turn.orchestrator import Dependencies, run_turn  # noqa: E402


async def main() -> None:
    settings = get_settings()
    store = Store("data/probe.db")
    store.init()
    session = await store.create_session(client_token="probe")
    session_id = session["id"]
    await store.save_context(
        session_id,
        TripContext(
            session_id=session_id,
            destination="Kyoto",
            origin="Hong Kong",
            start_date="2027-04-10",
            days=3,
            party_size=2,
            budget=300000,
            currency="JPY",
        ).model_dump(),
    )
    deps = Dependencies(settings=settings, store=store)
    request = ChatRequest(session_id=session_id, message="Plan a 3-day Kyoto trip for 2 people.", locale="en")
    started = time.perf_counter()
    stream = run_turn(deps, request, session_id=session_id, user_message=request.message)
    iterator = stream.__aiter__()
    while True:
        try:
            event = await asyncio.wait_for(iterator.__anext__(), timeout=45)
        except StopAsyncIteration:
            print("END", flush=True)
            break
        except asyncio.TimeoutError:
            print(f"TIMEOUT after {time.perf_counter() - started:.1f}s", flush=True)
            break
        if event.event == "trace":
            for line in event.data.get("why", []):
                print(f"        why: {line}", flush=True)
        summary = str(event.data)[:120]
        print(f"{time.perf_counter() - started:6.1f}s {event.event}: {summary}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
