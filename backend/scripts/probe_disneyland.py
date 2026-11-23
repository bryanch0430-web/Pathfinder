"""End-to-end check of the Disneyland case: plan, then add a named park after 13:00."""

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


async def turn(deps, session_id, message, review=False):
    events = await collect_turn(
        deps,
        ChatRequest(session_id=session_id, message=message, locale="en", review=review),
        session_id=session_id,
        user_message=message,
    )
    for event in events:
        if event.event == "error":
            print("ERROR", event.data)
        if event.event == "route":
            print("route:", event.data.get("intent"), event.data.get("confidence"))
        if event.event == "trace":
            for line in event.data.get("why", []):
                print("  why:", line)
    return events


async def main() -> None:
    settings = get_settings()
    store = Store(str(settings.database_file).replace("pathfinder.db", "disneyland.db"))
    store.init()
    session = await store.create_session(client_token="disney")
    sid = session["id"]
    await store.save_context(
        sid,
        TripContext(
            session_id=sid,
            destination="Hong Kong",
            origin="Singapore",
            start_date="2026-11-30",
            end_date="2026-12-01",
            days=2,
            party_size=2,
            budget=9000,
            currency="HKD",
        ).model_dump(),
    )
    deps = Dependencies(settings=settings, store=store)
    print("== plan ==")
    await turn(deps, sid, "Plan a 2-day Hong Kong trip for 2 people starting 2026-11-30, budget 9000 HKD.")
    plan = await store.load_plan(sid)
    print("version", plan["version"], "cost", plan["cost"]["total"], "hotel", plan["hotels"][0]["name"] if plan["hotels"] else None)
    for index, day in enumerate(plan["days"], start=1):
        print(f" Day {index}:", ", ".join(f"{item['start']} {item['name']}" for item in day["items"]))

    print("\n== add Disneyland after 13:00 ==")
    await turn(deps, sid, "Add Hong Kong Disneyland on day 2 from 13:00 for the rest of the day.")
    plan = await store.load_plan(sid)
    print("version", plan["version"], "cost", plan["cost"]["total"])
    for index, day in enumerate(plan["days"], start=1):
        print(f" Day {index}:", ", ".join(f"{item['start']} {item['name']} [{item['data_status']}]" for item in day["items"]))
    print("constraints:", [(row["text"][:70], row["status"], row["evidence"][:60]) for row in plan["constraints"]])
    print("gaps:", [gap["message"][:100] for gap in plan["data_quality"]["gaps"]])
    messages = await store.list_messages(sid)
    print("\nreply:", messages[-1]["content"][:600])


if __name__ == "__main__":
    asyncio.run(main())
