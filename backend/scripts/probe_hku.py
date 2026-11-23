"""End-to-end: add HKU (acronym lookup) and a nonexistent place (force-add path)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.domain.models import ChatRequest, TripContext  # noqa: E402
from app.turn.orchestrator import Dependencies, collect_turn  # noqa: E402


async def turn(deps, sid, message):
    events = await collect_turn(
        deps, ChatRequest(session_id=sid, message=message, locale="en", review=False),
        session_id=sid, user_message=message,
    )
    for event in events:
        if event.event == "route":
            print(f"  route: {event.data.get('intent')} {event.data.get('confidence')}")
        if event.event == "error":
            print("  ERROR", event.data)
        if event.event == "trace":
            for line in event.data.get("why", []):
                print("  why:", line)
    return events


async def make_trip(deps, store, token):
    session = await store.create_session(client_token=token)
    sid = session["id"]
    await store.save_context(
        sid,
        TripContext(
            session_id=sid, destination="Hong Kong", origin="Singapore",
            start_date="2026-11-30", end_date="2026-12-01", days=2,
            party_size=2, budget=9000, currency="HKD",
        ).model_dump(),
    )
    await turn(deps, sid, "Plan a 2-day Hong Kong trip for 2 people starting 2026-11-30, budget 9000 HKD.")
    return sid


async def show(store, sid):
    plan = await store.load_plan(sid)
    print("  version", plan["version"], "cost", plan["cost"]["total"])
    for index, day in enumerate(plan["days"], start=1):
        print(f"   Day {index}:", ", ".join(f"{item['start']} {item['name']} [{item['data_status']}]" for item in day["items"]))
    print("  constraints:", [(row["text"][:60], row["status"], row["evidence"][:50]) for row in plan["constraints"]])
    messages = await store.list_messages(sid)
    print("  reply:", messages[-1]["content"][:400])


async def main() -> None:
    settings = get_settings()
    store = Store(str(settings.database_file).replace("pathfinder.db", "hku.db"))
    store.init()
    deps = Dependencies(settings=settings, store=store)

    print("== A: HKU ==")
    sid = await make_trip(deps, store, "hku")
    await show(store, sid)
    print("\n-- go to HKU on day 2 --")
    await turn(deps, sid, "ok, day 2, go to HKU")
    await show(store, sid)

    print("\n== B: invented place ==")
    sid2 = await make_trip(deps, store, "force")
    print("\n-- add the Hal Jordon Museum --")
    await turn(deps, sid2, "add the Hal Jordon Museum on day 2 at 15:00")
    await show(store, sid2)
    print("\n-- add it anyway --")
    await turn(deps, sid2, "add it anyway, don't check")
    await show(store, sid2)


if __name__ == "__main__":
    asyncio.run(main())
