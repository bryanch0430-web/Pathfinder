"""Journey routes: the trip, its plan, its versions, and the in-place plan actions."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from app.core.currencies import normalise_currency

from app.api.deps import Container, container, require_session
from app.api.views import (
    calendar_ics,
    fingerprint,
    leg_options,
    load_context,
    load_plan,
    next_version,
    save_plan,
    segments_payload,
    session_view,
    translate_plan,
)
from app.domain.models import (
    BookedRequest,
    ChooseRequest,
    CreateSessionRequest,
    DayPlan,
    HotelStay,
    ImportRequest,
    LayoutRequest,
    LockRequest,
    PlanItem,
    ProposalDecision,
    RestoreRequest,
    TripContext,
    TripPlan,
    now_iso,
)
from app.plan.builder import refresh_derived

router = APIRouter()


def _c(request: Request) -> Container:
    return container(request)


@router.post("/api/journeys")
async def create_journey(payload: CreateSessionRequest, request: Request) -> dict[str, Any]:
    c = _c(request)
    token = (request.headers.get("X-Client-Token") or payload.client_token or "").strip()
    from app.api.deps import bearer_user

    user_id = bearer_user(request)
    session = await c.store.create_session(client_token=token, user_id=user_id)
    context = TripContext(
        session_id=session["id"],
        currency=c.settings.default_currency,
        language="en",
    )
    await c.store.save_context(session["id"], context.model_dump())
    return {"session": {"id": session["id"], "created_at": session["created_at"]}, "context": context.model_dump()}


@router.get("/api/journeys/{session_id}")
async def get_journey(session_id: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    session = await require_session(request, session_id)
    return await session_view(c, session_id, session)


@router.get("/api/journeys/{session_id}/held")
async def get_held(session_id: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    session = await require_session(request, session_id)
    seat = await c.store.seat_holder(session_id)
    return {
        "held": bool(session.get("held")),
        "agent_name": session.get("agent_name") or "",
        "planner_busy": bool(seat),
        "planner_note": session.get("note") or "",
    }


@router.patch("/api/journeys/{session_id}/state")
async def patch_state(session_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    context = await load_context(c, session_id)
    merged = context.model_dump()
    for field in (
        "destination", "origin", "start_date", "end_date", "days", "party_size", "budget",
        "currency", "hotel_style", "pace", "preferences", "hard_constraints", "language",
        "plan_locale", "stops", "one_way", "finalized",
    ):
        if field in payload and payload[field] is not None:
            if field == "currency":
                code = normalise_currency(payload[field])
                if not code:
                    raise HTTPException(status_code=400, detail="Use a three-letter currency code such as TWD, JPY or USD.")
                merged[field] = code
                continue
            merged[field] = payload[field]
    if isinstance(payload.get("profile"), dict):
        merged["profile"] = payload["profile"]
    context = TripContext.model_validate(merged)
    await c.store.save_context(session_id, context.model_dump())
    return {"context": context.model_dump()}


@router.get("/api/journeys/{session_id}/revisions")
async def revisions(session_id: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    return {"versions": await c.store.list_versions(session_id)}


@router.get("/api/journeys/{session_id}/itinerary")
async def itinerary(session_id: str, request: Request, version: int | None = None) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id, version)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary yet.")
    return {"plan": plan.model_dump(mode="json")}


@router.post("/api/journeys/{session_id}/itinerary/restore")
async def restore(session_id: str, payload: RestoreRequest, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id, payload.version)
    if plan is None:
        raise HTTPException(status_code=404, detail="That version is not there.")
    plan = await save_plan(c, plan, f"Restored version {payload.version}.")
    return {"plan": plan.model_dump(mode="json")}


@router.post("/api/journeys/{session_id}/layout")
async def apply_layout(session_id: str, payload: LayoutRequest, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary to rearrange.")
    if payload.plan_version and payload.plan_version != plan.version:
        plan = await load_plan(c, session_id, payload.plan_version) or plan
    context = await load_context(c, session_id)
    index = plan.item_index()
    for row in payload.items:
        item = index.get(row.id)
        target_day = _day_number(plan, row.day)
        if item is None or target_day is None:
            continue
        _detach(plan, item.id)
        item.start = row.start or item.start
        item.end = row.end or item.end
        if row.name:
            item.name = row.name[:160]
        target_day.items.append(item)
    for drop in payload.drops:
        _detach(plan, drop)
    for day in plan.days:
        day.items.sort(key=lambda entry: entry.start or "99:99")
    refresh_derived(plan, context)
    plan = await save_plan(c, plan, "Rearranged the plan by hand.")
    return {"plan": plan.model_dump(mode="json")}


@router.post("/api/journeys/{session_id}/proposals/{message_id}")
async def decide_proposal(
    session_id: str, message_id: str, payload: ProposalDecision, request: Request
) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    message = await c.store.get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="That change is no longer there.")
    meta = message.get("meta") or {}
    proposal = meta.get("proposal") or {}
    changes = proposal.get("changes") or []
    change = next((row for row in changes if row.get("id") == payload.change_id), None)
    if change is None:
        raise HTTPException(status_code=404, detail="That change is no longer there.")
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary to change.")
    if payload.accept:
        candidate = change.get("plan")
        if not isinstance(candidate, dict):
            raise HTTPException(status_code=400, detail="That change has no plan to apply.")
        new_plan = TripPlan(**candidate)
        context = await load_context(c, session_id)
        new_plan = await save_plan(c, new_plan, change.get("label") or "Applied a change.")
        plan = new_plan
        refresh_derived(plan, context)
        await c.store.save_plan(plan.model_dump(mode="json"))
    for row in changes:
        if row.get("id") == payload.change_id:
            row["status"] = "accepted" if payload.accept else "rejected"
    meta["proposal"] = {**proposal, "changes": changes}
    await c.store.update_message(message_id, meta=meta)
    message["meta"] = meta
    return {"plan": plan.model_dump(mode="json"), "message": message}


@router.post("/api/journeys/{session_id}/plan/choose")
async def choose_option(session_id: str, payload: ChooseRequest, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary yet.")
    context = await load_context(c, session_id)
    changed = False
    if payload.kind == "item":
        item = plan.item_index().get(payload.target_id or "")
        if item is None:
            raise HTTPException(status_code=404, detail="That stop is not on the plan.")
        alternative = next((row for row in item.alternatives if row.id == payload.option_id), None)
        if alternative is None:
            raise HTTPException(status_code=404, detail="That option is not listed for the stop.")
        item.name = alternative.name
        item.geo = alternative.geo
        item.address = alternative.address
        item.rating = alternative.rating
        item.review_count = alternative.review_count
        item.price_level = alternative.price_level
        item.cuisine = alternative.cuisine
        item.opening_hours = list(alternative.opening_hours)
        item.source_refs = list(alternative.source_refs)
        item.booking_url = alternative.booking_url
        item.links = list(alternative.links)
        item.image = alternative.image
        item.source_summary = alternative.source_summary
        item.locked = bool(payload.lock)
        changed = True
    elif payload.kind == "hotel":
        chosen = next((row for row in plan.hotel_options if row.id == payload.option_id), None)
        if chosen is None:
            raise HTTPException(status_code=404, detail="That stay is not listed.")
        chosen = chosen.model_copy(deep=True)
        chosen.locked = bool(payload.lock)
        chosen.check_in = plan.hotels[0].check_in if plan.hotels else plan.dates.start
        chosen.check_out = plan.hotels[0].check_out if plan.hotels else plan.dates.end
        plan.hotels = [chosen]
        changed = True
    elif payload.kind in {"flight", "transport"}:
        chosen = next((row for row in plan.tickets if row.id == payload.option_id), None)
        if chosen is None:
            raise HTTPException(status_code=404, detail="That travel option is not listed.")
        for ticket in plan.tickets:
            ticket.selected = ticket.id == chosen.id
        leg = next((row for row in plan.legs if row.id == (chosen.leg_id or "leg-out")), None)
        if leg is not None:
            leg.locked = bool(payload.lock)
        changed = True
    if not changed:
        raise HTTPException(status_code=400, detail="That choice did not change anything.")
    refresh_derived(plan, context)
    plan = await save_plan(c, plan, f"Chose another option ({payload.kind}).")
    return {"plan": plan.model_dump(mode="json")}


@router.post("/api/journeys/{session_id}/lock")
async def set_lock(session_id: str, payload: LockRequest, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary yet.")
    if payload.kind == "hotel":
        hotel = next((row for row in plan.hotels if row.id == payload.target_id), None)
        if hotel is None:
            raise HTTPException(status_code=404, detail="That stay is not on the plan.")
        hotel.locked = payload.locked
    else:
        leg = next((row for row in plan.legs if row.id == payload.target_id), None)
        if leg is None:
            raise HTTPException(status_code=404, detail="That leg is not on the plan.")
        leg.locked = payload.locked
    await c.store.save_plan(plan.model_dump(mode="json"))
    return {"plan": plan.model_dump(mode="json")}


@router.post("/api/journeys/{session_id}/booked")
async def set_booked(session_id: str, payload: BookedRequest, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary yet.")
    if payload.kind == "hotel":
        hotel = next((row for row in plan.hotels if row.id == payload.target_id), None)
        if hotel is None:
            raise HTTPException(status_code=404, detail="That stay is not on the plan.")
        hotel.booked = payload.booked
        hotel.confirmed = payload.booked
    else:
        leg = next((row for row in plan.legs if row.id == payload.target_id), None)
        if leg is None:
            raise HTTPException(status_code=404, detail="That leg is not on the plan.")
        leg.booked = payload.booked
    await c.store.save_plan(plan.model_dump(mode="json"))
    return {"plan": plan.model_dump(mode="json")}


@router.get("/api/journeys/{session_id}/plan/hotels/{hotel_id}/rooms")
async def hotel_rooms(session_id: str, hotel_id: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        return {"rooms": []}
    pool = [*plan.hotels, *plan.hotel_options]
    hotel = next((row for row in pool if row.id == hotel_id), None)
    return {"rooms": [room.model_dump() for room in (hotel.room_types if hotel else [])]}


@router.post("/api/journeys/{session_id}/finalise")
async def finalise(session_id: str, request: Request, background: BackgroundTasks) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    context = await load_context(c, session_id)
    # Finalise is idempotent: a double-click must not create a second saved trip.
    if context.finalized:
        return {"context": context.model_dump()}
    context.finalized = True
    await c.store.save_context(session_id, context.model_dump())
    plan = await load_plan(c, session_id)
    if plan is not None:
        plan.status = "ready"
        await save_plan(c, plan, "Finalised the trip.")
        from app.api.deps import identity_for
        from app.application.memory import remember_trip

        identity = identity_for(request)
        if identity:
            # Save the trip immediately. Memory extraction is a model call and
            # belongs in the background; it must not make Finalise feel frozen.
            await c.store.save_wishlist(
                {
                    # One stable row per trip, so a retry updates instead of duplicating.
                    "id": f"wish-{session_id}"[:36],
                    "session_id": session_id,
                    "identity": identity,
                    "destination": plan.destination.name,
                    "summary": plan.summary,
                    "stars": 5,
                    "body": plan.model_dump(mode="json"),
                    "created_at": now_iso(),
                }
            )
            background.add_task(
                remember_trip,
                container=c,
                identity=identity,
                plan=plan,
                context=context,
                session_id=session_id,
            )
    return {"context": context.model_dump()}


@router.post("/api/journeys/{session_id}/rating")
async def rate(
    session_id: str,
    payload: dict[str, Any],
    request: Request,
    background: BackgroundTasks,
) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    feedback_id = await c.store.save_feedback(session_id, bool(payload.get("useful")), payload.get("stars"))
    if payload.get("useful"):
        from app.api.deps import identity_for
        from app.application.memory import remember_trip

        identity = identity_for(request, payload.get("client_token"))
        plan = await load_plan(c, session_id)
        context = await load_context(c, session_id)
        if identity and plan is not None:
            # Memory extraction is a model call; it must not hold the star click for seconds.
            background.add_task(
                remember_trip,
                container=c,
                identity=identity,
                plan=plan,
                context=context,
                session_id=session_id,
            )
    return {"feedback": {"id": feedback_id}, "preferences_added": []}


@router.post("/api/journeys/{session_id}/share")
async def share(session_id: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    slug = secrets.token_urlsafe(10)
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    await c.store.save_share(slug, session_id, expires)
    return {"slug": slug, "url": f"/s/{slug}", "expires_at": expires}


@router.delete("/api/journeys/{session_id}/share")
async def revoke_share(session_id: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    return {"revoked": await c.store.revoke_shares(session_id)}


@router.get("/api/public/{slug}")
async def public_plan(slug: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    row = await c.store.get_share(slug)
    if row is None:
        raise HTTPException(status_code=404, detail="That shared trip is not there.")
    if row["expires_at"] < now_iso():
        raise HTTPException(status_code=410, detail="That shared trip has expired.")
    plan = await load_plan(c, row["session_id"])
    context = await load_context(c, row["session_id"])
    return {"plan": plan.model_dump(mode="json") if plan else None, "context": context.model_dump(mode="json")}


@router.get("/s/{slug}", response_class=Response)
async def public_page(slug: str, request: Request) -> Response:
    c = _c(request)
    row = await c.store.get_share(slug)
    if row is None:
        return Response("This shared trip is not here.", status_code=404, media_type="text/plain")
    plan = await load_plan(c, row["session_id"])
    if plan is None:
        return Response("This trip has no itinerary yet.", status_code=404, media_type="text/plain")
    rows = []
    for index, day in enumerate(plan.days, start=1):
        stops = "".join(f"<li><b>{item.start}</b> {item.name}</li>" for item in day.items)
        rows.append(f"<h2>Day {index} · {day.date} · {day.title}</h2><ul>{stops}</ul>")
    html = (
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{plan.destination.name} trip</title>"
        "<style>body{font:16px/1.55 system-ui;margin:0 auto;max-width:44rem;padding:2rem 1rem;color:#16202a}"
        "h1{font-size:1.7rem}h2{font-size:1.05rem;margin-top:1.6rem;color:#0b5c66}li{margin:.25rem 0}"
        ".s{color:#5b6b78}</style>"
        f"<h1>{plan.destination.name}</h1><p class='s'>{plan.dates.start} → {plan.dates.end} · "
        f"{plan.party_size} traveller(s)</p><p>{plan.summary}</p>{''.join(rows)}"
        "<p class='s'>Shared from Pathfinder.</p>"
    )
    return Response(html, media_type="text/html")


@router.get("/api/journeys/{session_id}/translation")
async def translation(session_id: str, request: Request, version: int, locale: str = "en") -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id, version)
    if plan is None:
        raise HTTPException(status_code=404, detail="That version is not there.")
    return await translate_plan(c, plan, locale)


@router.get("/api/journeys/{session_id}/geometry")
async def geometry(session_id: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        return {"days": []}
    return {
        "days": [
            {
                "date": day.date,
                "segment_id": day.segment_id,
                "points": [
                    {"id": item.id, "name": item.name, "lat": item.geo.lat, "lng": item.geo.lng}
                    for item in day.items
                    if item.geo
                ],
            }
            for day in plan.days
        ]
    }


@router.get("/api/journeys/{session_id}/segments")
async def segments(session_id: str, request: Request, day: str | None = None) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        return {"legs": [], "anchors": []}
    return segments_payload(plan, day)


@router.get("/api/journeys/{session_id}/leg-options")
async def get_leg_options(session_id: str, request: Request, from_id: str, to_id: str) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary yet.")
    return leg_options(plan, from_id, to_id)


@router.get("/api/journeys/{session_id}/calendar.ics")
async def calendar(session_id: str, request: Request) -> Response:
    c = _c(request)
    await require_session(request, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary yet.")
    return Response(
        calendar_ics(plan),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="pathfinder.ics"'},
    )


@router.post("/api/journeys/import")
async def import_plan(payload: ImportRequest, request: Request) -> dict[str, Any]:
    c = _c(request)
    token = (request.headers.get("X-Client-Token") or payload.client_token or "").strip()
    from app.api.deps import bearer_user

    session = await c.store.create_session(client_token=token, user_id=bearer_user(request))
    raw = payload.plan or {}
    try:
        plan = TripPlan(**raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"That file is not a Pathfinder plan: {exc}") from exc
    plan.session_id = session["id"]
    plan.version = 1
    plan.trip_id = plan.trip_id or "trip-" + fingerprint(session["id"])
    await c.store.save_plan(plan.model_dump(mode="json"))
    context = TripContext(
        session_id=session["id"],
        destination=plan.destination.name,
        origin=plan.origin,
        start_date=plan.dates.start,
        end_date=plan.dates.end,
        days=len(plan.days),
        party_size=plan.party_size,
        budget=plan.budget.amount if plan.budget else None,
        currency=plan.currency if hasattr(plan, "currency") else (plan.budget.currency if plan.budget else c.settings.default_currency),
        language="en",
    )
    await c.store.save_context(session["id"], context.model_dump())
    session_row = await c.store.get_session(session["id"])
    return await session_view(c, session["id"], session_row)


@router.delete("/api/journeys/{session_id}")
async def delete_journey(session_id: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    return {"deleted": await c.store.delete_session(session_id)}


@router.delete("/api/journeys/{session_id}/turns")
async def clear_turns(session_id: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    return {"deleted": await c.store.clear_messages(session_id)}


def _day_number(plan: TripPlan, day: int) -> DayPlan | None:
    return plan.days[day - 1] if 1 <= day <= len(plan.days) else None


def _detach(plan: TripPlan, item_id: str) -> None:
    for day in plan.days:
        day.items = [item for item in day.items if item.id != item_id]
