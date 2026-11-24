"""Compose a :class:`TripPlan` from a model's choices and sourced evidence.

The model decides *what goes where and when*; this module turns that decision into
the wire model.  Every fact on a stop comes from the evidence record the model
named, so a plan cannot contain a hallucinated place, price or address.  The parts
that must be consistent (dates, ordering, overlaps, transfers, cost, sources) are
settled here in code.
"""

from __future__ import annotations

import hashlib
import json
import time
from urllib.parse import quote
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.config import Settings
from app.core.dates import add_days, clock_minutes, day_range, minutes_between, overlaps, parse_date, shift_time
from app.core.llm import LLMClient, LLMError
from app.core.prompts import context_brief, skill
from app.core.trace import TurnTrace
from app.domain.models import (
    CostBreakdown,
    CostLine,
    ConstraintCheck,
    DataGap,
    DateRange,
    DayPlan,
    Destination,
    Disruption,
    GeoPoint,
    HotelStay,
    ImageRef,
    Money,
    PlanAssessment,
    PlanAlternative,
    PlanItem,
    Reservation,
    SourceRef,
    TicketOption,
    TransferLeg,
    TransportLeg,
    TripContext,
    TripPlan,
    TripSegment,
    WeatherDay,
    now_iso,
)
from app.evidence.models import Bundle, Evidence
from app.evidence.providers import _same_city, haversine_km
from app.plan.agents import candidate_pool
from app.plan.partial import partial_days
from app.understand.schemas import DayChoice, PlanDraft, StopChoice

DEFAULT_SLOTS = ["09:30", "11:30", "13:00", "15:00", "17:00", "19:00"]
DEFAULT_LENGTHS = {"attraction": 90, "meal": 75}


@dataclass
class ComposeResult:
    plan: TripPlan
    draft: PlanDraft
    problems: list["Problem"]


@dataclass
class Problem:
    code: str
    message: str
    day: int | None = None
    item_id: str | None = None


def _draft_route(context: TripContext, geo_by_name: dict[str, GeoPoint]) -> dict[str, Any] | None:
    """The trip's two ends, for the draft's flight arc, when coordinates are known."""
    from app.evidence import place_corpus as corpus

    def point(name: str) -> dict[str, Any] | None:
        clean = (name or "").strip()
        if not clean:
            return None
        geo = geo_by_name.get(clean.lower())
        if geo is None:
            meta = corpus.city_meta(clean)
            if meta:
                return {"name": meta["name"], "lat": meta["lat"], "lng": meta["lng"]}
        if geo is not None:
            return {"name": clean, "lat": geo.lat, "lng": geo.lng}
        return {"name": clean, "lat": None, "lng": None}

    origin = point(context.origin)
    destination = point(context.destination)
    if not origin or not destination:
        return None
    return {"from": origin, "to": destination}


async def compose(
    *,
    llm: LLMClient,
    settings: Settings,
    context: TripContext,
    bundle: Bundle,
    disruptions: list[Disruption],
    notes: list[str],
    previous_problems: list[Problem] | None = None,
    skill_overrides: dict[str, str] | None = None,
    trace: TurnTrace | None = None,
    avoid_transit_days: set[int] | None = None,
    emit: Any | None = None,
    deadline: float | None = None,
) -> tuple[TripPlan, PlanDraft]:
    start = parse_date(context.start_date)
    if start is None:
        raise ValueError("no_start_date")
    days = int(context.days or 1)
    end = parse_date(context.end_date) or add_days(start, days)
    weather_by_date = {day.date: day for day in bundle.weather}
    payload = {
        "trip": context_brief(context),
        "dates": [day.isoformat() for day in day_range(start, end)][:31],
        "weather": [
            {
                "date": day.date,
                "summary": day.summary,
                "high_c": day.high_c,
                "low_c": day.low_c,
                "rain_chance": day.precipitation_probability,
                "status": day.status,
            }
            for day in bundle.weather
        ],
        "candidates": candidate_pool(bundle),
        "disruptions": notes,
        "previous_problems": [
            {"code": problem.code, "message": problem.message} for problem in (previous_problems or [])
        ][:8],
        "destination": bundle.notes[:0] or None,
    }
    system = skill("plan", skill_overrides)
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    # A seven-day answer is roughly twice a four-day one. A fixed cap truncated
    # it, the truncation failed validation, and the retry repeated the same cut.
    token_cap = min(16000, 4000 + 900 * max(2, int(context.days or 4)))
    draft: PlanDraft | None = None
    if emit is not None:
        # The day sections appear while the composer writes them. A failure
        # anywhere in the stream falls back to the ordinary whole-answer call.
        places = {
            row["ref"]: row
            for group in candidate_pool(bundle).values()
            for row in group
            if row.get("ref")
        }
        geo_by_name = {
            item.name.strip().lower(): item.geo
            for item in bundle.items
            if item.geo is not None
        }
        route = _draft_route(context, geo_by_name)
        last = {"at": 0.0, "sig": None}
        # Tell the page at once that a plan is being written, before the first
        # day exists, so the workspace is not blank for the first half minute.
        await emit("plan_partial", {"days": [], "started": True, "route": route})

        def payload_days(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for day in days:
                stops: list[dict[str, Any]] = []
                for stop in day["stops"]:
                    row = places.get(stop["ref"])
                    if not row:
                        continue
                    geo = geo_by_name.get(str(row.get("name") or "").strip().lower())
                    stops.append(
                        {
                            "name": row.get("name", ""),
                            "start": stop["start"],
                            "end": stop["end"],
                            "lat": geo.lat if geo else None,
                            "lng": geo.lng if geo else None,
                        }
                    )
                rows.append({"date": day["date"], "title": day["title"], "stops": stops[:8]})
            return rows

        async def on_text(text: str) -> None:
            now = time.monotonic()
            if now - last["at"] < 0.35:
                return
            last["at"] = now
            days = partial_days(text)
            if not days:
                return
            # A stop added to a day already shown is a change too, so compare the
            # whole shape rather than only how many days exist.
            signature = tuple((day["date"], day["title"], len(day["stops"])) for day in days)
            if signature == last["sig"]:
                return
            last["sig"] = signature
            await emit("plan_partial", {"days": payload_days(days), "route": route})

        try:
            draft = await llm.json_stream(
                purpose="plan",
                system=system,
                user=user,
                schema=PlanDraft,
                on_text=on_text,
                max_tokens=token_cap,
                deadline=deadline,
            )
        except LLMError:
            # Without enough turn budget left, a second full generation would
            # only time out. Re-raise so the caller can build its fast fallback.
            if deadline is not None and deadline - time.monotonic() < 90:
                raise
            draft = None
    if draft is None:
        draft = await llm.json(
            purpose="plan",
            system=system,
            user=user,
            schema=PlanDraft,
            max_tokens=token_cap,
        )
    plan = build_plan(
        context=context,
        bundle=bundle,
        draft=draft,
        disruptions=disruptions,
        weather_by_date=weather_by_date,
        start=start,
        end=end,
        settings=settings,
        avoid_transit_days=avoid_transit_days,
    )
    problems = validate_structure(plan, context)
    if trace:
        trace.say(f"The planner chose {plan.item_count()} stops over {len(plan.days)} days.")
    return plan, draft


def a_or_an(number: int) -> str:
    """"A" or "An" for the number as it is spoken: an 8-day, an 11-day, a 7-day trip."""
    digits = str(abs(int(number)))
    return "An" if digits[0] == "8" or digits in ("11", "18") else "A"


def _ensure_unique_ids(days: list[DayPlan]) -> None:
    """The same place may appear twice (two dinners at one restaurant); ids must not collide."""
    seen: set[str] = set()
    for day in days:
        for item in day.items:
            if item.id in seen:
                suffix = 2
                while f"{item.id}-{suffix}" in seen:
                    suffix += 1
                item.id = f"{item.id}-{suffix}"
            seen.add(item.id)


def sync_constraints(plan: TripPlan, context: TripContext) -> None:
    """Keep the plan's constraint list equal to the trip's current hard constraints.

    An edit can add, replace or cancel a constraint; the plan must show the current
    set, with a cheap deterministic status. The full model audit still runs on plan
    turns, where it can judge meaning.
    """
    existing = {check.text.strip().lower(): check for check in plan.constraints}
    checks: list[ConstraintCheck] = []
    for index, text in enumerate(context.hard_constraints[:12], start=1):
        previous = existing.get(text.strip().lower())
        status, evidence = _quick_constraint(plan, text)
        if status == "unknown" and previous is not None and previous.status == "pass":
            # Keep a pass the model justified earlier; code simply cannot re-derive it.
            checks.append(previous)
            continue
        checks.append(ConstraintCheck(id=f"c{index}", text=text, status=status, evidence=evidence))
    plan.constraints = checks


STOP_WORDS = {
    "must", "include", "including", "keep", "stay", "within", "during", "avoid",
    "the", "with", "and", "for", "from", "day", "days", "stop", "stops", "please",
    "want", "would", "like", "should", "this", "that", "there", "their",
}


GENERIC_PLACE_WORDS = {
    "museum", "park", "temple", "market", "island", "garden", "beach", "tower",
    "street", "palace", "station", "hotel", "restaurant", "harbour", "harbor",
    "peak", "bay", "square", "centre", "center", "district", "road", "avenue",
    "night", "day", "trip", "stay", "stop", "visit", "tour", "city", "resort",
}


def _quick_constraint(plan: TripPlan, text: str) -> tuple[str, str]:
    low = text.lower()
    if "budget" in low and plan.cost.budget is not None:
        ok = plan.cost.total <= plan.cost.budget
        return (
            "pass" if ok else "fail",
            f"Total {plan.cost.total:.0f} {plan.cost.currency} against a budget of {plan.cost.budget:.0f}.",
        )
    tokens = [word.strip(".,;:()").lower() for word in text.split() if len(word.strip(".,;:()")) > 3]
    tokens = [word for word in tokens if word not in STOP_WORDS]
    distinctive = [word for word in tokens if word not in GENERIC_PLACE_WORDS]
    # The distinctive token decides: "Ghibli Museum" must not pass on any museum,
    # and "Hong Kong Disneyland" must not pass on any Hong Kong stop.
    key = max(distinctive, key=len) if distinctive else (max(tokens, key=len) if tokens else "")
    wanted_time = _first_time(text)
    for index, day in enumerate(plan.days, start=1):
        for item in day.items:
            name = item.name.lower()
            if key and key in name:
                hits = sum(1 for token in tokens if token in name)
                if hits >= 2 or len(key) >= 8:
                    if wanted_time and item.start != wanted_time:
                        continue
                    at = f" at {item.start}" if wanted_time else ""
                    return "pass", f"Day {index} includes {item.name}{at}."
    return "unknown", ""


def _first_time(text: str) -> str | None:
    for token in (text or "").replace(",", " ").split():
        candidate = token.strip("().;")
        parts = candidate.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1][:2].isdigit():
            return f"{int(parts[0]) % 24:02d}:{parts[1][:2].zfill(2)}"
    return None


def refresh_derived(plan: TripPlan, context: TripContext) -> None:
    """Recompute the parts that follow from the plan: times, transfers, cost, assessment."""
    for day in plan.days:
        day.items.sort(key=lambda item: item.start or "99:99")
        _attach_transfers(day.items)
        _settle_sequence(day.items)
    nights = max(1, len(plan.days) - 1)
    plan.cost = _cost(context, plan.days, plan.hotels, plan.tickets, nights)
    sync_constraints(plan, context)
    gaps = list(plan.data_quality.gaps)
    plan.assessment = _assessment(
        plan_confidence=(plan.assessment.confidence if plan.assessment else 0.6),
        days=plan.days,
        gaps=gaps,
        bundle=Bundle(),
    )
    plan.updated_at = now_iso()


# -- mapping -----------------------------------------------------------


def build_plan(
    *,
    context: TripContext,
    bundle: Bundle,
    draft: PlanDraft,
    disruptions: list[Disruption],
    weather_by_date: dict[str, WeatherDay],
    start: date,
    end: date,
    settings: Settings,
    avoid_transit_days: set[int] | None = None,
) -> TripPlan:
    by_ref = {item.ref: item for item in bundle.items}
    days = day_range(start, end)[:31]
    draft_days = list(draft.days)
    built_days: list[DayPlan] = []
    # A stop is one place on one trip. The planner may repeat a ref across days
    # (the image/poster import produced USJ twice), but a repeated attraction is
    # not a second visit — it creates overlaps and absurd times. Keep the first.
    used_refs: set[str] = set()

    for index, day_date in enumerate(days):
        choice = next((row for row in draft_days if row.date == day_date.isoformat()), None)
        if choice is None and index < len(draft_days):
            choice = draft_days[index]
        items: list[PlanItem] = []
        for position, stop in enumerate(choice.stops if choice else []):
            evidence = by_ref.get(stop.ref)
            if evidence is None or evidence.data_status == "unavailable":
                continue
            if evidence.kind not in {"attraction", "meal"}:
                continue
            if evidence.ref in used_refs:
                continue
            used_refs.add(evidence.ref)
            items.append(_item_from_evidence(evidence, stop, position, context.destination or ""))
        items.sort(key=lambda item: item.start or "99:99")
        _attach_transfers(items, avoid_transit=(index + 1) in (avoid_transit_days or set()))
        _settle_sequence(items)
        built_days.append(
            DayPlan(
                date=day_date.isoformat(),
                title=(choice.title if choice and choice.title else "Exploring"),
                weather=weather_by_date.get(day_date.isoformat()),
                items=items,
                note=(choice.note if choice else ""),
                segment_id="seg-1",
            )
        )

    destination = Destination(
        name=context.destination or "",
        country_code="",
        geo=None,
        timezone="UTC",
    )
    if bundle.items:
        # The destination's own centre comes from the first sourced place's city meta.
        from app.evidence import place_corpus as corpus

        meta = corpus.city_meta(context.destination or "")
        if meta:
            destination = Destination(
                name=meta["name"],
                country_code=meta["country_code"],
                geo=GeoPoint(lat=meta["lat"], lng=meta["lng"]),
                timezone=meta["timezone"],
            )

    _ensure_unique_ids(built_days)
    _attach_alternatives(built_days, bundle, context.destination or "")
    hotels = _hotels(context, bundle, draft)
    hotel_options = [_hotel_from_evidence(item, context.destination or "") for item in bundle.of_kind("hotel") if item.data_status != "unavailable"][:4]
    tickets = _tickets(context, bundle, draft, start, end)
    legs = _legs(context, start, end)
    segments = [
        TripSegment(
            id="seg-1",
            destination={
                "name": destination.name,
                "country_code": destination.country_code,
                "geo": destination.geo.model_dump() if destination.geo else None,
                "timezone": destination.timezone,
            },
            dates={"start": start.isoformat(), "end": end.isoformat()},
        )
    ]
    constraints = _constraints(context, draft)
    cost, trimmed = _fit_budget(context, built_days, hotels, hotel_options, tickets)
    gaps = _gaps(bundle, draft)
    for name in trimmed:
        gaps.append(
            DataGap(
                section="budget",
                kind="trimmed",
                message=f"{name} was left out to keep the trip within the budget.",
                retryable=True,
            )
        )
    plan = TripPlan(
        trip_id=_trip_id(context.session_id),
        session_id=context.session_id,
        version=1,
        status="draft",
        destination=destination,
        origin=context.origin or "",
        dates=DateRange(start=start.isoformat(), end=end.isoformat()),
        party_size=int(context.party_size or 1),
        budget=Money(amount=float(context.budget), currency=context.currency) if context.budget else None,
        summary=draft.summary or f"{len(days)} days in {destination.name}.",
        days=built_days,
        hotels=hotels,
        hotel_options=hotel_options,
        tickets=tickets,
        segments=segments,
        legs=legs,
        cost=cost,
        constraints=constraints,
        disruptions=disruptions,
        reservations=_reservations(hotels, built_days),
        video_notes=list(bundle.video_notes)[:4],
        data_quality=_data_quality(bundle, gaps),
        assessment=_assessment(plan_confidence=draft.confidence, days=built_days, gaps=gaps, bundle=bundle),
        change_summary="Planned the trip.",
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    plan.trip_id = _trip_id(context.session_id)
    return plan


def _item_from_evidence(evidence: Evidence, stop: Any, position: int, city: str = "") -> PlanItem:
    start, end = stop.start, stop.end
    if not start:
        start = DEFAULT_SLOTS[min(position, len(DEFAULT_SLOTS) - 1)]
    if not end:
        length = evidence.suggested_minutes or DEFAULT_LENGTHS.get(evidence.kind, 90)
        end = shift_time(start, length)
    elif minutes_between(start, end) <= 0:
        end = shift_time(start, DEFAULT_LENGTHS.get(evidence.kind, 90))
    kwargs = evidence.to_item_kwargs()
    item = PlanItem(
        id=_item_id(evidence),
        type="meal" if evidence.kind == "meal" else "attraction",
        start=start,
        end=end,
        notes=(stop.note or "").strip()[:600],
        meal_slot=stop.meal_slot if evidence.kind == "meal" else None,
        **kwargs,
    )
    if evidence.kind == "meal" and not item.meal_slot:
        item.meal_slot = _meal_slot_for(start)
    if item.image is None:
        item.image = _wiki_image(evidence.name, city)
    return item


def _meal_slot_for(start: str) -> str | None:
    hour = int((start or "12:00").split(":")[0] or 12)
    if hour < 11:
        return "breakfast"
    if hour < 15:
        return "lunch"
    if hour < 18:
        return "snack"
    return "dinner"


def _settle_times(items: list[PlanItem]) -> None:
    """Push a stop later when it would overlap the one before it."""
    for index in range(1, len(items)):
        before, current = items[index - 1], items[index]
        if not before.end or not current.start:
            continue
        if minutes_between(before.end, current.start) <= 0:
            length = max(30, minutes_between(current.start, current.end) or 60)
            current.start = before.end
            current.end = shift_time(current.start, length)


def _settle_sequence(items: list[PlanItem]) -> None:
    """Make the day physically possible: no overlap and enough time for the transfer.

    The model chooses the order; the code owns the clock.  A stop that would start
    before the previous one ends (or before the walk between them is done) is moved
    later by exactly the missing minutes, keeping its length.
    """
    for index in range(1, len(items)):
        previous, current = items[index - 1], items[index]
        previous_end = clock_minutes(previous.end)
        current_start = clock_minutes(current.start)
        if previous_end is None or current_start is None:
            continue
        needed = 5
        if current.transfer is not None:
            needed += int(round(current.transfer.minutes or 0))
        gap = current_start - previous_end
        if gap >= needed:
            continue
        shift = needed - gap
        current.start = shift_time(current.start, shift)
        current.end = shift_time(current.end, shift)


UPHILL_WORDS = ("peak", "sky", "tower", "buddha", "observatory", "summit", "ridge")


def _uphill(item: PlanItem) -> bool:
    """A stop people normally reach by tram, bus or taxi rather than on foot."""
    name = item.name.lower()
    return any(word in name for word in UPHILL_WORDS)


def _attach_transfers(items: list[PlanItem], *, avoid_transit: bool = False) -> None:
    for index in range(1, len(items)):
        previous, current = items[index - 1], items[index]
        if not previous.geo or not current.geo:
            continue
        km = haversine_km(previous.geo, current.geo)
        uphill = _uphill(current) and km > 0.8
        if avoid_transit:
            mode = "walk" if km <= 2.0 and not uphill else "drive"
        elif uphill:
            mode = "transit"
        else:
            mode = "walk" if km <= 1.4 else ("transit" if km <= 12 else "drive")
        speed = {"walk": 4.5, "transit": 18.0, "drive": 30.0}[mode]
        minutes = round(km * 1.3 / speed * 60, 1)
        # A motorised hop has waiting and access time; a hillside road is slower than
        # the straight-line estimate. Keep the clock honest.
        if mode in {"transit", "drive"}:
            minutes = max(minutes, 6.0)
        if uphill:
            minutes = max(minutes, 12.0)
        current.transfer = TransferLeg(
            from_item_id=previous.id,
            minutes=minutes,
            mode=mode,
            distance_km=round(km, 2),
            source="straight-line estimate",
            tool="haversine",
            result_id=f"{previous.id}->{current.id}",
        )


def _hotels(context: TripContext, bundle: Bundle, draft: PlanDraft) -> list[HotelStay]:
    by_ref = {item.ref: item for item in bundle.items}
    chosen = by_ref.get(draft.hotel_ref or "")
    pool = [item for item in bundle.of_kind("hotel") if item.data_status != "unavailable"]
    if chosen is None and pool:
        chosen = max(pool, key=lambda item: (item.rating or 0) + (1 if item.price else 0) * 0.1)
    if chosen is None:
        return []
    hotel = _hotel_from_evidence(chosen, context.destination or "")
    nights = max(1, int(context.days or 1) - 1)
    hotel.check_in = context.start_date or ""
    hotel.check_out = context.end_date or ""
    hotel.notes = (chosen.summary or "")[:400]
    hotel.rank = 1
    hotel.score = chosen.rating
    return [hotel]


def _hotel_from_evidence(evidence: Evidence, city: str = "") -> HotelStay:
    hotel = HotelStay(id=_hotel_id(evidence.name), notes=evidence.summary[:400], **evidence.to_hotel_kwargs())
    if hotel.image is None:
        hotel.image = _wiki_image(evidence.name, city)
    return hotel


def _wiki_image(name: str, city: str = "") -> ImageRef:
    """A place picture the browser fetches from the app's Wikipedia image endpoint."""
    return ImageRef(
        url=f"/api/images/place?name={quote(name[:120])}&city={quote((city or '')[:80])}",
        attribution="Wikipedia",
        source="wikimedia",
    )


def _tickets(context: TripContext, bundle: Bundle, draft: PlanDraft, start: date, end: date) -> list[TicketOption]:
    by_ref = {item.ref: item for item in bundle.items}
    pool = [item for item in bundle.of_kind("ticket") if item.data_status != "unavailable"][:3]
    chosen_refs = {ref for ref in draft.ticket_refs[:2] if ref}
    out: list[TicketOption] = []
    for evidence in pool:
        kwargs = evidence.to_ticket_kwargs()
        # Live flights carry their own clock; only a ticket without times gets a placeholder.
        kwargs["depart_at"] = evidence.depart_at or f"{start.isoformat()}T08:00"
        kwargs["arrive_at"] = evidence.arrive_at or f"{start.isoformat()}T12:00"
        out.append(
            TicketOption(
                id=_ticket_id(evidence.name),
                leg_id="leg-out",
                origin=context.origin or "",
                destination=context.destination or "",
                duration_minutes=_duration_of(evidence),
                selected=evidence.ref in chosen_refs if chosen_refs else not out,
                **kwargs,
            )
        )
    if out and not any(ticket.selected for ticket in out):
        out[0].selected = True
    return out


def _duration_of(evidence: Evidence) -> float | None:
    text = evidence.summary or ""
    for token in text.split():
        if token.isdigit() and len(token) >= 2:
            return float(token)
    return None


def _attach_alternatives(days: list[DayPlan], bundle: Bundle, city: str = "") -> None:
    """Give every stop a few sourced alternatives, nearest first, for the UI's swap control."""
    pool = [
        item
        for item in bundle.items
        if item.kind in {"attraction", "meal"} and item.data_status != "unavailable"
    ]
    for day in days:
        for item in day.items:
            alternatives: list[PlanAlternative] = []
            for evidence in pool:
                if evidence.name.strip().lower() == item.name.strip().lower():
                    continue
                if (evidence.kind == "meal") != (item.type == "meal"):
                    continue
                distance = (
                    round(haversine_km(item.geo, evidence.geo) * 1000, 0)
                    if item.geo is not None and evidence.geo is not None
                    else None
                )
                alternatives.append(
                    PlanAlternative(
                        id="alt-" + hashlib.sha1(f"{evidence.kind}:{evidence.name}".encode("utf-8")).hexdigest()[:10],
                        name=evidence.name,
                        geo=evidence.geo,
                        address=evidence.address,
                        rating=evidence.rating,
                        review_count=evidence.review_count,
                        price_level=evidence.price_level,
                        cuisine=evidence.cuisine,
                        reservable=evidence.reservable,
                        opening_hours=list(evidence.opening_hours),
                        source_refs=list(evidence.source_refs),
                        booking_url=evidence.booking_url,
                        distance_m=distance,
                        source_types=list(evidence.source_types),
                        source_summary=evidence.summary,
                        links=list(evidence.links),
                        image=evidence.image or _wiki_image(evidence.name, city),
                    )
                )
            alternatives.sort(
                key=lambda alt: (
                    alt.distance_m if alt.distance_m is not None else 9e9,
                    -(alt.rating or 0),
                )
            )
            item.alternatives = alternatives[:3]


def _legs(context: TripContext, start: date, end: date) -> list[TransportLeg]:
    if _same_city(context.origin, context.destination):
        return []
    return [
        TransportLeg(
            id="leg-out",
            from_place=context.origin or "",
            to_place=context.destination or "",
            depart_date=start.isoformat(),
            kind="outbound",
        ),
        TransportLeg(
            id="leg-back",
            from_place=context.destination or "",
            to_place=context.origin or "",
            depart_date=end.isoformat(),
            kind="return",
        ),
    ]


def _fit_budget(
    context: TripContext,
    days: list[DayPlan],
    hotels: list[HotelStay],
    hotel_options: list[HotelStay],
    tickets: list[TicketOption],
) -> tuple[CostBreakdown, list[str]]:
    """Keep the plan within budget deterministically: cheaper stay first, then paid stops.

    The composer is asked to respect the budget, but arithmetic is code's job. This runs
    after the draft is built, so a near-miss (a few hundred over) does not cost another
    model call. A protected stop (named by a hard constraint, or locked) is never
    removed; if nothing can be trimmed, the plan keeps the overrun and reports it.
    """
    nights = max(1, len(days) - 1)
    party = max(1, int(context.party_size or 1))
    cost = _cost(context, days, hotels, tickets, nights)
    if context.budget is None:
        return cost, []
    trimmed: list[str] = []
    for _ in range(8):
        if cost.total <= float(context.budget):
            break
        current = hotels[0] if hotels else None
        cheaper = None
        if current is not None and not current.locked and current.price_per_night is not None:
            options = sorted(
                (option for option in hotel_options if option.price_per_night is not None),
                key=lambda option: option.price_per_night.amount,
            )
            cheaper = next(
                (
                    option
                    for option in options
                    if option.name != current.name
                    and option.price_per_night.amount < current.price_per_night.amount
                ),
                None,
            )
        if cheaper is not None:
            hotel = cheaper.model_copy(deep=True)
            hotel.check_in = current.check_in if current else ""
            hotel.check_out = current.check_out if current else ""
            hotels[0] = hotel
            cost = _cost(context, days, hotels, tickets, nights)
            continue
        paid = [
            (item.price.amount * party, day_index, item)
            for day_index, day in enumerate(days)
            for item in day.items
            if item.price is not None
            and item.price.amount > 0
            and not item.locked
            and not _protected(item, context.hard_constraints)
        ]
        if not paid:
            break
        _, day_index, item = max(paid, key=lambda row: row[0])
        days[day_index].items = [entry for entry in days[day_index].items if entry.id != item.id]
        trimmed.append(item.name)
        cost = _cost(context, days, hotels, tickets, nights)
    return cost, trimmed


def _protected(item: PlanItem, constraints: list[str]) -> bool:
    name = item.name.lower()
    for text in constraints:
        low = text.lower()
        if name and name in low:
            return True
        tokens = [word for word in low.replace(",", " ").split() if len(word) > 4]
        if sum(1 for token in tokens if token in name) >= 2:
            return True
    return False


def _constraints(context: TripContext, draft: PlanDraft) -> list[ConstraintCheck]:
    checks: list[ConstraintCheck] = []
    self_notes = {note.text.strip().lower(): note for note in draft.constraint_notes if note.text}
    for index, text in enumerate(context.hard_constraints or [], start=1):
        note = self_notes.get(text.strip().lower())
        checks.append(
            ConstraintCheck(
                id=f"c{index}",
                text=text,
                status="pass" if note and note.met else ("fail" if note and not note.met else "unknown"),
                evidence=(note.evidence if note else "")[:300],
            )
        )
    return checks


def _cost(context: TripContext, days: list[DayPlan], hotels: list[HotelStay], tickets: list[TicketOption], nights: int) -> CostBreakdown:
    currency = context.currency or "HKD"
    lines: list[CostLine] = []
    party = max(1, int(context.party_size or 1))
    rooms = max(1, (party + 1) // 2)

    flight = tickets[0].price.amount if tickets and tickets[0].price else 0.0
    if flight:
        lines.append(CostLine(label="Travel", amount=round(flight, 2), kind="flight", note="Return travel for the party."))
    stay = 0.0
    if hotels and hotels[0].price_per_night:
        stay = hotels[0].price_per_night.amount * max(1, nights) * rooms
        lines.append(
            CostLine(
                label="Stay",
                amount=round(stay, 2),
                kind="hotel",
                note=f"{hotels[0].name} for {max(1, nights)} night(s), {rooms} room(s).",
            )
        )
    food = 0.0
    sights = 0.0
    for day in days:
        for item in day.items:
            if not item.price:
                continue
            if item.type == "meal":
                food += item.price.amount * party
            else:
                sights += item.price.amount * party
    if food:
        lines.append(CostLine(label="Food", amount=round(food, 2), kind="meal", note=f"Listed meal prices for {party}."))
    if sights:
        lines.append(CostLine(label="Admissions", amount=round(sights, 2), kind="ticket", note="Listed admission prices."))
    local = round(max(0, len(days)) * 30 * party, 2)
    lines.append(CostLine(label="Local transport", amount=local, kind="local", note="Estimate for local travel."))
    total = round(sum(line.amount for line in lines), 2)
    budget = float(context.budget) if context.budget else None
    remaining = round(budget - total, 2) if budget is not None else None
    status = "unknown"
    if budget is not None:
        status = "within" if total <= budget else "over"
    return CostBreakdown(
        currency=currency,
        lines=lines,
        total=total,
        budget=budget,
        remaining=remaining,
        status=status,
        notes=["Costs use the prices the sources returned; estimates are labelled."],
    )


def _gaps(bundle: Bundle, draft: PlanDraft) -> list[DataGap]:
    gaps = list(bundle.gaps)
    noise = ("breakfast", "not confirmed as per night", "unit (per night")
    for text in draft.gaps[:4]:
        if not text or any(word in text.lower() for word in noise):
            continue
        gaps.append(DataGap(section="plan", kind="model_gap", message=str(text)[:300], retryable=False))
    return gaps[:6]


def _data_quality(bundle: Bundle, gaps: list[DataGap]):
    from app.domain.models import DataQuality

    return DataQuality(
        sources_used=sorted(bundle.sources_used)[:20],
        gaps=gaps,
        notes=list(bundle.notes)[:8],
        generated_at=now_iso(),
    )


def _assessment(*, plan_confidence: float, days: list[DayPlan], gaps: list[DataGap], bundle: Bundle) -> PlanAssessment:
    items = sum(len(day.items) for day in days)
    days_with_stops = sum(1 for day in days if day.items)
    coverage = days_with_stops / max(1, len(days))
    sourced = sum(1 for day in days for item in day.items if item.source_refs)
    grounding = sourced / max(1, items)
    confidence = max(
        0.0,
        min(1.0, 0.35 * float(plan_confidence or 0.5) + 0.35 * coverage + 0.3 * grounding),
    )
    level = "high" if confidence >= 0.75 else ("medium" if confidence >= 0.5 else "low")
    signals = []
    if grounding < 1:
        signals.append({"code": "unsourced_items", "weight": -0.2, "message": "Some stops had no source."})
    if gaps:
        signals.append({"code": "data_gaps", "weight": -0.1, "message": f"{len(gaps)} data gap(s) remain."})
    if coverage < 1:
        signals.append({"code": "empty_days", "weight": -0.2, "message": "Some days have no stops."})
    unverified = [gap.message for gap in gaps][:4]
    return PlanAssessment(
        confidence=round(confidence, 2),
        level=level,
        signals=signals,
        missing=[],
        unverified=unverified,
        next_action="research" if gaps else "none",
    )


def _reservations(hotels: list[HotelStay], days: list[DayPlan]) -> list[Reservation]:
    out: list[Reservation] = []
    if hotels and hotels[0].booking_url:
        out.append(
            Reservation(
                id="res-hotel",
                name=hotels[0].name,
                reason="Book the stay once the dates are confirmed.",
                url=hotels[0].booking_url,
                needed_by=None,
                status="to_book",
            )
        )
    for day in days:
        for item in day.items:
            if item.reservable and item.booking_url and item.type == "attraction":
                out.append(
                    Reservation(
                        id=f"res-{item.id}",
                        name=item.name,
                        reason="The place takes reservations.",
                        url=item.booking_url,
                        needed_by=f"{day.date}T00:00",
                        status="optional",
                    )
                )
    return out[:6]


def _trip_id(session_id: str) -> str:
    return "trip-" + hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:12]


def _item_id(evidence: Evidence) -> str:
    digest = hashlib.sha1(f"{evidence.kind}:{evidence.name.lower()}".encode("utf-8")).hexdigest()[:10]
    return f"it-{digest}"


def _hotel_id(name: str) -> str:
    return "ho-" + hashlib.sha1(name.lower().encode("utf-8")).hexdigest()[:10]


def _ticket_id(name: str) -> str:
    return "tk-" + hashlib.sha1(name.lower().encode("utf-8")).hexdigest()[:10]


# -- structural validation (no model call) -----------------------------


def validate_structure(plan: TripPlan, context: TripContext) -> list[Problem]:
    problems: list[Problem] = []
    expected = 0
    start = parse_date(context.start_date)
    if start is None:
        problems.append(Problem(code="no_start_date", message="The trip has no start date."))
    if not plan.days:
        problems.append(Problem(code="no_days", message="The plan has no days."))
    for index, day in enumerate(plan.days, start=1):
        if not day.items:
            problems.append(Problem(code="empty_day", message=f"Day {index} has no stops.", day=index))
        for first in range(len(day.items)):
            for second in range(first + 1, len(day.items)):
                before, after = day.items[first], day.items[second]
                if overlaps(before.start, before.end, after.start, after.end):
                    problems.append(
                        Problem(
                            code="overlap",
                            message=f"{after.name} overlaps {before.name} on day {index}.",
                            day=index,
                            item_id=after.id,
                        )
                    )
        for position in range(1, len(day.items)):
            previous, current = day.items[position - 1], day.items[position]
            start_minutes = clock_minutes(current.start)
            end_minutes = clock_minutes(previous.end)
            gap = (start_minutes - end_minutes) if start_minutes is not None and end_minutes is not None else 0
            transfer = current.transfer.minutes if current.transfer else 0
            if transfer and gap + 1 < transfer:
                problems.append(
                    Problem(
                        code="transfer_infeasible",
                        message=(
                            f"Day {index}: {gap:.0f} min between {previous.name} and {current.name}, "
                            f"but the transfer needs {transfer:.0f} min."
                        ),
                        day=index,
                        item_id=current.id,
                    )
                )
    if any(item.data_status == "unavailable" for day in plan.days for item in day.items):
        problems.append(Problem(code="unavailable_item", message="A closed stop is still in the plan."))
    if plan.cost.budget is not None and plan.cost.total > plan.cost.budget:
        problems.append(
            Problem(
                code="over_budget",
                message=f"The plan costs {plan.cost.total:.0f} {plan.cost.currency}, over the "
                f"{plan.cost.budget:.0f} budget.",
            )
        )
    for check in plan.constraints:
        if check.status == "fail":
            problems.append(Problem(code="constraint_failed", message=f"Constraint not met: {check.text}"))
    return problems[:12]


def fallback_plan(
    *,
    context: TripContext,
    bundle: Bundle,
    disruptions: list[Disruption],
    weather_by_date: dict[str, WeatherDay],
    start: date,
    end: date,
    settings: Settings,
    reason: str = "",
) -> TripPlan:
    """A deterministic plan from the best-sourced candidates, when the composer cannot answer.

    This is a degraded path, not a second planner: it takes the highest-rated
    attractions and meals, schedules them in a simple day shape, and says so in the
    plan's gaps. It exists so a provider outage leaves a usable itinerary and a clear
    explanation instead of an empty turn.
    """
    attractions = sorted(
        [item for item in bundle.of_kind("attraction") if item.data_status != "unavailable"],
        key=lambda item: (item.rating or 0, item.review_count or 0),
        reverse=True,
    )
    meals = sorted(
        [item for item in bundle.of_kind("meal") if item.data_status != "unavailable"],
        key=lambda item: (item.rating or 0, item.review_count or 0),
        reverse=True,
    )
    hotels = sorted(
        [item for item in bundle.of_kind("hotel") if item.data_status != "unavailable"],
        key=lambda item: (item.rating or 0),
        reverse=True,
    )
    days = day_range(start, end)[:31]
    per_day = 2
    attraction_index = 0
    meal_index = 0
    choices: list[DayChoice] = []
    for day_date in days:
        stops: list[StopChoice] = []
        # A fallback itinerary must not repeat the same attraction to fill days.
        # Take the next unique candidates; an exhausted pool leaves a visible gap.
        for slot in range(per_day):
            if attraction_index >= len(attractions):
                break
            chosen = attractions[attraction_index]
            start_time = ["09:30", "13:30"][slot]
            length = chosen.suggested_minutes or 90
            stops.append(
                StopChoice(
                    ref=chosen.ref,
                    start=start_time,
                    end=shift_time(start_time, length),
                    note="",
                )
            )
            attraction_index += 1
        for meal_slot, meal_time in (("lunch", "12:15"), ("dinner", "19:00")):
            if meal_index >= len(meals):
                break
            chosen_meal = meals[meal_index]
            stops.append(
                StopChoice(
                    ref=chosen_meal.ref,
                    start=meal_time,
                    end=shift_time(meal_time, 75),
                    meal_slot=meal_slot,  # type: ignore[arg-type]
                )
            )
            meal_index += 1
        choices.append(DayChoice(date=day_date.isoformat(), title="Exploring", stops=stops))
    draft = PlanDraft(
        summary=f"{a_or_an(len(days))} {len(days)}-day plan for {context.destination}, built from the best-sourced options.",
        days=choices,
        hotel_ref=hotels[0].ref if hotels else None,
        ticket_refs=[],
        confidence=0.4,
    )
    plan = build_plan(
        context=context,
        bundle=bundle,
        draft=draft,
        disruptions=disruptions,
        weather_by_date=weather_by_date,
        start=start,
        end=end,
        settings=settings,
    )
    plan.change_summary = "Built a fallback itinerary."
    plan.data_quality.gaps.append(
        DataGap(
            section="plan",
            kind="degraded",
            message=(
                "The planning model did not answer"
                + (f" ({reason[:160]})" if reason else "")
                + "; this itinerary was assembled from the highest-rated sourced options. Send the request again to refine it."
            ),
            retryable=True,
        )
    )
    plan.assessment = _assessment(
        plan_confidence=0.4, days=plan.days, gaps=plan.data_quality.gaps, bundle=bundle
    )
    return plan
