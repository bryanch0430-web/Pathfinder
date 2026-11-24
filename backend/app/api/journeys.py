"""Journey routes: the trip, its plan, its versions, and the in-place plan actions."""

from __future__ import annotations

import html
import json
import math
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import ValidationError

from app.core.currencies import normalise_currency

from app.api.deps import Container, container, rate_limit, require_session
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
    PlanAlternative,
    PlanItem,
    ProposalDecision,
    RestoreRequest,
    TripContext,
    TripPlan,
    now_iso,
)
from app.plan.builder import refresh_derived
from app.plan.import_screen import clean_text, screen_plan

router = APIRouter()

MAX_IMPORT_SUMMARY = 20_000
MAX_IMPORT_MESSAGES = 200
MAX_IMPORT_MESSAGE_CHARS = 8_000
# A hand-edited or generated file is a few hundred kilobytes at most; anything
# larger is a body meant to exhaust the server, not a trip.
MAX_IMPORT_PLAN_CHARS = 1_500_000


def _c(request: Request) -> Container:
    return container(request)


@router.post("/api/journeys")
async def create_journey(payload: CreateSessionRequest, request: Request) -> dict[str, Any]:
    c = _c(request)
    rate_limit(request, "create", 120, 3600.0)
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
        if field not in payload:
            continue
        value = payload[field]
        if value is None:
            # Clear details sends null for the optional fields. Ignoring them made
            # the old dates, party size and budget come back when the form saved.
            if field in {"start_date", "end_date", "days", "party_size", "budget"}:
                merged[field] = None
            continue
        if field == "currency":
            code = normalise_currency(value)
            if not code:
                raise HTTPException(status_code=400, detail="Use a three-letter currency code such as TWD, JPY or USD.")
            merged[field] = code
            merged["currency_set"] = True
            continue
        merged[field] = _checked_state(field, value)
    if isinstance(payload.get("profile"), dict):
        merged["profile"] = payload["profile"]
    try:
        context = TripContext.model_validate(merged)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ())) or "state"
        raise HTTPException(status_code=422, detail=f"The value for {where} is not valid.") from exc
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
    await _require_open(c, session_id)
    plan = await load_plan(c, session_id, payload.version)
    if plan is None:
        raise HTTPException(status_code=404, detail="That version is not there.")
    plan = await save_plan(c, plan, f"Restored version {payload.version}.")
    return {"plan": plan.model_dump(mode="json")}


@router.post("/api/journeys/{session_id}/layout")
async def apply_layout(session_id: str, payload: LayoutRequest, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    context = await _require_open(c, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary to rearrange.")
    if payload.plan_version and payload.plan_version != plan.version:
        plan = await load_plan(c, session_id, payload.plan_version) or plan
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
    # A message of another trip is "not there" for this one.
    if message is None or message.pop("session_id", None) != session_id:
        raise HTTPException(status_code=404, detail="That change is no longer there.")
    meta = message.get("meta") or {}
    proposal = meta.get("proposal") or {}
    changes = proposal.get("changes") or []
    change = next((row for row in changes if row.get("id") == payload.change_id), None)
    if change is None:
        raise HTTPException(status_code=404, detail="That change is no longer there.")
    if (change.get("status") or "pending") != "pending":
        raise HTTPException(status_code=409, detail="That change was already decided.")
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary to change.")
    if payload.accept:
        context = await _require_open(c, session_id)
        candidate = change.get("plan")
        if not isinstance(candidate, dict):
            raise HTTPException(status_code=400, detail="That change has no plan to apply.")
        # The suggestion was written on top of the version before its own; if the
        # plan moved on since, applying it would throw the newer edits away.
        base = candidate.get("version")
        if isinstance(base, int) and plan.version != base - 1:
            raise HTTPException(
                status_code=409,
                detail="The plan has changed since this suggestion was made, so it can no longer be applied. Ask again for a fresh one.",
            )
        try:
            new_plan = TripPlan(**candidate)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail="That change has no valid plan to apply.") from exc
        new_plan.session_id = session_id
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
    context = await _require_open(c, session_id)
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no itinerary yet.")
    changed = False
    if payload.kind == "item":
        item = plan.item_index().get(payload.target_id or "")
        if item is None:
            raise HTTPException(status_code=404, detail="That stop is not on the plan.")
        alternative = next((row for row in item.alternatives if row.id == payload.option_id), None)
        if alternative is None:
            raise HTTPException(status_code=404, detail="That option is not listed for the stop.")
        # The stop being replaced becomes an alternative, so it can be switched back.
        taken = {row.id for row in item.alternatives if row.id != alternative.id}
        previous_id = f"{item.id}-prev"
        number = 1
        while previous_id in taken:
            number += 1
            previous_id = f"{item.id}-prev{number}"
        previous = PlanAlternative(
            id=previous_id,
            name=item.name,
            geo=item.geo,
            address=item.address,
            rating=item.rating,
            review_count=item.review_count,
            price_level=item.price_level,
            cuisine=item.cuisine,
            reservable=item.reservable,
            opening_hours=list(item.opening_hours),
            source_refs=list(item.source_refs),
            booking_url=item.booking_url,
            source_types=list(item.source_types),
            source_summary=item.source_summary,
            links=list(item.links),
            image=item.image,
        )
        item.alternatives = [previous if row.id == alternative.id else row for row in item.alternatives]
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
        current = None
        if payload.target_id:
            current = next((row for row in plan.hotels if row.id == payload.target_id), None)
            if current is None:
                raise HTTPException(status_code=404, detail="That stay is not on the plan.")
        elif len(plan.hotels) == 1:
            current = plan.hotels[0]
        elif plan.hotels:
            # Several stays (a multi-city trip): the option's own city decides which one.
            wanted = chosen.segment_id if chosen is not None else None
            current = next((row for row in plan.hotels if wanted and row.segment_id == wanted), None)
            if current is None:
                raise HTTPException(status_code=404, detail="Say which stay to change.")
        # The current stay is also a lockable option: it need not appear again in
        # the alternative list. This is the common "Lock in" path for a hotel.
        if chosen is None:
            chosen = current
        if chosen is None:
            raise HTTPException(status_code=404, detail="That stay is not listed.")
        chosen = chosen.model_copy(deep=True)
        chosen.locked = bool(payload.lock)
        if current is not None:
            chosen.check_in = current.check_in
            chosen.check_out = current.check_out
            chosen.segment_id = current.segment_id or chosen.segment_id
            # Keep the other city's stays; replacing the whole list would drop
            # them on a multi-city trip.
            plan.hotels = [chosen if row.id == current.id else row for row in plan.hotels]
        else:
            chosen.check_in = plan.hotels[0].check_in if plan.hotels else plan.dates.start
            chosen.check_out = plan.hotels[0].check_out if plan.hotels else plan.dates.end
            plan.hotels = [chosen]
        changed = True
    elif payload.kind in {"flight", "transport"}:
        chosen = next((row for row in plan.tickets if row.id == payload.option_id), None)
        if chosen is None:
            raise HTTPException(status_code=404, detail="That travel option is not listed.")
        # On a multi-city trip each city has its own ticket; only that city's pick changes.
        same_trip = plan.tickets
        if len(plan.segments) > 1:
            city = (chosen.destination or "").strip().lower()
            same_trip = [row for row in plan.tickets if (row.destination or "").strip().lower() == city]
        for ticket in same_trip:
            ticket.selected = ticket.id == chosen.id
        # The UI locks the slot it is showing (leg-back), not the ticket's
        # source leg. Return options can share the outbound leg_id in the
        # provider payload; use target_id first so locking the return works.
        leg = next((row for row in plan.legs if row.id == payload.target_id), None)
        if leg is None:
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
    await _require_open(c, session_id)
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
async def finalise(session_id: str, request: Request) -> dict[str, Any]:
    c = _c(request)
    await require_session(request, session_id)
    context = await load_context(c, session_id)
    # Finalise is idempotent: a double-click must not create a second saved trip.
    if context.finalized:
        return {"context": context.model_dump()}
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=409, detail="There is no itinerary to finalise yet.")
    context.finalized = True
    await c.store.save_context(session_id, context.model_dump())
    if plan is not None:
        plan.status = "ready"
        await save_plan(c, plan, "Finalised the trip.")
        from app.api.deps import identity_for

        identity = identity_for(request)
        if identity:
            # The trip is saved now, but it is not useful yet. The Useful /
            # Not-useful choice and the 1–5 stars arrive from the rating step.
            await c.store.save_wishlist(
                {
                    # One stable row per trip, so a retry updates instead of duplicating.
                    "id": f"wish-{session_id}"[:36],
                    "session_id": session_id,
                    "identity": identity,
                    "destination": plan.destination.name,
                    "summary": plan.summary,
                    "stars": None,
                    "useful": None,
                    "body": plan.model_dump(mode="json"),
                    "created_at": now_iso(),
                }
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
    useful = payload.get("useful", False)
    if not isinstance(useful, bool):
        raise HTTPException(status_code=422, detail="useful must be true or false.")
    raw_stars = payload.get("stars")
    stars: int | None = None
    if useful and raw_stars is not None:
        try:
            value = int(raw_stars)
        except (TypeError, ValueError):
            value = 0
        if 1 <= value <= 5:
            stars = value
    feedback_id = await c.store.save_feedback(session_id, useful, stars)
    from app.api.deps import identity_for
    from app.application.memory import remember_trip

    identity = identity_for(request, payload.get("client_token"))
    if identity:
        # My Trips carries the same verdict; only a useful, starred trip enters
        # the trending pool.
        await c.store.update_wishlist_feedback(session_id, identity, useful, stars)
    if useful:
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
    # The session id is the only credential of a trip without an owner: never share it.
    plan_json = plan.model_dump(mode="json") if plan else None
    if plan_json:
        plan_json.pop("session_id", None)
    context_json = context.model_dump(mode="json")
    context_json.pop("session_id", None)
    return {"plan": plan_json, "context": context_json}


@router.get("/s/{slug}", response_class=Response)
async def public_page(slug: str, request: Request) -> Response:
    c = _c(request)
    row = await c.store.get_share(slug)
    if row is None:
        return Response("This shared trip is not here.", status_code=404, media_type="text/plain")
    if row["expires_at"] < now_iso():
        return Response("That shared trip has expired.", status_code=410, media_type="text/plain")
    plan = await load_plan(c, row["session_id"])
    if plan is None:
        return Response("This trip has no itinerary yet.", status_code=404, media_type="text/plain")
    esc = html.escape
    rows = []
    for index, day in enumerate(plan.days, start=1):
        stops = "".join(f"<li><b>{esc(item.start)}</b> {esc(item.name)}</li>" for item in day.items)
        rows.append(f"<h2>Day {index} · {esc(day.date)} · {esc(day.title)}</h2><ul>{stops}</ul>")
    page = (
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(plan.destination.name)} trip</title>"
        "<style>body{font:16px/1.55 system-ui;margin:0 auto;max-width:44rem;padding:2rem 1rem;color:#16202a}"
        "h1{font-size:1.7rem}h2{font-size:1.05rem;margin-top:1.6rem;color:#0b5c66}li{margin:.25rem 0}"
        ".s{color:#5b6b78}</style>"
        f"<h1>{esc(plan.destination.name)}</h1><p class='s'>{esc(plan.dates.start)} → {esc(plan.dates.end)} · "
        f"{int(plan.party_size)} traveller(s)</p><p>{esc(plan.summary)}</p>{''.join(rows)}"
        "<p class='s'>Shared from Pathfinder.</p>"
    )
    return Response(page, media_type="text/html")


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
    rate_limit(request, "import", 30, 3600.0)
    token = (request.headers.get("X-Client-Token") or payload.client_token or "").strip()
    from app.api.deps import bearer_user

    raw = dict(payload.plan or {})
    if _plan_too_large(raw):
        raise HTTPException(status_code=413, detail="That plan file is too large to import.")
    # A conversation may sit beside the plan or, in a hand-edited file, on it.
    # It is removed before validation so an old plan file still opens.
    embedded = raw.pop("messages", None)
    if isinstance(raw.get("summary"), str) and len(raw["summary"]) > MAX_IMPORT_SUMMARY:
        raise HTTPException(status_code=413, detail="That plan's summary is too long to import.")
    # A file from someone else is data: unsafe text and links are neutralised field by field.
    raw, warnings, threats = screen_plan(raw)
    try:
        plan = TripPlan(**raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"That file is not a Pathfinder plan: {exc}") from exc
    # Only a valid plan gets a session, so a bad file leaves nothing behind.
    session = await c.store.create_session(client_token=token, user_id=bearer_user(request))
    plan.session_id = session["id"]
    plan.version = 1
    plan.trip_id = "trip-" + fingerprint(session["id"])
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
    supplied = payload.messages if payload.messages else embedded
    skipped: list[str] = []
    for message in _imported_messages(supplied, skipped):
        await c.store.add_message(session["id"], message)
    warnings += skipped
    await _count_import_threats(c, threats + len(skipped))
    session_row = await c.store.get_session(session["id"])
    view = await session_view(c, session["id"], session_row)
    view["warnings"] = warnings[:20]
    return view


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


async def _require_open(c: Container, session_id: str) -> TripContext:
    """The trip's context, or 409 when the trip is finalised and so read-only."""
    context = await load_context(c, session_id)
    if context.finalized:
        raise HTTPException(status_code=409, detail="This trip is finalised, so it can be viewed but not changed.")
    return context


def _plan_too_large(raw: dict[str, Any]) -> bool:
    return len(json.dumps(raw, ensure_ascii=False)) > MAX_IMPORT_PLAN_CHARS


async def _count_import_threats(c: Container, count: int) -> None:
    """Add to the admin threat counters. Never fails an import."""
    if count <= 0:
        return
    try:
        counters = await c.store.get_kv("threats") or {}
        if isinstance(counters, dict):
            counters["import_text"] = int(counters.get("import_text", 0) or 0) + count
            await c.store.set_kv("threats", counters)
    except Exception:  # noqa: BLE001
        return


def _imported_messages(rows: Any, warnings: list[str] | None = None) -> list[dict[str, Any]]:
    """The conversation to restore. A bad row is skipped; it never rejects the plan."""
    if not isinstance(rows, list):
        return []
    restored: list[dict[str, Any]] = []
    for row in rows[:MAX_IMPORT_MESSAGES]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "assistant")
        # A file must not insert a system instruction into later turns.
        if role not in {"user", "assistant"}:
            continue
        content, unsafe = clean_text(str(row.get("content") or ""), MAX_IMPORT_MESSAGE_CHARS)
        if unsafe:
            if warnings is not None and len(warnings) < 20:
                warnings.append(f"messages[{len(restored)}]: message left out (unsafe text)")
            continue
        if not content.strip():
            continue
        intent = row.get("intent")
        if intent not in {"plan", "modify", "ask", "unclear"}:
            intent = None
        when = str(row.get("created_at") or row.get("at") or now_iso())[:40]
        # A hand-edited file cannot appoint itself the operator. The imported mark
        # also keeps this line out of the model's own history.
        safe_meta: dict[str, Any] = {"imported": True}
        restored.append(
            {
                "id": secrets.token_hex(8),
                "role": role,
                "content": content,
                "intent": intent,
                "created_at": when,
                "meta": safe_meta,
            }
        )
    return restored


def _checked_state(field: str, value: Any) -> Any:
    def bad(message: str) -> HTTPException:
        return HTTPException(status_code=422, detail=message)

    def number(low: float | None = None, high: float | None = None, *, whole: bool = False) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise bad(f"{field} must be a number.")
        if whole and value != int(value):
            raise bad(f"{field} must be a whole number.")
        if (low is not None and value < low) or (high is not None and value > high):
            raise bad(f"{field} must be between {low:g} and {high:g}." if high is not None else f"{field} must be at least {low:g}.")
        return int(value) if whole else value

    if field == "party_size":
        return number(1, 20, whole=True)
    if field == "days":
        return number(1, 60, whole=True)
    if field == "budget":
        return number(0)
    if field in {"start_date", "end_date"}:
        if not isinstance(value, str):
            raise bad(f"{field} must be a date such as 2026-12-03.")
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise bad(f"{field} must be a date such as 2026-12-03.") from exc
        return value
    if field in {"preferences", "hard_constraints"}:
        if not isinstance(value, list) or not all(isinstance(row, str) for row in value):
            raise bad(f"{field} must be a list of text values.")
        return value
    if field in {"finalized", "one_way"}:
        if not isinstance(value, bool):
            raise bad(f"{field} must be true or false.")
        return value
    if field == "stops":
        if not isinstance(value, list):
            raise bad("stops must be a list.")
        return value
    if not isinstance(value, str):
        raise bad(f"{field} must be text.")
    return value


def _day_number(plan: TripPlan, day: int) -> DayPlan | None:
    return plan.days[day - 1] if 1 <= day <= len(plan.days) else None


def _detach(plan: TripPlan, item_id: str) -> None:
    for day in plan.days:
        day.items = [item for item in day.items if item.id != item_id]
