from __future__ import annotations

from datetime import date

from app.config import Settings
from app.domain.models import GeoPoint, Money, SourceRef, TripContext
from app.evidence.models import Bundle, Evidence
from app.plan.builder import build_plan, refresh_derived, validate_structure
from app.understand.schemas import DayChoice, PlanDraft, StopChoice


def _settings() -> Settings:
    return Settings(database_path="data/test.db")


def _evidence(ref: str, kind: str, name: str, *, lat: float = 35.0, lng: float = 135.0) -> Evidence:
    return Evidence(
        ref=ref,
        kind=kind,  # type: ignore[arg-type]
        name=name,
        address="1 Test Street",
        geo=GeoPoint(lat=lat, lng=lng),
        summary="A sourced place.",
        source_refs=[SourceRef(title=name, url="https://example.test", source="Test")],
        rating=4.5,
        price=Money(amount=500, currency="JPY") if kind != "hotel" else Money(amount=20000, currency="JPY"),
        price_kind="reference",
        opening_hours=["09:00-17:00"],
        hours_stated=True,
        suggested_minutes=90,
    )


def _bundle() -> Bundle:
    bundle = Bundle()
    bundle.add(
        [
            _evidence("at-1", "attraction", "Temple One"),
            _evidence("at-2", "attraction", "Temple Two", lat=35.02, lng=135.02),
            _evidence("me-1", "meal", "Noodle Shop"),
            _evidence("ho-1", "hotel", "Station Hotel"),
        ],
        "test",
    )
    return bundle


def _draft() -> PlanDraft:
    return PlanDraft(
        summary="Two days of temples.",
        days=[
            DayChoice(
                date="2027-04-10",
                title="Temples",
                stops=[
                    StopChoice(ref="at-1", start="09:00", end="10:30", note="Go early."),
                    StopChoice(ref="at-2", start="10:45", end="12:00", note="Then walk."),
                    StopChoice(ref="me-1", start="12:15", end="13:15", note="Lunch.", meal_slot="lunch"),
                    StopChoice(ref="missing-ref", start="15:00", end="16:00"),
                ],
            ),
            DayChoice(date="2027-04-11", title="Second day", stops=[StopChoice(ref="at-1", start="09:00", end="10:30")]),
        ],
        hotel_ref="ho-1",
        ticket_refs=[],
        confidence=0.8,
    )


def test_builder_uses_only_known_refs():
    context = TripContext(
        session_id="s1", destination="Kyoto", origin="Hong Kong", start_date="2027-04-10",
        end_date="2027-04-11", days=2, party_size=2, budget=200000, currency="JPY",
    )
    plan = build_plan(
        context=context,
        bundle=_bundle(),
        draft=_draft(),
        disruptions=[],
        weather_by_date={},
        start=date(2027, 4, 10),
        end=date(2027, 4, 11),
        settings=_settings(),
    )
    names = [item.name for day in plan.days for item in day.items]
    assert "Temple One" in names and "Noodle Shop" in names
    assert len(names) == 4  # the unknown ref is dropped
    assert all(item.source_refs for day in plan.days for item in day.items)
    assert plan.hotels and plan.hotels[0].name == "Station Hotel"
    assert plan.cost.currency == "JPY"


def test_builder_settles_overlaps_and_transfers():
    context = TripContext(
        session_id="s1", destination="Kyoto", start_date="2027-04-10", end_date="2027-04-11",
        days=2, party_size=1, currency="JPY",
    )
    draft = PlanDraft(
        summary="Packed.",
        days=[
            DayChoice(
                date="2027-04-10",
                stops=[
                    StopChoice(ref="at-1", start="09:00", end="11:00"),
                    StopChoice(ref="at-2", start="09:30", end="10:30"),  # overlaps and is far away
                ],
            ),
            DayChoice(date="2027-04-11", stops=[StopChoice(ref="at-1", start="09:00", end="10:00")]),
        ],
        hotel_ref="ho-1",
    )
    plan = build_plan(
        context=context, bundle=_bundle(), draft=draft, disruptions=[], weather_by_date={},
        start=date(2027, 4, 10), end=date(2027, 4, 11), settings=_settings(),
    )
    day = plan.days[0]
    assert len(day.items) == 2
    assert day.items[0].end <= day.items[1].start
    assert day.items[1].transfer is not None
    assert validate_structure(plan, context) == []


def test_refresh_recomputes_cost():
    context = TripContext(session_id="s1", destination="Kyoto", start_date="2027-04-10", end_date="2027-04-11", days=2, party_size=2, budget=50000, currency="JPY")
    plan = build_plan(
        context=context, bundle=_bundle(), draft=_draft(), disruptions=[], weather_by_date={},
        start=date(2027, 4, 10), end=date(2027, 4, 11), settings=_settings(),
    )
    before = plan.cost.total
    plan.days[0].items[0].price = Money(amount=9000, currency="JPY")
    refresh_derived(plan, context)
    assert plan.cost.total > before
    assert plan.cost.status in {"within", "over"}


def test_validate_flags_over_budget_and_empty_day():
    context = TripContext(session_id="s1", destination="Kyoto", start_date="2027-04-10", end_date="2027-04-11", days=2, party_size=1, budget=100, currency="JPY")
    draft = PlanDraft(
        summary="x",
        days=[
            DayChoice(date="2027-04-10", stops=[StopChoice(ref="at-1", start="09:00", end="10:00")]),
            DayChoice(date="2027-04-11", stops=[]),
        ],
        hotel_ref="ho-1",
    )
    plan = build_plan(
        context=context, bundle=_bundle(), draft=draft, disruptions=[], weather_by_date={},
        start=date(2027, 4, 10), end=date(2027, 4, 11), settings=_settings(),
    )
    codes = {problem.code for problem in validate_structure(plan, context)}
    assert "empty_day" in codes
    assert "over_budget" in codes


def test_fallback_plan_is_sourced_and_flagged():
    from app.plan.builder import fallback_plan

    context = TripContext(
        session_id="s1", destination="Kyoto", start_date="2027-04-10", end_date="2027-04-11",
        days=2, party_size=2, budget=200000, currency="JPY",
    )
    plan = fallback_plan(
        context=context,
        bundle=_bundle(),
        disruptions=[],
        weather_by_date={},
        start=date(2027, 4, 10),
        end=date(2027, 4, 11),
        settings=_settings(),
        reason="no model",
    )
    assert plan.days and all(day.items for day in plan.days)
    assert all(item.source_refs for day in plan.days for item in day.items)
    assert any(gap.kind == "degraded" for gap in plan.data_quality.gaps)


def test_local_trip_has_no_flight_or_legs():
    context = TripContext(
        session_id="s1", destination="Hong Kong", origin="Hong Kong", start_date="2027-04-10",
        end_date="2027-04-11", days=2, party_size=2, budget=9000, currency="HKD",
    )
    plan = build_plan(
        context=context, bundle=_bundle(), draft=_draft(), disruptions=[], weather_by_date={},
        start=date(2027, 4, 10), end=date(2027, 4, 11), settings=_settings(),
    )
    assert plan.tickets == []
    assert plan.legs == []
    assert all(line.kind != "flight" for line in plan.cost.lines)


def test_budget_guard_takes_the_cheaper_stay_then_trims():
    from app.domain.models import HotelStay
    from app.plan.builder import _fit_budget

    context = TripContext(
        session_id="s1", destination="Kyoto", start_date="2027-04-10", end_date="2027-04-11",
        days=2, party_size=1, budget=200000, currency="JPY",
    )
    plan = build_plan(
        context=context, bundle=_bundle(), draft=_draft(), disruptions=[], weather_by_date={},
        start=date(2027, 4, 10), end=date(2027, 4, 11), settings=_settings(),
    )
    context.budget = 4000
    expensive = plan.hotels[0]
    expensive.price_per_night = Money(amount=20000, currency="JPY")
    cheap = HotelStay(
        id="cheap", name="Cheap Stay", check_in=plan.dates.start, check_out=plan.dates.end,
        price_per_night=Money(amount=5000, currency="JPY"), source_refs=[SourceRef(title="x", url="", source="t")],
    )
    cost, trimmed = _fit_budget(context, plan.days, plan.hotels, [expensive, cheap], plan.tickets)
    assert plan.hotels[0].name == "Cheap Stay"
    assert trimmed  # still over, so paid stops were dropped
    assert cost.total <= 20000
