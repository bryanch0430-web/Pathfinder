import asyncio, os, sys
from pathlib import Path
os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.application.memory import remember_trip
from app.config import get_settings
from app.core.store import Store
from app.domain.models import ChatRequest, TripContext, TripPlan
from app.turn.orchestrator import Dependencies, collect_turn

SETTINGS=get_settings(); DB=str(SETTINGS.database_file).replace("pathfinder.db","core2.db")

async def turn(deps, sid, message, token="guest:test"):
    events=await collect_turn(deps, ChatRequest(session_id=sid,message=message,locale="en",review=False,identity=token), session_id=sid, user_message=message)
    err=next((e.data for e in events if e.event=="error"), None)
    if err: print("ERROR", err)
    return events

async def new(store, token, ctx):
    s=await store.create_session(client_token=token); ctx.session_id=s["id"]; await store.save_context(s["id"], ctx.model_dump()); return s["id"]

async def main():
    store=Store(DB); store.init(); deps=Dependencies(settings=SETTINGS, store=store)
    print("== live agent ==")
    sid=await new(store,"agent",TripContext(session_id="",destination="Hong Kong",days=2,currency="HKD"))
    await turn(deps,sid,"I want to talk to a live agent please")
    s=await store.get_session(sid); print("held",s["held"],"agent",s["agent_name"])
    print("== PII ==")
    sid=await new(store,"pii",TripContext(session_id="",destination="Kyoto",currency="JPY"))
    await turn(deps,sid,"my email is hal@example.com and my phone is 9123 4567, plan a 2-day Kyoto trip")
    rows=await store.list_messages(sid); print("stored:",rows[0]["content"][:160])
    print("== memory ==")
    sid=await new(store,"mem",TripContext(session_id="",destination="Kyoto",currency="JPY",days=2,party_size=2,preferences=["museums","slow pace"]))
    await turn(deps,sid,"Plan a 2-day Kyoto trip for 2 people; we love museums and a slow pace.")
    plan=await store.load_plan(sid); context=await store.load_context(sid)
    if plan:
        added=await remember_trip(container=type("C",(),{"settings":SETTINGS,"store":store})(), identity="guest:mem", plan=TripPlan(**plan), context=TripContext(**context), session_id=sid)
        profile=await store.get_profile("guest:mem"); print("added:",added); print("profile:",(profile or {}).get("preferences"))

asyncio.run(main())
