"""Grounded planner.

The planner is allowed to decide *selection and order*; it is not allowed to
create facts. Candidate places, hotels, flights and weather all arrive from the
agents with sources. The model returns only ids, day titles and a summary, and
every id is validated before use.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.application.llm_gateway import LLMGateway
from app.application.trip_rules import DayWindow, blocked_dates, travel_windows
from app.application.trip_rules import must_include as _must_include
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import (
    AgentResult,
    AgentStatus,
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
    HotelStay,
    Money,
    NoteItem,
    PlanItem,
    PriceKind,
    Reservation,
    SourceRef,
    TicketOption,
    TripContext,
    TripPlan,
    WeatherDay,
    utcnow,
)
from app.infrastructure.http import haversine_km
from app.infrastructure.providers.fx import FxService


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
# Mirrors the mode thresholds of the console map (2.5 / 35 / 160 km) so the
# itinerary text, the map legend and the schedule never disagree.
TRANSPORT_SPEED_KMH = {"walk": 4.5, "metro": 22.0, "drive": 32.0, "flight": 620.0}
TRANSPORT_OVERHEAD_MIN = {"walk": 0, "metro": 8, "drive": 5, "flight": 90}
TRANSPORT_NOTE = {
    "walk": "on foot",
    "metro": "by metro or bus",
    "drive": "by car or taxi",
    "flight": "by plane",
}

# Dwell time per stop and the earliest start, by pace. A slow trip sees fewer
# places for longer; a packed one starts earlier and moves faster.
PACE_DWELL_MINUTES = {"slow": 120, "balanced": 90, "fast": 75}
PACE_FIRST_START = {"slow": 9 * 60 + 30, "balanced": 9 * 60, "fast": 8 * 60 + 30}
PACE_ATTRACTION_CAP = {"slow": 3, "balanced": 4, "fast": 5}
PACE_BUFFER_MINUTES = {"slow": 15, "balanced": 5, "fast": 0}

# A stop that opens by 08:30, or that carries a large review count, is worth the
# early slot: the generic, destination-independent proxy for "crowds build here".
EARLY_OPEN_BY = 8 * 60 + 30
POPULAR_REVIEW_COUNT = 5000

LUNCH_START, LUNCH_END = 12 * 60 + 30, 13 * 60 + 30
DINNER_START, DINNER_END = 19 * 60, 20 * 60 + 15
# Latest end for a sightseeing stop, used to size a day cut short by a flight.
DAY_END = 21 * 60
# Transfer and buffer allowance per stop when sizing a shortened day.
SLOT_OVERHEAD_MINUTES = 30


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
    ) -> TripPlan:
        places = list(results.get("attraction", AgentResult(agent="attraction", status=AgentStatus.UNAVAILABLE)).payload.get("places", []))
        meals = list(results.get("attraction", AgentResult(agent="attraction", status=AgentStatus.UNAVAILABLE)).payload.get("meals", []))
        hotels = list(results.get("hotel", AgentResult(agent="hotel", status=AgentStatus.UNAVAILABLE)).payload.get("hotels", []))
        flights = list(results.get("ticket", AgentResult(agent="ticket", status=AgentStatus.UNAVAILABLE)).payload.get("flights", []))
        weather_rows = list(results.get("weather", AgentResult(agent="weather", status=AgentStatus.UNAVAILABLE)).payload.get("days", []))
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
        )

        dates = list(date_range.dates)
        day_count = len(dates)
        pace = _pace(context)
        must_see = _must_see(context)
        rule_texts = [*context.hard_constraints, *context.preferences]
        blocked = blocked_dates(rule_texts, dates)
        windows = travel_windows(rule_texts, dates)
        attraction_payload = results.get("attraction").payload if results.get("attraction") else {}
        wishes = {str(key): value for key, value in (attraction_payload.get("wishes") or {}).items()}
        # A named must-include is placed before the rest, so a short trip with
        # more candidates than slots never drops the stop the traveller asked for.
        required = must_see + _required_names(context) + [name for name in wishes.values() if name]
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
        assignment = self._assign_places(places, day_count, draft, pace=pace, limits=limits)
        if self.settings.enable_route_matrix and self.matrix is not None and toolbox is not None:
            assignment = await self._matrix_order(assignment, toolbox)
        meal_days = {index: rows for index, rows in assignment.items() if dates[index] not in blocked}
        meal_plan = self._assign_meals(meals, meal_days, destination)
        hotel = self._choose_hotel(hotels, draft)

        days: list[DayPlan] = []
        for index, day_date in enumerate(dates):
            weather_row = weather_by_date.get(day_date.isoformat())
            weather = WeatherDay.model_validate(weather_row) if weather_row else None
            day_places = [row for row in assignment.get(index, [])]
            lunch = meal_plan.get(index, {}).get("lunch")
            dinner = meal_plan.get(index, {}).get("dinner")
            window = windows.get(day_date)
            items = _schedule_day(day_date, day_places, lunch, dinner, pace=pace, must_see=must_see, window=window)
            title = ""
            if day_date in blocked:
                title = f"Free day in {context.destination}"
            elif draft and index < len(draft.day_titles):
                title = draft.day_titles[index].strip()
            if not title:
                if day_places:
                    title = f"{day_places[0]['name']} and nearby stops"
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
                note = "No verified stops were available for this day."
            days.append(DayPlan(date=day_date, title=title, weather=weather, items=items, note=note))

        hotel_stay = self._build_hotel(hotel, date_range)
        tickets = self._build_tickets(flights)
        reservations = _reservations(places, tickets)
        cost = self._cost(context, hotel_stay, tickets, date_range)
        constraints = _constraints(
            context, days, hotel_stay, tickets, cost, blocked=blocked, windows=windows, wishes=wishes
        )
        gaps = _gaps(results)
        if diet and not meals:
            gaps.append(
                DataGap(
                    section="meals",
                    kind=FailureKind.NO_DATA,
                    message=f"No {diet} restaurant came back from the place sources, so no meal stop is suggested.",
                )
            )
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
        )
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
        try:
            data = await self.gateway.json(
                "planner",
                user=json.dumps(payload, ensure_ascii=False, default=str),
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
    ) -> dict[int, list[dict]]:
        by_id = {row["id"]: row for row in places}
        assignment: dict[int, list[dict]] = {index: [] for index in range(day_count)}
        used: set[str] = set()
        accepted: list[str] = []
        cap = min(self.settings.max_attractions_per_day, PACE_ATTRACTION_CAP.get(pace, 4))
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
        )

    @staticmethod
    def _build_tickets(flights: list[dict]) -> list[TicketOption]:
        tickets: list[TicketOption] = []
        for row in flights[:3]:
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
        if tickets:
            flight = tickets[0]
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
    return (
        f"A {days}-day trip to {context.destination} for {party} traveller{'s' if party != 1 else ''}. "
        "Stops are grouped by location so the days do not backtrack; each stop carries its source "
        "and a freshness status."
    )


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


def _closest(rows: list[dict], centre: dict) -> dict:
    def distance(row: dict) -> float:
        geo = row.get("geo") or {}
        if not geo:
            return 10**6
        return haversine_km(centre["lat"], centre["lng"], geo.get("lat", 0), geo.get("lng", 0))

    return min(rows, key=distance)


def _pace(context: Any) -> str:
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


def _travel(km: float) -> tuple[str, int]:
    """Mode and minutes for a leg, using the same thresholds as the console map."""
    if km < 2.5:
        mode = "walk"
    elif km < 35:
        mode = "metro"
    elif km < 160:
        mode = "drive"
    else:
        mode = "flight"
    minutes = max(2, round(km / TRANSPORT_SPEED_KMH[mode] * 60 + TRANSPORT_OVERHEAD_MIN[mode]))
    return mode, minutes


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


def _schedule_day(
    day_date: date,
    places: list[dict],
    lunch: dict | None,
    dinner: dict | None,
    *,
    pace: str = "balanced",
    must_see: list[str] | None = None,
    window: DayWindow | None = None,
) -> list[PlanItem]:
    """Lay the day out chronologically with realistic transfer time.

    Times are derived, not slotted: each stop follows the previous one after the
    transfer that the distance actually needs (walk / metro / drive), and lunch
    holds its window. A named must-see, or a crowd-sensitive marquee site, takes
    the 08:00 slot before the crowds. On a flight day nothing starts before the
    traveller is in town or ends after they must leave for the airport.
    """
    open_from = window.start if window and window.start is not None else None
    open_until = window.end if window and window.end is not None else None
    if lunch and ((open_from is not None and open_from > LUNCH_START) or (open_until is not None and open_until < LUNCH_END)):
        lunch = None
    if dinner and (
        (open_from is not None and open_from > DINNER_START) or (open_until is not None and open_until < DINNER_END)
    ):
        dinner = None
    must_see = must_see or []
    ordered = list(places)
    if must_see:
        ordered.sort(key=lambda row: 0 if _is_must_see(str(row.get("name", "")), must_see) else 1)

    dwell = PACE_DWELL_MINUTES.get(pace, 90)
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

    items: list[PlanItem] = []
    previous_geo: dict | None = None
    previous_end: int | None = None

    for row in ordered:
        geo = row.get("geo")
        transfer = 0
        transfer_note = ""
        if previous_geo and geo:
            km = haversine_km(
                previous_geo["lat"], previous_geo["lng"], geo["lat"], geo["lng"]
            )
            mode, transfer = _travel(km)
            transfer_note = f" About {transfer} min {TRANSPORT_NOTE[mode]} from the previous stop (estimate)."
            transfer += PACE_BUFFER_MINUTES.get(pace, 5)

        earliest = start_at if previous_end is None else previous_end + transfer
        # Never straddle lunch: move the stop to the afternoon instead. Only when
        # a lunch candidate exists for this day.
        if lunch and earliest < LUNCH_END and earliest + dwell > LUNCH_START:
            earliest = LUNCH_END + 15
        start = _round5(earliest)
        end = start + dwell
        if open_until is not None and end > open_until:
            break

        note = (row.get("description") or "").strip()
        note = (note + transfer_note).strip()
        items.append(
            PlanItem(
                id=f"item_{row['id']}",
                type="attraction",
                name=row["name"],
                start=_hhmm(start),
                end=_hhmm(end),
                geo=geo,
                address=row.get("address", ""),
                notes=note[:700],
                source_refs=[SourceRef.model_validate(ref) for ref in row.get("source_refs", [])],
                data_status=DataStatus(row.get("data_status", "ok")),
                fetched_at=utcnow(),
                tags=(row.get("tags") or [])[:4],
            )
        )
        previous_geo = geo
        previous_end = end

    if lunch:
        items.append(_meal_item("Lunch", _hhmm(LUNCH_START), _hhmm(LUNCH_END), lunch))
    if dinner:
        items.append(_meal_item("Dinner", _hhmm(DINNER_START), _hhmm(DINNER_END), dinner))
    # Chronological order is the contract; meals are not "after all sights".
    items.sort(key=lambda item: (_minutes_of(item.start), 0 if item.type == "attraction" else 1))
    return items


def _window_limit(window: DayWindow | None, pace: str) -> int | None:
    """How many stops fit in a flight day's free hours; None when the day is whole."""
    if window is None:
        return None
    open_from = max(window.start if window.start is not None else 0, PACE_FIRST_START.get(pace, 9 * 60))
    open_until = window.end if window.end is not None else DAY_END
    slot = PACE_DWELL_MINUTES.get(pace, 90) + SLOT_OVERHEAD_MINUTES
    return max(0, (open_until - open_from) // slot)


def _meal_item(label: str, start: str, end: str, row: dict) -> PlanItem:
    return PlanItem(
        id=f"item_{row['id']}",
        type="meal",
        name=row["name"],
        start=start,
        end=end,
        geo=row.get("geo"),
        address=row.get("address", ""),
        notes=f"{label} candidate from {', '.join(ref.get('source', 'source') for ref in row.get('source_refs', [])) or 'a travel source'}.",
        source_refs=[SourceRef.model_validate(ref) for ref in row.get("source_refs", [])],
        data_status=DataStatus.OK,
        fetched_at=utcnow(),
        tags=["meal"],
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
    if tickets and tickets[0].booking_url:
        reservations.append(
            Reservation(
                name=f"Flight · {tickets[0].name}",
                reason="Reference itinerary; confirm the fare and seat with the airline.",
                url=tickets[0].booking_url,
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
) -> list[ConstraintCheck]:
    blocked = blocked or {}
    windows = windows or {}
    wishes = wishes or {}
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
                evidence=tickets[0].name if tickets else "No flight source returned an option.",
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
            status, evidence = ConstraintStatus.UNKNOWN, "No place source returned a place with this name."
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
