"""Local smoke test: one planning turn through the real pipeline (sandbox providers)."""

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


async def main() -> None:
    settings = get_settings()
    db = Path(settings.database_path)
    if db.exists():
        db.unlink()
    store = Store(settings.database_path)
    store.init()
    session = await store.create_session(client_token="smoke")
    session_id = session["id"]
    context = TripContext(
        session_id=session_id,
        destination="Kyoto",
        origin="Hong Kong",
        start_date="2027-04-10",
        end_date="2027-04-13",
        days=4,
        party_size=2,
        budget=400000,
        currency="JPY",
        hard_constraints=["must include Fushimi Inari", "stay within budget"],
    )
    await store.save_context(session_id, context.model_dump())
    deps = Dependencies(settings=settings, store=store)
    request = ChatRequest(
        session_id=session_id,
        message="Plan a 4-day Kyoto trip for 2 people starting 2027-04-10, budget 400000 JPY, must include Fushimi Inari.",
        locale="en",
    )
    events = await collect_turn(deps, request, session_id=session_id, user_message=request.message)
    for event in events:
        name = event.event
        if name == "token":
            continue
        if name == "plan":
            plan = event.data.get("plan") or {}
            print(f"[plan] v{plan.get('version')} {len(plan.get('days', []))} days")
        elif name == "trace":
            data = event.data
            print(f"[trace] generations={len(data['generations'])} tools={data['tool_calls']}")
            for gen in data["generations"]:
                print(f"   {gen['purpose']}: {gen['model']} {gen['latency_ms']}ms in={gen['prompt_tokens']} out={gen['completion_tokens']}")
            for line in data["why"]:
                print("   why:", line)
        else:
            print(f"[{name}] {str(event.data)[:180]}")
    row = await store.load_plan(session_id)
    if row:
        print("\nSUMMARY:", row["summary"])
        for index, day in enumerate(row["days"], start=1):
            names = ", ".join(f"{item['start']} {item['name']}" for item in day["items"])
            print(f"Day {index} {day['date']}: {names}")
        print("hotel:", row["hotels"][0]["name"] if row["hotels"] else None)
        print("cost:", row["cost"]["total"], row["cost"]["currency"], row["cost"]["status"])
        print("constraints:", [(c["text"], c["status"]) for c in row["constraints"]])
        print("gaps:", [gap["message"] for gap in row["data_quality"]["gaps"]])
    messages = await store.list_messages(session_id)
    for message in messages:
        print(f"\n{message['role']}: {message['content'][:600]}")


if __name__ == "__main__":
    asyncio.run(main())
