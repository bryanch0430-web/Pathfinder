"""Grounded planner.

The planner is allowed to decide *selection and order*; it is not allowed to
create facts. Candidate places, hotels, flights and weather all arrive from the
agents with sources. The model returns only ids, day titles and a summary, and
every id is validated before use.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.application.llm_gateway import LLMGateway
from app.application.meals import (
    alternatives_for,
    attraction_alternative,
    choose_meals,
    day_centre,
    search_nearby,
    image_of,
    source_facts,
)
from app.application.opening_hours import DAY_MINUTES, lines_for, windows_on
from app.application.profile_rules import (
    CHILDREN_STOPS_END_BY,
    PACE_ATTRACTION_CAP,  # noqa: F401  (re-exported)
    PACE_MIN_GAP,
    business_first_start,
    is_business,
    profile_of,
    profile_order,
    stops_cap,
)
from app.application.transfers import (
    TRANSFER_TOOL,
    TRANSPORT_NOTE,
    Legs,
    fetch_legs,
    transfer_from,
    transfer_sentence,
)
from app.application.trip_rules import DayWindow, blocked_dates, flight_windows, travel_windows
from app.application.trip_rules import must_include as _must_include
from app.application.validation import _URL
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import (
    AgentResult,
    AgentStatus,
    AuditReport,
    AuditSeverity,
    ConstraintCheck,
    ConstraintStatus,
    CostBreakdown,
    CostLine,
    DataGap,
    DataQuality,
    DataStatus,
    DateRange,
    DayPlan,
    Destination,
    Disruption,
    FailureKind,
    GeoLocation,
    GeoPoint,
    HotelNearby,
    RoomType,
    StayMatch,
    HotelStay,
    ItemType,
    Money,
    NoteItem,
    PlanItem,
    PriceKind,
    Reservation,
    RouteLeg,
    SourceRef,
    TicketOption,
    TransferLeg,
    TripContext,
    TripPlan,
    WeatherDay,
    selected_ticket,
    utcnow,
    TravellerProfile,
)
from app.domain.place_names import english_name, trusted_names
from app.infrastructure.http import haversine_km
from app.infrastructure.providers.fx import FxService
from app.infrastructure.providers.routes import (  # noqa: F401  (re-exported for the TC-07 check)
    TRANSPORT_OVERHEAD_MIN,
    TRANSPORT_SPEED_KMH,
    model_travel,
)
from app.security import strip_fence_tags_deep

logger = logging.getLogger(__name__)


#: The gap for a kept stay whose rate for the new dates could not be fetched; it keeps its old rate.
STAY_PRICE_NOT_REFRESHED = "The hotel's price for the new dates could not be refreshed."
#: What a day with no stop reads; an edit that puts a stop on such a day clears it.
NO_STOPS_NOTE = "No verified stops were available for this day."
#: The most flight options a plan keeps to choose from (the first is selected unless the traveller picks another).
MAX_TICKET_OPTIONS = 5
#: The most hotel stays a plan keeps to choose from.
MAX_HOTEL_OPTIONS = 5


def annotate_stays(plan: TripPlan) -> None:
    """Give each stay (chosen and options) the straight-line distance to its closest planned stop."""
    stops = [(item.name, item.geo) for day in plan.days for item in day.items if item.geo is not None]
    for stay in [*plan.hotels, *plan.hotel_options]:
        if stay.geo is None or not stops:
            stay.nearest_stop_km, stay.nearest_stop_name = None, ""
            continue
        km, name = min(
            (haversine_km(stay.geo.lat, stay.geo.lng, geo.lat, geo.lng), name) for name, geo in stops
        )
        stay.nearest_stop_km, stay.nearest_stop_name = round(km, 2), name


class PlannerAssignment(BaseModel):
    day_index: int = 0
    item_ids: list[str] = Field(default_factory=list)
    theme: str = ""


class PlannerDraft(BaseModel):
    summary: str = ""
    day_titles: list[str] = Field(default_factory=list)
    assignments: list[PlannerAssignment] = Field(default_factory=list)
    hotel_id: str | None = None
    notes: list[str] = Field(default_factory=list)


# --- local transport -------------------------------------------------------
# The transport model lives with the routes sources (it is served as the
# routes.segments tool); it mirrors the console map's thresholds (2.5 / 35 /
# 160 km) so the itinerary text, the map legend and the schedule never disagree.
_travel = model_travel

# Dwell time per stop and the earliest start, by pace. A slow trip sees fewer
# places for longer; a packed one starts earlier and moves faster.
PACE_DWELL_MINUTES = {"slow": 120, "balanced": 90, "fast": 75, "relaxed": 120, "packed": 75}
PACE_FIRST_START = {
    "slow": 9 * 60 + 30,
    "balanced": 9 * 60,
    "fast": 8 * 60 + 30,
    "relaxed": 9 * 60 + 30,
    "packed": 8 * 60 + 30,
}
PACE_BUFFER_MINUTES = {"slow": 15, "balanced": 5, "fast": 0, "relaxed": 15, "packed": 0}

# A stop that opens by 08:30, or that carries a large review count, is worth the
# early slot: the generic, destination-independent proxy for "crowds build here".
EARLY_OPEN_BY = 8 * 60 + 30
POPULAR_REVIEW_COUNT = 5000

BREAKFAST_START, BREAKFAST_END = 8 * 60, 8 * 60 + 45
# Breakfast is over by 09:30: it starts by this time or is left out.
BREAKFAST_LATEST = 9 * 60 + 30 - (BREAKFAST_END - BREAKFAST_START)
LUNCH_START, LUNCH_END = 12 * 60 + 30, 13 * 60 + 30
DINNER_START, DINNER_END = 19 * 60, 20 * 60 + 15
# Latest end for a sightseeing stop, used to size a day cut short by a flight.
DAY_END = 21 * 60
# Transfer and buffer allowance per stop when sizing a shortened day.
SLOT_OVERHEAD_MINUTES = 30
# Meals start at their usual time or after the transfer from the previous stop,
# whichever is later. A stop may keep lunch waiting LUNCH_SLACK minutes at most;
# otherwise lunch comes first. A meal that cannot start by its latest time (or
# inside the restaurant's stated hours) is left out rather than squeezed in.
LUNCH_SLACK = 15
LUNCH_LATEST = 14 * 60
DINNER_LATEST = 21 * 60
# Nothing is scheduled to end after this time.
DAY_LAST_END = 22 * 60 + 30
# A stop near closing time may be shortened to this, but not below it.
MIN_DWELL_MINUTES = 45
# A stop that opens more than this long after the traveller could arrive is
# tried again after the remaining stops instead of leaving an idle gap.
OPENING_WAIT_LIMIT = 60


class Planner:
    def __init__(self, gateway: LLMGateway, settings: Settings, fx: FxService, route_matrix=None):
        self.gateway = gateway
        self.settings = settings
        self.fx = fx
        self.matrix = route_matrix

    async def compose(
        self,
        *,
        session_id: str,
        context: TripContext,
        destination: GeoLocation,
        date_range: DateRange,
        results: dict[str, AgentResult],
        memory_summaries: list[str],
        version: int = 1,
        trip_id: str | None = None,
        change_summary: str = "",
        toolbox=None,
        transfers=None,
        keep_hotel: HotelStay | None = None,
        pick_hotel: Callable[[list[dict]], dict | None] | None = None,
        audit: AuditReport | None = None,
        meal_sources: list | None = None,
    ) -> TripPlan:
        places = list(results.get("attraction", AgentResult(agent="attraction", status=AgentStatus.UNAVAILABLE)).payload.get("places", []))
        meals = list(results.get("attraction", AgentResult(agent="attraction", status=AgentStatus.UNAVAILABLE)).payload.get("meals", []))
        hotels = list(results.get("hotel", AgentResult(agent="hotel", status=AgentStatus.UNAVAILABLE)).payload.get("hotels", []))
        must_see = _must_see(context)
        attraction_payload = results.get("attraction").payload if results.get("attraction") else {}
        wishes = {str(key): value for key, value in (attraction_payload.get("wishes") or {}).items()}
        wish_failures = {str(key): str(value) for key, value in (attraction_payload.get("wish_failures") or {}).items()}
        # A named must-include is placed before the rest, so a short trip with
        # more candidates than slots never drops the stop the traveller asked for.
        required = must_see + _required_names(context) + [name for name in wishes.values() if name]
        audit_brief: list[dict] = []
        if audit is not None and audit.findings:
            # A capped block drops the candidate before the model sees it and
            # before the deterministic fallback, so it holds without a model too.
            # A place the traveller named, and the stay they keep, are never dropped:
            # their finding stays in the brief as a warning.
            audit_blocked = set(audit.blocked_ids)
            if required:
                audit_blocked -= {
                    str(row.get("id")) for row in [*places, *meals]
                    if _is_must_see(str(row.get("name", "")), required)
                }
            if keep_hotel is not None:
                audit_blocked -= {str(row.get("id")) for row in hotels if row.get("name") == keep_hotel.name}
            places, _ = apply_audit_blocks(places, audit_blocked)
            meals, _ = apply_audit_blocks(meals, audit_blocked)
            hotels, _ = apply_audit_blocks(hotels, audit_blocked)
        flights = list(results.get("ticket", AgentResult(agent="ticket", status=AgentStatus.UNAVAILABLE)).payload.get("flights", []))
        weather_rows = list(results.get("weather", AgentResult(agent="weather", status=AgentStatus.UNAVAILABLE)).payload.get("days", []))
        if audit is not None and audit.findings:
            # Flights and weather are not blocked here; a weather item's id is its date.
            kept_ids = {str(row.get("id")) for row in [*places, *meals, *hotels, *flights] if row.get("id")}
            kept_ids |= {str(row.get("date")) for row in weather_rows if row.get("date")}
            audit_brief = _audit_brief(audit, kept_ids)
        weather_by_date = {row.get("date"): row for row in weather_rows if row.get("date")}

        known_place_ids = {row["id"] for row in places}
        known_meal_ids = {row["id"] for row in meals}
        draft = await self._draft(
            context=context,
            destination=destination,
            date_range=date_range,
            places=places,
            meals=meals,
            hotels=hotels,
            flights=flights,
            weather_rows=weather_rows,
            memory_summaries=memory_summaries,
            audit_brief=audit_brief,
        )
        evidence_urls = {ref["url"] for entry in audit_brief for ref in entry["evidence"] if ref.get("url")}
        if draft is not None and evidence_urls:
            # Audit evidence is reference material for the planner, not a link for the traveller.
            notes_kept = [text for text in (_drop_urls(note, evidence_urls) for note in draft.notes) if text]
            draft = draft.model_copy(update={"notes": notes_kept, "summary": _drop_urls(draft.summary, evidence_urls)})

        dates = list(date_range.dates)
        day_count = len(dates)
        pace = _pace(context)
        rule_texts = [*context.hard_constraints, *context.preferences]
        blocked = blocked_dates(rule_texts, dates)
        windows = travel_windows(rule_texts, dates)
        for day, flight_window in flight_windows(flights, dates).items():
            window = windows.setdefault(day, DayWindow())
            if flight_window.start is not None:
                window.start = flight_window.start if window.start is None else max(window.start, flight_window.start)
            if flight_window.end is not None:
                window.end = flight_window.end if window.end is None else min(window.end, flight_window.end)
            window.texts.extend(flight_window.texts)
        profile = profile_of(context)
        business = is_business(context) and pace != "packed"
        first_stop = business_first_start(context) if business else None
        if first_stop is not None:
            # A business traveller's sightseeing starts in the evening: the day's free window opens then.
            for day in dates:
                window = windows.setdefault(day, DayWindow())
                window.start = first_stop if window.start is None else max(window.start, first_stop)
        # The profile's order of the candidates (family places, step-free places first) comes before any choice.
        places[:] = profile_order(places, profile)
        if required:
            places.sort(key=lambda row: 0 if _is_must_see(str(row.get("name", "")), required) else 1)
        diet = _diet(context)
        diet_note = ""
        if diet:
            # A dietary constraint is hard: only restaurants the source labels as
            # serving it may become a meal stop.
            suitable = [row for row in meals if _serves_diet(row, diet)]
            if len(suitable) < len(meals):
                diet_note = f"Meal stops are limited to the {len(suitable)} {diet} restaurant(s) the sources returned."
            meals = suitable
        # A blocked day takes no stop; a flight day takes only what fits around it.
        limits = [0 if day in blocked else _window_limit(windows.get(day), pace) for day in dates]
        assignment = self._assign_places(places, day_count, draft, pace=pace, limits=limits, business=business)
        if self.settings.enable_route_matrix and self.matrix is not None and toolbox is not None:
            assignment = await self._matrix_order(assignment, toolbox)
        cap = stops_cap(self.settings, pace, business)
        caps = [cap if limit is None else min(cap, limit) for limit in limits]
        _move_closed_stops(assignment, dates, caps)
        meal_days = {index: rows for index, rows in assignment.items() if dates[index] not in blocked}
        hotel = self._choose_hotel(hotels, draft)
        if pick_hotel is not None:
            # An edit that asked for another stay says which: none (nothing better came back) leaves `keep_hotel`.
            hotel = pick_hotel(hotels)
        # Meals: breakfast, lunch, dinner and a cafe stop near each day's own stops, with alternatives;
        # the city-wide pool when the nearby searches fail or are not available (no model call).
        hotel_geo = (hotel or {}).get("geo") if isinstance((hotel or {}).get("geo"), dict) else None
        fallback_centre = hotel_geo if hotel_geo and hotel_geo.get("lat") is not None else destination.geo.model_dump()
        nearby: dict[int, dict[str, list[dict]]] = {}
        if meal_sources:
            centres = {
                index: centre
                for index, rows in meal_days.items()
                if rows and (centre := day_centre(rows, fallback_centre)) is not None
            }
            try:
                nearby = await search_nearby(
                    sources=list(meal_sources), toolbox=toolbox, destination=context.destination,
                    centres=centres, settings=self.settings,
                )
            except CanaryLeak:
                raise
            except Exception as exc:
                # The pool below is the fallback; a bug in the nearby search never costs the plan.
                logger.warning("nearby meal search failed open: %s", type(exc).__name__)
                nearby = {}
        meal_plan = choose_meals(
            pool=meals, nearby=nearby, assignment=meal_days, dates=dates, fallback_centre=fallback_centre,
            suitable=(lambda row: _serves_diet(row, diet)) if diet else None,
        )

        async def day_schedule(index: int, day_places: list[dict]) -> tuple[list[PlanItem], list[str]]:
            day_meals = meal_plan.get(index, {})
            breakfast, lunch, dinner = day_meals.get("breakfast"), day_meals.get("lunch"), day_meals.get("dinner")
            # Transfer times come from one logged routes.segments call per day
            # (Proposal TC-09), covering every pair the schedule may need.
            stops = [row for row in [breakfast, *day_places, lunch, dinner] if row and _geo_point(row) is not None]
            legs, tool = await fetch_legs(
                [(_item_id(row), _geo_point(row)) for row in stops],
                toolbox=toolbox,
                source=transfers,
                step_free=profile.step_free,
            )
            return _schedule(
                dates[index], day_places, lunch, dinner, pace=pace, must_see=must_see,
                window=windows.get(dates[index]), legs=legs, tool=tool,
                breakfast=breakfast, snack=day_meals.get("snack"),
                stops_end_by=CHILDREN_STOPS_END_BY if profile.with_children else None,
                step_free=profile.step_free,
            )

        schedules: list[dict[str, Any]] = []
        for index in range(len(dates)):
            day_places = [row for row in assignment.get(index, [])]
            items, left_out = await day_schedule(index, day_places)
            schedules.append({"places": day_places, "items": items, "left_out": left_out})
        # A stop that did not fit its day moves to another day with room, when it
        # fits there without pushing anything else out.
        for index, entry in enumerate(schedules):
            for row in [row for row in entry["places"] if row["name"] in entry["left_out"]]:
                for other_index, other in enumerate(schedules):
                    if other_index == index or len(other["places"]) >= caps[other_index]:
                        continue
                    start = other["places"][0].get("geo") if other["places"] else None
                    trial = _nearest_order([*other["places"], row], start)
                    items, left_out = await day_schedule(other_index, trial)
                    if row["name"] in left_out or not set(left_out) <= set(other["left_out"]):
                        continue
                    other.update(places=trial, items=items, left_out=left_out)
                    entry["places"].remove(row)
                    entry["left_out"].remove(row["name"])
                    break

        # Each attraction carries the nearest open place candidate that is not in the plan.
        scheduled = {item.id for entry in schedules for item in entry["items"]}
        unused = [row for row in places if _item_id(row) not in scheduled]
        offered: set[str] = set()
        for index, entry in enumerate(schedules):
            for item in entry["items"]:
                if item.type == ItemType.ATTRACTION and not item.alternatives:
                    geo = item.geo.model_dump() if item.geo is not None else None
                    alternative = attraction_alternative(geo, unused, dates[index], offered)
                    if alternative is not None:
                        item.alternatives = [alternative]

        days: list[DayPlan] = []
        for index, day_date in enumerate(dates):
            weather_row = weather_by_date.get(day_date.isoformat())
            weather = WeatherDay.model_validate(weather_row) if weather_row else None
            day_places = schedules[index]["places"]
            items, left_out = schedules[index]["items"], schedules[index]["left_out"]
            window = windows.get(day_date)
            title = ""
            if day_date in blocked:
                title = f"Free day in {context.destination}"
            elif draft and index < len(draft.day_titles):
                title = draft.day_titles[index].strip()
            if not title:
                if day_places:
                    first = day_places[0]
                    title = f"{english_name(first['name'], trusted_names(first.get('names')))} and nearby stops"
                elif window is not None:
                    title = f"Travel day in {context.destination}"
                else:
                    title = f"Open day in {context.destination}"
            if day_date in blocked:
                note = "Kept free, as you asked."
            elif items:
                note = ""
            elif window is not None:
                note = "Travel day: no time for stops around your flight."
            else:
                note = NO_STOPS_NOTE
            if left_out:
                note = (
                    f"{note} Not scheduled, for lack of time inside the day or the stated "
                    f"opening hours: {', '.join(left_out)}."
                ).strip()
            days.append(DayPlan(date=day_date, title=title, weather=weather, items=items, note=note))

        hotel_stay = self._build_hotel(hotel, date_range)
        stay_gap: DataGap | None = None
        if keep_hotel is not None and day_count > 1 and (pick_hotel is None or hotel is None):
            # An edit that changes the trip's length keeps the stay the traveller already has, not a new pick.
            hotel_stay, refreshed = self.keep_stay(keep_hotel, hotels, date_range)
            if not refreshed:
                stay_gap = DataGap(section="hotels", kind=FailureKind.NO_DATA, message=STAY_PRICE_NOT_REFRESHED)
        tickets = self._build_tickets(flights)
        reservations = _reservations(places, tickets)
        cost = self._cost(context, hotel_stay, tickets, date_range)
        constraints = _constraints(
            context, days, hotel_stay, tickets, cost, blocked=blocked, windows=windows, wishes=wishes,
            wish_failures=wish_failures,
        )
        gaps = _gaps(results)
        if stay_gap is not None:
            gaps.append(stay_gap)
        if TRANSFER_TOOL in getattr(toolbox, "refused", ()):
            gaps.append(
                DataGap(
                    section="transfers",
                    kind=FailureKind.BLOCKED,
                    message=(
                        "Transfer times are estimates: this request reached its limit of data lookups "
                        "before they could be looked up."
                    ),
                )
            )
        if diet and not meals:
            gaps.append(
                DataGap(
                    section="meals",
                    kind=FailureKind.NO_DATA,
                    message=f"No {diet} restaurant came back from the place sources, so no meal stop is suggested.",
                )
            )
        open_days = _open_days_gap(days, blocked=blocked, windows=windows)
        if open_days is not None and (places or not any(gap.section == "attractions" for gap in gaps)):
            # With no place at all the attraction agent's own gap already says why.
            gaps.append(open_days)
        sources_used = sorted({ref.source for result in results.values() for ref in result.sources})
        notes = [note for result in results.values() for note in result.notes]
        if diet_note:
            notes.append(diet_note)
        if draft:
            notes.extend(draft.notes)

        video_notes: list[NoteItem] = []
        seen_videos: set[str] = set()
        attraction = results.get("attraction")
        if attraction is not None:
            for row in attraction.payload.get("notes") or []:
                try:
                    note = NoteItem.model_validate(row)
                except Exception:
                    continue
                if note.url and note.url not in seen_videos:
                    seen_videos.add(note.url)
                    video_notes.append(note)

        plan = TripPlan(
            trip_id=trip_id or TripPlan.model_fields["trip_id"].default_factory(),  # type: ignore[misc]
            session_id=session_id,
            version=version,
            destination=Destination(
                name=context.destination,
                country_code=destination.country_code,
                geo=destination.geo,
                timezone=destination.timezone,
            ),
            origin=context.origin,
            dates=date_range,
            party_size=context.party_size or 1,
            budget=Money(amount=context.budget, currency=context.currency) if context.budget is not None else None,
            summary=_summary(draft, context, date_range, destination),
            days=days,
            hotels=[hotel_stay] if hotel_stay else [],
            hotel_options=self.hotel_options(hotels, hotel_stay, date_range),
            tickets=tickets,
            cost=cost,
            constraints=constraints,
            reservations=reservations,
            data_quality=DataQuality(
                sources_used=sources_used,
                gaps=gaps,
                notes=_dedupe(notes)[:12],
                search_window_months=self.settings.search_window_months,
                generated_at=utcnow(),
            ),
            video_notes=video_notes[:6],
            change_summary=change_summary,
            profile=None if profile == TravellerProfile() else profile,
        )
        annotate_stays(plan)
        return plan

    async def _draft(
        self,
        *,
        context: TripContext,
        destination: GeoLocation,
        date_range: DateRange,
        places: list[dict],
        meals: list[dict],
        hotels: list[dict],
        flights: list[dict],
        weather_rows: list[dict],
        memory_summaries: list[str],
        audit_brief: list[dict] | None = None,
    ) -> PlannerDraft | None:
        if not self.gateway.available:
            return None
        payload = {
            "trip": {
                "destination": context.destination,
                "origin": context.origin,
                "start": date_range.start.isoformat(),
                "end": date_range.end.isoformat(),
                "days": len(date_range.dates),
                "party_size": context.party_size,
                "budget": context.budget,
                "currency": context.currency,
                "hotel_style": context.hotel_style,
                "pace": context.pace,
                "preferences": context.preferences,
                "hard_constraints": context.hard_constraints,
            },
            "candidates": {
                "places": [_trim_place(row) for row in places],
                "meals": [_trim_place(row) for row in meals],
                "hotels": [_trim_hotel(row) for row in hotels],
                "flights": [
                    {
                        "id": row.get("id"),
                        "airline": row.get("airline"),
                        "flight_number": row.get("flight_number"),
                        "depart_at": row.get("depart_at"),
                        "arrive_at": row.get("arrive_at"),
                        "price": row.get("price"),
                    }
                    for row in flights[:4]
                ],
                "weather": [
                    {
                        "date": row.get("date"),
                        "status": row.get("status"),
                        "summary": row.get("summary"),
                        "high_c": row.get("high_c"),
                        "low_c": row.get("low_c"),
                    }
                    for row in weather_rows
                ],
            },
            "saved_trip_summaries": memory_summaries[:3],
            "allowed_ids": {
                "places": [row["id"] for row in places],
                "meals": [row["id"] for row in meals],
                "hotels": [row["id"] for row in hotels],
            },
        }
        if audit_brief:
            # Untrusted text (tool data, an LLM's detail): fenced below with the rest of the payload.
            payload["audit"] = audit_brief
        try:
            data = await self.gateway.json(
                "planner",
                user=json.dumps(strip_fence_tags_deep(payload), ensure_ascii=False, default=str),
                purpose="planner",
            )
            return PlannerDraft.model_validate(data)
        except CanaryLeak:
            raise
        except Exception:
            return None

    def _assign_places(
        self,
        places: list[dict],
        day_count: int,
        draft: PlannerDraft | None,
        *,
        pace: str = "balanced",
        limits: list[int | None] | None = None,
        business: bool = False,
    ) -> dict[int, list[dict]]:
        by_id = {row["id"]: row for row in places}
        assignment: dict[int, list[dict]] = {index: [] for index in range(day_count)}
        used: set[str] = set()
        accepted: list[str] = []
        cap = stops_cap(self.settings, pace, business)
        # Per-day room: a blocked day has none, a flight day only what fits.
        caps = [
            cap if limits is None or index >= len(limits) or limits[index] is None else min(cap, limits[index])
            for index in range(day_count)
        ]
        spill: list[dict] = []

        def admit(row: dict) -> bool:
            """Reject near-duplicates ("Arashiyama" vs "Arashiyama Bamboo Forest")."""
            name = _normalise_name(str(row.get("name", "")))
            if not name:
                return True
            if any(_similar_place(name, other) for other in accepted):
                return False
            accepted.append(name)
            return True

        if draft:
            for item in draft.assignments:
                if not 0 <= item.day_index < day_count:
                    continue
                for item_id in item.item_ids:
                    row = by_id.get(item_id)
                    if row is None or item_id in used:
                        continue
                    if len(assignment[item.day_index]) >= caps[item.day_index]:
                        spill.append(row)
                    elif admit(row):
                        assignment[item.day_index].append(row)
                    used.add(item_id)
        # Fill anything the model did not assign, keeping the days balanced.
        remaining = spill + [row for row in places if row["id"] not in used]
        counters = [len(assignment[index]) for index in range(day_count)]
        for row in remaining:
            open_days = [i for i in range(day_count) if counters[i] < caps[i]]
            if not open_days:
                break
            index = min(open_days, key=lambda i: counters[i])
            if not admit(row):
                continue
            assignment[index].append(row)
            counters[index] += 1
        # A model can leave one day with a single stop while another has three.
        # Move one stop across, but never break a day that already has two.
        counts = [len(assignment[index]) for index in range(day_count)]
        for _ in range(2):
            roomy = [i for i in range(day_count) if counts[i] < caps[i]]
            if not roomy:
                break
            low = min(roomy, key=lambda i: counts[i])
            high = max(range(day_count), key=lambda i: counts[i])
            if counts[low] >= 2 or counts[high] <= 2:
                break
            assignment[low].append(assignment[high].pop())
            counts[low] += 1
            counts[high] -= 1
        # A model can also pack every place onto the first days of a long trip. No day with room is left
        # empty while another holds two or more stops: the fullest day gives up the stop farthest from
        # the rest of its day. With fewer places than days some days stay empty; nothing is invented.
        while True:
            empty = [i for i in range(day_count) if not assignment[i] and caps[i] > 0]
            fullest = max(range(day_count), key=lambda i: len(assignment[i]), default=None)
            if not empty or fullest is None or len(assignment[fullest]) < 2:
                break
            assignment[empty[0]].append(_outlier(assignment[fullest]))
        _pull_nearby(assignment, caps)

        # Geographic order inside each day.
        for index, rows in assignment.items():
            assignment[index] = _nearest_order(rows, rows[0].get("geo") if rows else None)
        return assignment

    async def _matrix_order(self, assignment: dict[int, list[dict]], toolbox) -> dict[int, list[dict]]:
        """Reorder each day by real walking minutes instead of straight-line km."""
        for index, rows in assignment.items():
            if len(rows) < 3:
                continue
            points: list[GeoPoint] = []
            for row in rows:
                geo = row.get("geo") or {}
                if geo.get("lat") is None or geo.get("lng") is None:
                    points = []
                    break
                points.append(GeoPoint(lat=float(geo["lat"]), lng=float(geo["lng"])))
            if len(points) != len(rows):
                continue
            try:
                table = await toolbox.call("routes.matrix", self.matrix, points=points, mode="walk")
            except Exception:
                continue
            if not table:
                continue
            order = _best_open_path(table)
            if order:
                assignment[index] = [rows[position] for position in order]
        return assignment

    def _assign_meals(self, meals: list[dict], assignment: dict[int, list[dict]], destination: GeoLocation) -> dict[int, dict]:
        remaining = list(meals)
        plan: dict[int, dict] = {}
        for index in sorted(assignment):
            rows = assignment[index]
            centre = _centroid([row.get("geo") for row in rows]) or destination.geo.model_dump()
            if remaining:
                lunch = _closest(remaining, centre)
                remaining.remove(lunch)
                plan.setdefault(index, {})["lunch"] = lunch
            if remaining and rows:
                dinner = _closest(remaining, centre)
                remaining.remove(dinner)
                plan.setdefault(index, {})["dinner"] = dinner
        return plan

    @staticmethod
    def _choose_hotel(hotels: list[dict], draft: PlannerDraft | None) -> dict | None:
        if not hotels:
            return None
        if draft and draft.hotel_id:
            for row in hotels:
                if row.get("id") == draft.hotel_id:
                    return row
        return hotels[0]

    @staticmethod
    def _build_hotel(hotel: dict | None, date_range: DateRange) -> HotelStay | None:
        if not hotel:
            return None
        price = hotel.get("nightly_price")
        return HotelStay(
            id=f"hotel_{hotel.get('id', '1')}",
            name=hotel["name"],
            area=hotel.get("area", ""),
            geo=hotel.get("geo"),
            check_in=date_range.start,
            check_out=date_range.end,
            price_per_night=Money.model_validate(price) if price else None,
            price_kind=PriceKind(hotel.get("price_kind", "none")),
            rating=hotel.get("rating"),
            review_count=hotel.get("review_count"),
            distance_to_centre_km=hotel.get("distance_to_centre_km"),
            source_refs=[SourceRef.model_validate(ref) for ref in hotel.get("source_refs", [])],
            data_status=DataStatus.OK,
            fetched_at=utcnow(),
            booking_url=hotel.get("booking_url", ""),
            notes=hotel.get("notes", ""),
            amenities=[str(item) for item in hotel.get("amenities") or []],
            hotel_class=hotel.get("hotel_class"),
            property_token=str(hotel.get("property_token") or ""),
            nearby=[HotelNearby.model_validate(row) for row in hotel.get("nearby") or [] if isinstance(row, dict)],
            # What the stay research step fetched and judged, when it ran.
            room_types=[RoomType.model_validate(row) for row in hotel.get("room_types") or [] if isinstance(row, dict)],
            rooms_checked_for=(
                DateRange.model_validate(hotel["rooms_checked_for"]) if hotel.get("rooms_checked_for") else None
            ),
            match=StayMatch.model_validate(hotel["match"]) if hotel.get("match") else None,
            image=image_of(hotel),
        )

    @staticmethod
    def hotel_options(candidates: list[dict], chosen: HotelStay | None, date_range: DateRange) -> list[HotelStay]:
        """Up to MAX_HOTEL_OPTIONS source-backed stays, the chosen one first and the same object as ``hotels[0]``."""
        options: list[HotelStay] = []
        for row in candidates:
            try:
                stay = Planner._build_hotel(row, date_range)
            except Exception:
                continue
            if stay is not None:
                options.append(stay)
        if chosen is not None:
            options = [stay for stay in options if stay.id != chosen.id and stay.name != chosen.name]
            options.insert(0, chosen)
        return options[:MAX_HOTEL_OPTIONS]

    @staticmethod
    def keep_stay(stay: HotelStay, candidates: list[dict], date_range: DateRange) -> tuple[HotelStay, bool]:
        """The stay the traveller already has, for other dates: priced again from the candidate of its name.

        It keeps its id and its confirmed and locked marks. When no candidate of that name carries a price,
        the old rate stays (with its own fetch time) and the second value is False: nothing was refreshed.
        """
        row = next((row for row in candidates if row.get("name") == stay.name and row.get("nightly_price")), None)
        fresh = Planner._build_hotel(row, date_range)
        if fresh is None:
            return stay.model_copy(update={"check_in": date_range.start, "check_out": date_range.end}), False
        return fresh.model_copy(update={"id": stay.id, "confirmed": stay.confirmed, "locked": stay.locked}), True

    @staticmethod
    def _build_tickets(flights: list[dict]) -> list[TicketOption]:
        tickets: list[TicketOption] = []
        for row in flights[:MAX_TICKET_OPTIONS]:
            try:
                tickets.append(
                    TicketOption(
                        id=f"flight_{row.get('id', len(tickets) + 1)}",
                        mode="flight",
                        name=" ".join(part for part in [row.get("airline"), row.get("flight_number")] if part)
                        or "Flight",
                        origin=row.get("origin", ""),
                        destination=row.get("destination", ""),
                        depart_at=_parse_dt(row.get("depart_at")),
                        arrive_at=_parse_dt(row.get("arrive_at")),
                        duration_minutes=row.get("duration_minutes"),
                        return_origin=row.get("return_origin", ""),
                        return_destination=row.get("return_destination", ""),
                        return_depart_at=_parse_dt(row.get("return_depart_at")),
                        return_arrive_at=_parse_dt(row.get("return_arrive_at")),
                        return_duration_minutes=row.get("return_duration_minutes"),
                        return_airline=row.get("return_airline", ""),
                        return_flight_number=row.get("return_flight_number", ""),
                        price=Money.model_validate(row["price"]) if row.get("price") else None,
                        price_kind=PriceKind(row.get("price_kind", "reference")),
                        operator=row.get("airline", ""),
                        booking_url=row.get("booking_url", ""),
                        notes="Reference itinerary. Confirm fare and baggage with the airline.",
                        source_refs=[SourceRef.model_validate(ref) for ref in row.get("source_refs", [])],
                        data_status=DataStatus.OK,
                        fetched_at=utcnow(),
                    )
                )
            except Exception:
                continue
        return tickets

    def _cost(
        self,
        context: TripContext,
        hotel: HotelStay | None,
        tickets: list[TicketOption],
        date_range: DateRange,
    ) -> CostBreakdown:
        currency = (context.currency or self.settings.default_currency).upper()
        lines: list[CostLine] = []
        unknown: list[str] = []
        nights = max((date_range.end - date_range.start).days, 0)
        if hotel and hotel.price_per_night:
            amount = self.fx.convert(hotel.price_per_night.amount, hotel.price_per_night.currency, currency) * nights
            lines.append(
                CostLine(
                    label=f"Hotel · {hotel.name} × {nights} night{'s' if nights != 1 else ''}",
                    amount=round(amount, 2),
                    kind="reference",
                    note="Reference nightly rate × nights; availability not confirmed.",
                )
            )
        elif hotel:
            unknown.append("Hotel price was not returned.")
        flight = selected_ticket(tickets)
        if flight is not None:
            if flight.price:
                amount = self.fx.convert(flight.price.amount, flight.price.currency, currency)
                lines.append(
                    CostLine(
                        label=f"Flights · {flight.name}",
                        amount=round(amount, 2),
                        kind="reference",
                        note="Reference fare for the travelling party.",
                    )
                )
            else:
                unknown.append("Flight fare was not returned.")
        unknown.append("Local transport, meals and attraction tickets are not costed because no source returned prices.")
        total = round(sum(line.amount for line in lines), 2)
        budget = context.budget
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
            status=status,  # type: ignore[arg-type]
            notes=unknown,
        )

    def cost_for_plan(self, context: TripContext, plan: TripPlan) -> CostBreakdown:
        """The cost of a whole plan.

        A one-city plan is priced as ``_cost`` has always priced it. A plan of several cities (or with a split stay)
        is priced from each stay for its own nights and the chosen option of every leg; a leg whose option is an
        estimate reserves nothing, and the notes say so.
        """
        if len(plan.segments) <= 1 and len(plan.route_legs()) <= 1 and len(plan.hotels) <= 1:
            return self._cost(context, plan.first_stay, plan.tickets, plan.dates)
        currency = (context.currency or self.settings.default_currency).upper()
        lines: list[CostLine] = []
        unknown: list[str] = []
        for stay in plan.hotels:
            nights = max((stay.check_out - stay.check_in).days, 0)
            if stay.price_per_night:
                amount = self.fx.convert(stay.price_per_night.amount, stay.price_per_night.currency, currency) * nights
                lines.append(
                    CostLine(
                        label=f"Hotel · {stay.name} × {nights} night{'s' if nights != 1 else ''}",
                        amount=round(amount, 2),
                        kind="reference",
                        note="Reference nightly rate × nights; availability not confirmed.",
                    )
                )
            else:
                unknown.append(f"Hotel price was not returned for {stay.name}.")
        legs = {leg.id: leg for leg in plan.route_legs()}
        for ticket in plan.chosen_tickets():
            leg = legs.get(plan.leg_id_of(ticket))
            where = f"{leg.from_place} to {leg.to_place}" if leg else ticket.name
            if ticket.price:
                amount = self.fx.convert(ticket.price.amount, ticket.price.currency, currency)
                lines.append(
                    CostLine(
                        label=f"{'Flights' if ticket.mode == 'flight' else 'Transport'} · {ticket.name} ({where})",
                        amount=round(amount, 2),
                        kind="reference",
                        note="Reference fare for the travelling party.",
                    )
                )
            elif ticket.estimated:
                unknown.append(f"Transport from {where} is an estimate and is not costed.")
            else:
                unknown.append(f"Fare was not returned for {where}.")
        unknown.append("Local transport, meals and attraction tickets are not costed because no source returned prices.")
        total = round(sum(line.amount for line in lines), 2)
        budget = context.budget
        status = "unknown"
        if budget is not None:
            status = "within" if total <= budget else "over"
        return CostBreakdown(
            currency=currency,
            lines=lines,
            total=total,
            budget=budget,
            remaining=round(budget - total, 2) if budget is not None else None,
            status=status,  # type: ignore[arg-type]
            notes=unknown,
        )


def apply_audit_blocks(rows: list[dict], blocked: set[str]) -> tuple[list[dict], set[str]]:
    """Drop the audit's blocked rows from one candidate section, within a cap.

    Only ids that are in this section count. They are removed only when that is at
    most half the section and at least one row remains; otherwise nothing is removed
    and the findings stay as warnings for the planner.
    """
    hits = {str(row.get("id")) for row in rows if str(row.get("id")) in blocked}
    if not hits or len(hits) > len(rows) // 2 or len(hits) >= len(rows):
        return rows, set()
    return [row for row in rows if str(row.get("id")) not in hits], hits


AUDIT_BRIEF_LIMIT = 20
_SEVERITY_ORDER = {AuditSeverity.BLOCK: 0, AuditSeverity.WARN: 1, AuditSeverity.INFO: 2}


def _drop_urls(text: str, urls: set[str]) -> str:
    """``text`` without the URLs in ``urls`` (compared case-insensitively, trailing .,;:) aside)."""
    wanted = {url.lower() for url in urls}

    def replace(match: re.Match[str]) -> str:
        found = match.group(0)
        core = found.rstrip(".,;:)")
        return found[len(core):] if core.lower() in wanted else found

    dropped = _URL.sub(replace, text)
    if dropped == text:
        return text
    return re.sub(r"[ \t]{2,}", " ", dropped).strip()


def _audit_brief(audit: AuditReport, kept_ids: set[str]) -> list[dict]:
    """The audit findings the planner sees: those about a kept candidate or a whole section.

    At most AUDIT_BRIEF_LIMIT entries, blocks first, then warnings, then notes, in report order within each.
    """
    brief: list[dict] = []
    ordered = sorted(enumerate(audit.findings), key=lambda pair: _SEVERITY_ORDER.get(pair[1].severity, 3))
    for index, finding in ordered:
        if len(brief) >= AUDIT_BRIEF_LIMIT:
            break
        if finding.item_id is not None and finding.item_id not in kept_ids:
            continue
        evidence = [
            {"title": ref.title, "url": ref.url}
            for entry in audit.evidence
            if entry.finding_index == index
            for ref in entry.references
        ]
        brief.append(
            {
                "agent": str(finding.agent),
                "item_id": finding.item_id,
                "issue": str(finding.issue),
                "severity": str(finding.severity),
                "detail": finding.detail,
                "evidence": evidence,
            }
        )
    return brief


def _summary(
    draft: PlannerDraft | None,
    context: TripContext,
    date_range: DateRange,
    destination: GeoLocation,
) -> str:
    if draft and draft.summary.strip():
        return draft.summary.strip()
    days = len(date_range.dates)
    party = context.party_size or 1
    # The summary is also the chat reply, so it describes the trip only; how the
    # app groups stops and marks freshness is in the help text, not repeated here.
    return f"A {days}-day trip to {context.destination} for {party} traveller{'s' if party != 1 else ''}."


def _trim_place(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "category": row.get("category"),
        "description": (row.get("description") or "")[:260],
        "geo": row.get("geo"),
        "rating": row.get("rating"),
        "opening_hours": (row.get("opening_hours") or [])[:2],
        "address": row.get("address"),
        "source_count": len(row.get("source_refs") or []),
    }


def _trim_hotel(row: dict) -> dict:
    price = row.get("nightly_price") or {}
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "area": row.get("area"),
        "rating": row.get("rating"),
        "price": price.get("amount"),
        "currency": price.get("currency"),
        "distance_to_centre_km": row.get("distance_to_centre_km"),
        "price_kind": row.get("price_kind"),
    }


def _route_length(rows: list[dict]) -> float:
    total = 0.0
    for first, second in zip(rows, rows[1:]):
        a, b = first.get("geo") or {}, second.get("geo") or {}
        total += haversine_km(a.get("lat", 0), a.get("lng", 0), b.get("lat", 0), b.get("lng", 0))
    return total


def _two_opt(rows: list[dict]) -> list[dict]:
    """Open-path 2-opt from the fixed first stop.

    The planner has already chosen *which* places belong to a day; this only
    improves their order. It never moves the first stop, so hotel/day anchoring
    remains stable.
    """
    if len(rows) < 4:
        return rows
    best = list(rows)
    best_length = _route_length(best)
    improved = True
    passes = 0
    while improved and passes < 30:
        improved = False
        passes += 1
        for i in range(1, len(best) - 1):
            for j in range(i + 1, len(best)):
                candidate = best[:i] + list(reversed(best[i : j + 1])) + best[j + 1 :]
                length = _route_length(candidate)
                if length + 1e-9 < best_length:
                    best, best_length = candidate, length
                    improved = True
    return best


def _best_open_path(table: list[list[float]]) -> list[int] | None:
    """Best visit order with the first stop fixed, using real travel minutes."""
    from itertools import permutations

    size = len(table)
    if size < 3:
        return list(range(size))
    best: list[int] | None = None
    best_cost = float("inf")
    for tail in permutations(range(1, size)):
        path = (0, *tail)
        cost = sum(table[path[i]][path[i + 1]] for i in range(len(path) - 1))
        if cost < best_cost:
            best_cost = cost
            best = list(path)
    return best if best_cost != float("inf") else None


def _point(row: dict) -> tuple[float, float] | None:
    geo = row.get("geo") or {}
    lat, lng = geo.get("lat"), geo.get("lng")
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


def _pull_nearby(assignment: dict[int, list[dict]], caps: list[int]) -> None:
    """Move a stop onto a nearer day when this day has scattered it across the city.

    A stop more than 8 km from the rest of its day moves if another day with room
    is at least 8 km closer. A day is not emptied.
    """
    for _ in range(4):
        moved = False
        for index, rows in list(assignment.items()):
            if len(rows) < 2:
                continue
            others = [point for row in rows if (point := _point(row))]
            if len(others) < 2:
                continue
            centre = (sum(lat for lat, _ in others) / len(others), sum(lng for _, lng in others) / len(others))
            for row in list(rows):
                point = _point(row)
                if point is None:
                    continue
                here = haversine_km(centre[0], centre[1], point[0], point[1])
                if here < 8:
                    continue
                best: int | None = None
                best_km = here - 8
                for other, other_rows in assignment.items():
                    if other == index or len(other_rows) >= caps[other]:
                        continue
                    neighbours = [item for item in other_rows if _point(item)]
                    if not neighbours:
                        continue
                    there = min(
                        haversine_km(point[0], point[1], place[0], place[1])
                        for place in (_point(item) for item in neighbours)
                        if place is not None
                    )
                    if there < best_km:
                        best, best_km = other, there
                if best is None:
                    continue
                rows.remove(row)
                assignment[best].append(row)
                moved = True
                break
            if moved:
                break
        if not moved:
            return


def _nearest_order(rows: list[dict], start: dict | None) -> list[dict]:
    if len(rows) <= 2:
        return rows
    remaining = list(rows)
    ordered: list[dict] = []
    current = start
    if not current:
        ordered.append(remaining.pop(0))
        current = ordered[-1].get("geo")
    while remaining:
        if current:
            def distance(row: dict) -> float:
                geo = row.get("geo") or {}
                return haversine_km(current["lat"], current["lng"], geo.get("lat", 0), geo.get("lng", 0))

            nearest = min(remaining, key=distance)
        else:
            nearest = remaining[0]
        ordered.append(nearest)
        remaining.remove(nearest)
        current = nearest.get("geo")
    return _two_opt(ordered)


def _centroid(points: list[dict | None]) -> dict | None:
    valid = [point for point in points if point and "lat" in point and "lng" in point]
    if not valid:
        return None
    return {
        "lat": sum(point["lat"] for point in valid) / len(valid),
        "lng": sum(point["lng"] for point in valid) / len(valid),
    }


def _outlier(rows: list[dict]) -> dict:
    """Remove and return the stop that adds most travel to its day: the one farthest from the others' centre.

    Without coordinates to compare, the day's last stop. A tie goes to the later stop.
    """
    def distance(position: int) -> float:
        centre = _centroid([row.get("geo") for index, row in enumerate(rows) if index != position])
        geo = rows[position].get("geo") or {}
        if centre is None or geo.get("lat") is None or geo.get("lng") is None:
            return -1.0
        return haversine_km(centre["lat"], centre["lng"], geo["lat"], geo["lng"])

    scores = [distance(position) for position in range(len(rows))]
    best = max(scores)
    return rows.pop(len(rows) - 1 - scores[::-1].index(best))


def _closest(rows: list[dict], centre: dict) -> dict:
    def distance(row: dict) -> float:
        geo = row.get("geo") or {}
        if not geo:
            return 10**6
        return haversine_km(centre["lat"], centre["lng"], geo.get("lat", 0), geo.get("lng", 0))

    return min(rows, key=distance)


def _pace(context: Any) -> str:
    """The pace planning uses: the profile's (relaxed, packed), else the older "slow pace" / "fast pace" tags."""
    profile_pace = profile_of(context).pace
    if profile_pace in {"relaxed", "packed"}:
        return profile_pace
    preferences = {str(item).strip().lower() for item in context.preferences}
    if "slow pace" in preferences:
        return "slow"
    if "fast pace" in preferences:
        return "fast"
    return "balanced"


def _must_see(context: Any) -> list[str]:
    return [
        str(item).split(":", 1)[1].strip()
        for item in context.preferences
        if str(item).lower().startswith("must-see:") and ":" in str(item)
    ]


_OPEN_ALL_DAY = re.compile(r"\b24\s*(?:hours|h|/7)\b|open\s*24", re.IGNORECASE)
_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b(\d{1,2}):(\d{2})\b", re.IGNORECASE)


def _opening_minutes(row: dict) -> int | None:
    """Earliest opening time readable from the source's opening hours.

    Google Places returns entries such as "Monday: 9:00 AM - 5:00 PM"; guide data
    may return "24 hours" or "09:00-17:00". Anything unparseable is ignored.
    """
    for entry in (row.get("opening_hours") or [])[:2]:
        text = str(entry)
        if _OPEN_ALL_DAY.search(text):
            return 0
        match = _CLOCK.search(text)
        if not match:
            continue
        if match.group(3):
            hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "pm" else 0)
            minute = int(match.group(2) or 0)
        else:
            hour, minute = int(match.group(4)), int(match.group(5))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    return None


def _starts_early(row: dict) -> bool:
    """Should this stop be scheduled before the crowds?

    Destination-independent signals only: the source says it opens early, or the
    review count marks it as a place crowds build at later in the day.
    """
    opening = _opening_minutes(row)
    if opening is not None and opening <= EARLY_OPEN_BY:
        return True
    return (row.get("review_count") or 0) >= POPULAR_REVIEW_COUNT



def _normalise_name(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", name.lower()).strip()


_NAME_NOISE = {
    "the", "and", "of", "in", "at", "de", "du", "la", "le",
    "temple", "shrine", "garden", "park", "grove", "forest", "bamboo",
}


def _name_tokens(name: str) -> set[str]:
    return {
        token
        for token in _normalise_name(name).split()
        if (len(token) > 2 or token.isdigit()) and token not in _NAME_NOISE
    }


def _similar_place(first: str, second: str) -> bool:
    """True when two candidate names almost certainly mean the same place."""
    if not first or not second:
        return False
    if first == second:
        return True
    # Whole-word containment ("arashiyama" inside "arashiyama bamboo forest"), not
    # a raw substring: "eta" is inside "beta", but they are different places.
    short, longer = (first, second) if len(first) <= len(second) else (second, first)
    if len(short) >= 4 and re.search(rf"(?:^|\s){re.escape(short)}(?:\s|$)", longer):
        return True
    a, b = _name_tokens(first), _name_tokens(second)
    if not a or not b:
        return False
    shared = a & b
    # One shared word is not enough: "Kyoto National Museum" and "Kyoto Railway
    # Museum" are different places.
    return len(shared) >= 2 and len(shared) / len(a | b) >= 0.6


def _is_must_see(name: str, needles: list[str]) -> bool:
    normalised = _normalise_name(name)
    return any(needle and _normalise_name(needle) in normalised for needle in needles)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _minutes_of(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _round5(minutes: int) -> int:
    """Round up to the next 5 minutes so a transfer never overlaps the last stop."""
    return -(-minutes // 5) * 5


def _item_id(row: dict) -> str:
    return f"item_{row['id']}"


def _geo_point(row: dict | None) -> GeoPoint | None:
    geo = (row or {}).get("geo") or {}
    if geo.get("lat") is None or geo.get("lng") is None:
        return None
    return GeoPoint(lat=float(geo["lat"]), lng=float(geo["lng"]))


def _hours(row: dict, day_date: date) -> tuple[list[str], list[tuple[int, int]] | None]:
    """The source's hours lines for the day, and the windows read from them."""
    stated = [str(line) for line in (row.get("opening_hours") or []) if str(line).strip()]
    return lines_for(stated, day_date), windows_on(stated, day_date)


def _move_closed_stops(assignment: dict[int, list[dict]], dates: list[date], caps: list[int]) -> None:
    """A stop the source says is closed on its day moves to a day it is open, if one has room."""
    for index in sorted(assignment):
        if index >= len(dates):
            continue
        for row in list(assignment[index]):
            if windows_on(row.get("opening_hours") or [], dates[index]) != []:
                continue
            for other in sorted(assignment):
                if other == index or other >= len(dates) or len(assignment[other]) >= caps[other]:
                    continue
                if windows_on(row.get("opening_hours") or [], dates[other]) != []:
                    assignment[index].remove(row)
                    assignment[other].append(row)
                    break


def _schedule_day(
    day_date: date,
    places: list[dict],
    lunch: dict | None,
    dinner: dict | None,
    *,
    pace: str = "balanced",
    must_see: list[str] | None = None,
    window: DayWindow | None = None,
    legs: Legs | None = None,
    tool: str = "",
    stops_end_by: int | None = None,
    step_free: bool = False,
) -> list[PlanItem]:
    return _schedule(
        day_date, places, lunch, dinner, pace=pace, must_see=must_see, window=window, legs=legs, tool=tool,
        stops_end_by=stops_end_by, step_free=step_free,
    )[0]


def _schedule(
    day_date: date,
    places: list[dict],
    lunch: dict | None,
    dinner: dict | None,
    *,
    pace: str = "balanced",
    must_see: list[str] | None = None,
    window: DayWindow | None = None,
    legs: Legs | None = None,
    tool: str = "",
    breakfast: dict | None = None,
    snack: dict | None = None,
    stops_end_by: int | None = None,
    step_free: bool = False,
) -> tuple[list[PlanItem], list[str]]:
    """Lay the day out chronologically with realistic transfer time.

    Times are derived, not slotted. Every stop, meals included, starts no earlier
    than the previous stop's end plus the transfer (Proposal TC-07): the leg comes
    from the day's routes.segments result, plus the pace buffer. Lunch starts at
    12:30 or after its transfer; a stop that would keep lunch waiting more than
    LUNCH_SLACK minutes goes after it. Dinner starts at 19:00 or after its
    transfer. A stop is placed inside the opening hours its source states for
    that date. A named must-see, or a crowd-sensitive marquee site, takes the
    08:00 slot before the crowds when its hours allow. On a flight day nothing
    starts before the traveller is in town or ends after they must leave for the
    airport. Returns the items and the names of stops that did not fit.
    """
    open_from = window.start if window and window.start is not None else None
    open_until = window.end if window and window.end is not None else None
    if lunch and ((open_from is not None and open_from > LUNCH_START) or (open_until is not None and open_until < LUNCH_END)):
        lunch = None
    if dinner and (
        (open_from is not None and open_from > DINNER_START) or (open_until is not None and open_until < DINNER_END)
    ):
        dinner = None
    if breakfast and (
        (open_from is not None and open_from > BREAKFAST_LATEST)
        or (open_until is not None and open_until < BREAKFAST_END)
    ):
        breakfast = None
    must_see = must_see or []
    legs = legs or {}
    ordered = list(places)
    if must_see:
        ordered.sort(key=lambda row: 0 if _is_must_see(str(row.get("name", "")), must_see) else 1)

    dwell = PACE_DWELL_MINUTES.get(pace, 90)
    buffer = PACE_BUFFER_MINUTES.get(pace, 5)
    gap = PACE_MIN_GAP.get(pace)  # the least time between two stops; a balanced day has no floor
    start_at = PACE_FIRST_START.get(pace, 9 * 60)
    first = ordered[0] if ordered else None
    if first:
        named = _is_must_see(str(first.get("name", "")), must_see)
        # Early when the traveller named it, the source says it opens early, it is
        # popular enough to get busy, or the day simply has a lot to fit in.
        if named or _starts_early(first) or len(ordered) >= 4:
            start_at = min(start_at, 8 * 60)
    if open_from is not None:
        start_at = max(start_at, open_from)

    def leg(previous: dict | None, row: dict) -> RouteLeg | None:
        if previous is None:
            return None
        origin, destination = _geo_point(previous), _geo_point(row)
        if origin is None or destination is None:
            return None
        found = legs.get((_item_id(previous), _item_id(row)))
        if found is None:
            # Not in the tool result (no toolbox, or the call failed): the same
            # model, computed here, and recorded as not logged.
            km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
            mode, minutes = model_travel(km, step_free)
            found = RouteLeg(
                from_geo=origin, to_geo=destination, duration_minutes=minutes,
                distance_km=round(km, 3), mode=mode, source="transport-model",
            )
        return found

    def arrival(
        previous: dict | None, previous_end: int | None, row: dict, default: int, sight: bool = False
    ) -> int:
        if previous is None or previous_end is None:
            return default
        found = leg(previous, row)
        wait = (found.duration_minutes or 0) + buffer if found else 0
        if sight and gap is not None:
            wait = max(wait, gap)
        return previous_end + wait

    def fit(
        row: dict, earliest: int, minutes: int, shortest: int, latest: int | None = None
    ) -> tuple[int, int] | None:
        windows = _hours(row, day_date)[1]
        if windows is None:
            windows = [(0, DAY_MINUTES)]
        for opens, closes in windows:
            # A flight day also ends when the traveller must leave for the airport.
            closes = min(closes, DAY_LAST_END, DAY_MINUTES if open_until is None else open_until)
            if latest is not None:
                closes = min(closes, latest)
            start = _round5(max(earliest, opens))
            if start + shortest <= closes:
                return start, min(start + minutes, closes)
        return None

    def transfer(previous: dict | None, row: dict) -> TransferLeg | None:
        found = leg(previous, row)
        return transfer_from(found, _item_id(previous), tool) if found and previous else None

    items: list[PlanItem] = []
    left_out: list[str] = []
    state: dict[str, Any] = {"row": None, "end": None}

    def place_meal(row: dict, label: str, usual: int, latest: int, minutes: int) -> None:
        earliest = max(usual, arrival(state["row"], state["end"], row, usual))
        slot = fit(row, earliest, minutes, minutes)
        if slot is None or slot[0] > latest:
            left_out.append(f"{label.lower()} at {row['name']}")
            return
        items.append(
            _meal_item(label, _hhmm(slot[0]), _hhmm(slot[1]), row, day_date=day_date, transfer=transfer(state["row"], row))
        )
        state["row"], state["end"] = row, slot[1]

    if breakfast is not None:
        # Breakfast is the day's first stop; the first sight then starts after it.
        place_meal(
            breakfast, "Breakfast", max(BREAKFAST_START, open_from or 0), BREAKFAST_LATEST,
            BREAKFAST_END - BREAKFAST_START,
        )

    lunch_due = lunch is not None
    queue = list(ordered)
    deferred: set[int] = set()
    while queue:
        row = queue.pop(0)
        # Never before the day's usual start (a breakfast stop ends earlier than that).
        earliest = max(start_at, arrival(state["row"], state["end"], row, start_at, sight=True))
        slot = fit(row, earliest, dwell, min(MIN_DWELL_MINUTES, dwell), latest=stops_end_by)
        if slot and slot[0] - earliest > OPENING_WAIT_LIMIT and queue and id(row) not in deferred:
            deferred.add(id(row))
            queue.append(row)
            continue
        if slot and lunch_due and lunch is not None:
            to_lunch = leg(row, lunch)
            ready = slot[1] + ((to_lunch.duration_minutes or 0) + buffer if to_lunch else 0)
            if ready > LUNCH_START + LUNCH_SLACK:
                lunch_due = False
                place_meal(lunch, "Lunch", LUNCH_START, LUNCH_LATEST, LUNCH_END - LUNCH_START)
                earliest = max(start_at, arrival(state["row"], state["end"], row, start_at, sight=True))
                slot = fit(row, earliest, dwell, min(MIN_DWELL_MINUTES, dwell), latest=stops_end_by)
        if slot and dinner is not None:
            to_dinner = leg(row, dinner)
            if slot[1] + ((to_dinner.duration_minutes or 0) + buffer if to_dinner else 0) > DINNER_LATEST:
                slot = None
        if slot is None:
            left_out.append(row["name"])
            continue
        start, end = slot
        hours_lines = _hours(row, day_date)[0]
        moved_in = transfer(state["row"], row)
        note = (row.get("description") or "").strip()
        note = f"{note} {transfer_sentence(moved_in)}".strip()
        items.append(
            PlanItem(
                id=_item_id(row),
                type="attraction",
                name=row["name"],
                start=_hhmm(start),
                end=_hhmm(end),
                geo=row.get("geo"),
                address=row.get("address", ""),
                notes=note[:700],
                source_refs=[SourceRef.model_validate(ref) for ref in row.get("source_refs", [])],
                data_status=DataStatus(row.get("data_status", "ok")),
                fetched_at=utcnow(),
                tags=(row.get("tags") or [])[:4],
                opening_hours=hours_lines,
                hours_status="stated" if hours_lines else "unknown",
                transfer=moved_in,
                names=trusted_names(row.get("names")),
                image=image_of(row),
                step_free=row.get("step_free") if isinstance(row.get("step_free"), bool) else None,
            )
        )
        state["row"], state["end"] = row, end

    if lunch_due and lunch is not None:
        place_meal(lunch, "Lunch", LUNCH_START, LUNCH_LATEST, LUNCH_END - LUNCH_START)
    if dinner is not None:
        place_meal(dinner, "Dinner", DINNER_START, DINNER_LATEST, DINNER_END - DINNER_START)
    # Chronological order is the contract; meals are not "after all sights".
    items.sort(key=lambda item: (_minutes_of(item.start), 0 if item.type == "attraction" else 1))
    if snack is not None:
        _place_snack(items, snack, day_date, buffer=buffer, until=open_until)
    return items, left_out


SNACK_MINUTES = 30
SNACK_EARLIEST, SNACK_LATEST = 14 * 60, 18 * 60


def _place_snack(items: list[PlanItem], row: dict, day_date: date, *, buffer: int, until: int | None) -> None:
    """Put a cafe stop in the longest afternoon gap after a sight, when it fits with its transfers.

    The transfers in and out are the app's own transport model. The stop after it gets its
    transfer from the cafe. Nothing moves; with no gap long enough there is no cafe stop.
    """
    point = _geo_point(row)
    if point is None:
        return
    windows = _hours(row, day_date)[1]
    best: tuple[int, int, int, RouteLeg, RouteLeg | None] | None = None
    for position, previous in enumerate(items):
        if previous.type != ItemType.ATTRACTION or previous.geo is None:
            continue
        following = items[position + 1] if position + 1 < len(items) else None
        if following is not None and following.type not in {ItemType.ATTRACTION, ItemType.MEAL}:
            continue
        gap_end = _minutes_of(following.start) if following is not None else min(SNACK_LATEST + SNACK_MINUTES, until or DAY_MINUTES)
        into = _model_leg(previous.geo, point)
        out = _model_leg(point, following.geo) if following is not None and following.geo is not None else None
        start = _round5(_minutes_of(previous.end) + (into.duration_minutes or 0) + buffer)
        end = start + SNACK_MINUTES
        latest_end = gap_end - ((out.duration_minutes or 0) + buffer if out is not None else 0)
        if start < SNACK_EARLIEST or start > SNACK_LATEST or end > latest_end:
            continue
        if windows is not None and not any(opens <= start and end <= closes for opens, closes in windows):
            continue
        room = gap_end - _minutes_of(previous.end)
        if best is None or room > best[0]:
            best = (room, position, start, into, out)
    if best is None:
        return
    _, position, start, into, out = best
    previous = items[position]
    item = _meal_item(
        "Cafe stop", _hhmm(start), _hhmm(start + SNACK_MINUTES), row, day_date=day_date,
        transfer=transfer_from(into, previous.id, ""),
    )
    items.insert(position + 1, item)
    following = items[position + 2] if position + 2 < len(items) else None
    if following is not None and out is not None:
        old = transfer_sentence(following.transfer)
        following.transfer = transfer_from(out, item.id, "")
        if old and old in following.notes:
            following.notes = following.notes.replace(old, transfer_sentence(following.transfer))


def _model_leg(origin: GeoPoint, destination: GeoPoint) -> RouteLeg:
    km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
    mode, minutes = model_travel(km)
    return RouteLeg(
        from_geo=origin, to_geo=destination, duration_minutes=minutes,
        distance_km=round(km, 3), mode=mode, source="transport-model",
    )


def _window_limit(window: DayWindow | None, pace: str) -> int | None:
    """How many stops fit in a flight day's free hours; None when the day is whole."""
    if window is None:
        return None
    open_from = max(window.start if window.start is not None else 0, PACE_FIRST_START.get(pace, 9 * 60))
    open_until = window.end if window.end is not None else DAY_END
    slot = PACE_DWELL_MINUTES.get(pace, 90) + SLOT_OVERHEAD_MINUTES
    return max(0, (open_until - open_from) // slot)


def _meal_item(
    label: str,
    start: str,
    end: str,
    row: dict,
    *,
    day_date: date | None = None,
    transfer: TransferLeg | None = None,
) -> PlanItem:
    hours_lines = _hours(row, day_date)[0] if day_date else []
    slot = row.get("meal_slot") if row.get("meal_slot") in {"breakfast", "lunch", "dinner", "snack"} else None
    # The meal's transfer is on the item (``transfer``); its note stays short.
    note = f"{label} candidate from {', '.join(ref.get('source', 'source') for ref in row.get('source_refs', [])) or 'a travel source'}."
    return PlanItem(
        id=_item_id(row),
        type="meal",
        name=row["name"],
        start=start,
        end=end,
        geo=row.get("geo"),
        address=row.get("address", ""),
        notes=note,
        source_refs=[SourceRef.model_validate(ref) for ref in row.get("source_refs", [])],
        data_status=DataStatus.OK,
        fetched_at=utcnow(),
        tags=["meal"],
        opening_hours=hours_lines,
        hours_status="stated" if hours_lines else "unknown",
        transfer=transfer,
        names=trusted_names(row.get("names")),
        meal_slot=slot,
        alternatives=alternatives_for(row),
        **source_facts(row),
    )


def _reservations(places: list[dict], tickets: list[TicketOption]) -> list[Reservation]:
    reservations: list[Reservation] = []
    for row in places:
        text = f"{row.get('name', '')} {row.get('description', '')}".lower()
        if any(keyword in text for keyword in ("reservation required", "book in advance", "timed entry", "advance booking")):
            refs = row.get("source_refs") or []
            reservations.append(
                Reservation(
                    name=row["name"],
                    reason="The source notes that advance booking or a timed entry is required.",
                    url=refs[0].get("url", "") if refs else "",
                    status="to_book",
                )
            )
    flight = selected_ticket(tickets)
    if flight is not None and flight.booking_url:
        reservations.append(
            Reservation(
                name=f"Flight · {flight.name}",
                reason="Reference itinerary; confirm the fare and seat with the airline.",
                url=flight.booking_url,
                status="optional",
            )
        )
    return reservations


def _constraints(
    context: TripContext,
    days: list[DayPlan],
    hotel: HotelStay | None,
    tickets: list[TicketOption],
    cost: CostBreakdown,
    *,
    blocked: dict[date, str] | None = None,
    windows: dict[date, DayWindow] | None = None,
    wishes: dict[str, str | None] | None = None,
    wish_failures: dict[str, str] | None = None,
) -> list[ConstraintCheck]:
    blocked = blocked or {}
    windows = windows or {}
    wishes = wishes or {}
    wish_failures = wish_failures or {}
    checks: list[ConstraintCheck] = []
    if context.budget is not None:
        checks.append(
            ConstraintCheck(
                text=f"Stay within {context.currency} {context.budget:,.0f}",
                status=ConstraintStatus.PASS if cost.status == "within" else ConstraintStatus.FAIL,
                evidence=f"Known cost total is {cost.currency} {cost.total:,.2f}; meals and attraction tickets are not costed.",
            )
        )
    checks.append(
        ConstraintCheck(
            text="Dates are continuous",
            status=ConstraintStatus.PASS,
            evidence=f"{len(days)} days cover {context.start_date} to {context.end_date}.",
        )
    )
    # A day kept free on purpose, or filled by a flight, is not a missing stop.
    empty_days = [
        day.date.isoformat() for day in days if not day.items and day.date not in blocked and day.date not in windows
    ]
    checks.append(
        ConstraintCheck(
            text="Every day has at least one verified stop",
            status=ConstraintStatus.PASS if not empty_days else ConstraintStatus.UNKNOWN,
            evidence="All days populated." if not empty_days else f"No verified stop for: {', '.join(empty_days)}.",
        )
    )
    weather_missing = [day.date.isoformat() for day in days if not (day.weather and day.weather.is_available)]
    checks.append(
        ConstraintCheck(
            text="Weather available for every travel date",
            status=ConstraintStatus.PASS if not weather_missing else ConstraintStatus.UNKNOWN,
            evidence="Forecast available." if not weather_missing else f"No forecast for: {', '.join(weather_missing)}.",
        )
    )
    if len(days) > 1:
        checks.append(
            ConstraintCheck(
                text="Overnight hotel selected",
                status=ConstraintStatus.PASS if hotel else ConstraintStatus.FAIL,
                evidence=hotel.name if hotel else "No hotel source returned a candidate.",
            )
        )
    if context.origin:
        checks.append(
            ConstraintCheck(
                text="Flight option available",
                status=ConstraintStatus.PASS if tickets else ConstraintStatus.UNKNOWN,
                evidence=selected_ticket(tickets).name if tickets else "No flight source returned an option.",
            )
        )
    planned_names = [item.name.lower() for day in days for item in day.items]

    def planned(needle: str) -> bool:
        # The source may know the place by another name: "Lotte World" was found as 롯데월드.
        found_as = wishes.get(needle)
        targets = [needle.lower()] + ([found_as.lower()] if found_as else [])
        return any(target in name for target in targets for name in planned_names)

    checked_needles: set[str] = set()
    for text in context.hard_constraints:
        needle = _must_include(text)
        if needle:
            checked_needles.add(needle.lower())
            found = planned(needle)
            checks.append(
                ConstraintCheck(
                    text=text,
                    status=ConstraintStatus.PASS if found else ConstraintStatus.FAIL,
                    evidence="Matched a planned stop." if found else "No planned stop matches this requirement.",
                )
            )
    # A place named only as a tag gets its own check, so a miss is visible.
    for wish, found_as in wishes.items():
        if wish.lower() in checked_needles:
            continue
        if found_as is None:
            # A lookup that failed is not a place nobody knows.
            status = ConstraintStatus.UNKNOWN
            evidence = wish_failures.get(wish) or "No place source returned a place with this name."
        elif planned(wish):
            status, evidence = ConstraintStatus.PASS, f"Planned as {found_as}."
        else:
            status, evidence = ConstraintStatus.FAIL, f"Found as {found_as}, but no day had room for it."
        checks.append(ConstraintCheck(text=f"Include {wish}", status=status, evidence=evidence))

    by_day = {day.date: day for day in days}
    for text in _dedupe(list(blocked.values())):
        free = sorted(when for when, source in blocked.items() if source == text)
        busy = [when.isoformat() for when in free if by_day.get(when) and by_day[when].items]
        checks.append(
            ConstraintCheck(
                text=text,
                status=ConstraintStatus.FAIL if busy else ConstraintStatus.PASS,
                evidence=(
                    f"Stops were still planned on {', '.join(busy)}."
                    if busy
                    else f"Nothing planned on {', '.join(when.isoformat() for when in free)}."
                ),
            )
        )
    for when, window in sorted(windows.items()):
        day = by_day.get(when)
        items = day.items if day else []
        early = [item for item in items if window.start is not None and _minutes_of(item.start) < window.start]
        late = [item for item in items if window.end is not None and _minutes_of(item.end) > window.end]
        parts = []
        if window.start is not None:
            parts.append(f"nothing starts before {_hhmm(window.start)}")
        if window.end is not None:
            parts.append(f"everything ends by {_hhmm(window.end)}")
        for text in _dedupe(window.texts):
            checks.append(
                ConstraintCheck(
                    text=text,
                    status=ConstraintStatus.FAIL if early or late else ConstraintStatus.PASS,
                    evidence=f"On {when.isoformat()}, {' and '.join(parts)}.",
                )
            )
    return checks


def _required_names(context: TripContext) -> list[str]:
    """Stops named in a must-include hard constraint ("X and Y" counts as two)."""
    names: list[str] = []
    for text in context.hard_constraints:
        needle = _must_include(text)
        if needle:
            names.append(needle)
            names.extend(
                re.sub(r"^(?:the|a|an)\s+", "", part, flags=re.IGNORECASE)
                for part in re.split(r"\s*,\s*|\s+and\s+|\s*&\s*", needle)
                if part and part != needle
            )
    return names


_DIET = re.compile(r"\b(vegan|vegetarian|halal)\b", re.IGNORECASE)


def _diet(context: TripContext) -> str | None:
    found = {match.lower() for text in [*context.preferences, *context.hard_constraints] for match in _DIET.findall(str(text))}
    return next((diet for diet in ("vegan", "vegetarian", "halal") if diet in found), None)


def _serves_diet(row: dict, diet: str) -> bool:
    """True when the source labels the restaurant for the diet (type or wording)."""
    accepted = {f"{diet}_restaurant"} | ({"vegan_restaurant"} if diet == "vegetarian" else set())
    if accepted & {str(tag) for tag in row.get("tags") or []}:
        return True
    words = (diet, "vegan") if diet == "vegetarian" else (diet,)
    text = f"{row.get('name', '')} {row.get('category', '')} {row.get('description', '')}".lower()
    return any(re.search(rf"\b{word}\b", text) for word in words)


def _gaps(results: dict[str, AgentResult]) -> list[DataGap]:
    gaps: list[DataGap] = []
    for result in results.values():
        gaps.extend(result.failures)
        if result.status in {AgentStatus.UNAVAILABLE, AgentStatus.TIMEOUT, AgentStatus.FAILED}:
            sections = {
                "attraction": "attractions",
                "hotel": "hotels",
                "weather": "weather",
                "ticket": "tickets",
            }
            section = sections.get(result.agent.value, result.agent.value)
            if not any(gap.section == section for gap in result.failures):
                gaps.append(
                    DataGap(
                        section=section,
                        kind=FailureKind.NO_DATA,
                        message=f"{result.agent.value} agent returned no usable data.",
                    )
                )
    return gaps


def _open_days_gap(
    days: list[DayPlan], *, blocked: dict[date, str], windows: dict[date, DayWindow]
) -> DataGap | None:
    """A gap naming how many days have a verified stop, when some have none.

    A day kept free on purpose, or shaped by a flight, is not counted (as in `_constraints`); a meal is not a stop.
    """
    counted = [day for day in days if day.date not in blocked and day.date not in windows]
    covered = sum(1 for day in counted if any(item.type is not ItemType.MEAL for item in day.items))
    if covered == len(counted):
        return None
    message = (
        f"Only {covered} of {len(counted)} days have verified stops; the rest are open."
        if covered
        else "No day has a verified stop; the days are open."
    )
    return DataGap(section="attractions", kind=FailureKind.NO_DATA, message=message)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
