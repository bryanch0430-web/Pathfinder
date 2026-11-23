"""Check the seven reported issues on the current code (sandbox evidence)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.application.memory import remember_trip  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.domain.models import ChatRequest, TripContext, TripPlan  # noqa: E402
from app.turn.orchestrator import Dependencies, collect_turn  # noqa: E402

SETTINGS = get_settings()
DB = str(SETTINGS.database_file).replace("pathfinder.db", "features.db")


async def turn(deps, sid, message, review=False, token="guest:test"):
    events = await collect_turn(
        deps,
        ChatRequest(session_id=sid, message=message, locale="en", review=review, identity=token),
        session_id=sid,
        user_message=message,
    )
    err = next((e.data for e in events if e.event == "error"), None)
    if err:
        print("  ERROR", err)
    return events


async def new_trip(store, token, context):
    session = await store.create_session(client_token=token)
    sid = session["id"]
    context.session_id = sid
    await store.save_context(sid, context.model_dump())
    return sid


async def show_plan(store, sid):
    plan = await store.load_plan(sid)
    if not plan:
        print("  no plan")
        return
    for index, day in enumerate(plan["days"], 1):
        names = [f"{item['name']}" for item in day["items"]]
        print(f"  Day {index}: {names}")
    photos = [item for day in plan["days"] for item in day["items"] if item.get("image")]
    print("  photos:", len(photos), "/", sum(len(day["items"]) for day in plan["days"]))
    print("  videos:", [note["title"][:50] for note in plan.get("video_notes") or []])
    print("  gaps:", [gap["message"][:100] for gap in plan["data_quality"]["gaps"]][:4])


async def main() -> None:
    store = Store(DB)
    store.init()
    deps = Dependencies(settings=SETTINGS, store=store)

    print("== 1. Gundam (research + photos + videos) ==")
    sid = await new_trip(
        store,
        "gundam",
        TripContext(
            session_id="", destination="Tokyo", origin="Hong Kong", start_date="2026-11-30",
            end_date="2026-12-02", days=3, party_size=2, budget=120000, currency="HKD",
            preferences=["see the Gundam statue"],
        ),
    )
    await turn(deps, sid, "Plan a 3-day Tokyo trip for 2 people starting 2026-11-30. I really want to see the Gundam statue.")
    await show_plan(store, sid)

    print("\n== 2. HKU full days ==")
    sid = await new_trip(
        store,
        "hku2",
        TripContext(
            session_id="", destination="Hong Kong", origin="Taiwan", start_date="2026-11-23",
            end_date="2026-11-25", days=3, party_size=1, budget=9000, currency="HKD",
        ),
    )
    await turn(
        deps,
        sid,
        "Plan 3 days in Hong Kong for 1 person starting 2026-11-23. Each day is a full day at HKU; no other attractions.",
    )
    await show_plan(store, sid)

    print("\n== 3. live agent ==")
    sid = await new_trip(
        store, "agent2", TripContext(session_id="", destination="Hong Kong", days=2, currency="HKD")
    )
    await turn(deps, sid, "I want to talk to a live agent please")
    session = await store.get_session(sid)
    print("  held:", session["held"], "agent:", session["agent_name"])

    print("\n== 4. PII first layer ==")
    sid = await new_trip(store, "pii2", TripContext(session_id="", destination="Kyoto", currency="JPY"))
    await turn(deps, sid, "my email is hal@example.com and my phone is 9123 4567, plan a 2-day Kyoto trip")
    rows = await store.list_messages(sid)
    print("  stored user text:", rows[0]["content"][:140])

    print("\n== 5. memory ==")
    plan = await store.load_plan(sid)
    context = await store.load_context(sid)
    if plan:
        added = await remember_trip(
            container=type("C", (), {"settings": SETTINGS, "store": store})(),
            identity="guest:mem",
            plan=TripPlan(**plan),
            context=TripContext(**context),
            session_id=sid,
        )
        profile = await store.get_profile("guest:mem")
        print("  added:", added)
        print("  profile:", (profile or {}).get("preferences"))


if __name__ == "__main__":
    asyncio.run(main())
