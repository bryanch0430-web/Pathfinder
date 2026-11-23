import asyncio, os, sys
from pathlib import Path
os.environ.setdefault("PATHFINDER_SANDBOX","1")
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.application.memory import remember_trip
from app.config import get_settings
from app.core.store import Store
from app.domain.models import ChatRequest, TripContext, TripPlan
from app.turn.orchestrator import Dependencies, collect_turn
SETTINGS=get_settings(); DB=str(SETTINGS.database_file).replace("pathfinder.db","memory.db")
async def main():
    store=Store(DB); store.init(); deps=Dependencies(settings=SETTINGS,store=store)
    s=await store.create_session(client_token="mem"); sid=s["id"]
    ctx=TripContext(session_id=sid,destination="Kyoto",origin="Hong Kong",start_date="2027-04-10",end_date="2027-04-11",days=2,party_size=2,budget=200000,currency="JPY",preferences=["museums","slow pace"])
    await store.save_context(sid,ctx.model_dump())
    events=await collect_turn(deps,ChatRequest(session_id=sid,message="Plan a 2-day Kyoto trip for 2 people starting 2027-04-10; we love museums and a slow pace.",locale="en",review=False,identity="guest:mem"),session_id=sid,user_message="plan")
    err=next((e.data for e in events if e.event=="error"),None)
    if err: print("ERR",err)
    plan=await store.load_plan(sid); print("plan",bool(plan))
    if plan:
        added=await remember_trip(container=type("C",(),{"settings":SETTINGS,"store":store})(),identity="guest:mem",plan=TripPlan(**plan),context=ctx,session_id=sid)
        profile=await store.get_profile("guest:mem"); print("added",added); print("profile",(profile or {}).get("preferences"))
asyncio.run(main())
