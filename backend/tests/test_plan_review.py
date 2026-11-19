"""The review after a plan is drafted: every row, checked by code."""

import asyncio
from datetime import date

import pytest

from app.application.plan_review import ReviewCatalog, ReviewFinding, catalog_from_results, review_plan
from app.application.visit_length import STATED
from app.config import Settings
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    CostBreakdown,
    CostLine,
    DateRange,
    DayPlan,
    Destination,
    GeoPoint,
    HotelStay,
    ItemType,
    Money,
    PlanItem,
    PriceKind,
    SourceRef,
    TicketOption,
    TripContext,
    TripPlan,
    TripSegment,
)

MONDAY = date(2027, 4, 12)
TUESDAY = date(2027, 4, 13)
HK = GeoPoint(lat=22.32, lng=114.17)
TOKYO = GeoPoint(lat=35.68, lng=139.65)


def _ref(name: str) -> SourceRef:
    return SourceRef(title=name, url=f"https://example.test/{name}", source="google-places")


def _stop(item_id: str, name: str, start: str, end: str, **extra) -> PlanItem:
    return PlanItem(
        id=item_id, type=ItemType.ATTRACTION, name=name, start=start, end=end,
        source_refs=[_ref(name)], geo=extra.pop("geo", HK), **extra,
    )


def _meal(item_id: str, name: str, start: str, end: str, **extra) -> PlanItem:
    return PlanItem(
        id=item_id, type=ItemType.MEAL, name=name, start=start, end=end, meal_slot="lunch", **extra,
    )


def _plan(days: list[DayPlan], **extra) -> TripPlan:
    dates = DateRange(start=days[0].date, end=days[-1].date)
    return TripPlan(
        session_id="s",
        destination=Destination(name="Hong Kong", geo=HK),
        dates=dates,
        days=days,
        **extra,
    )


def _codes(findings) -> list[str]:
    return [finding.code for finding in findings]


def test_a_sourced_plan_of_known_places_has_nothing_to_report() -> None:
    plan = _plan([DayPlan(date=MONDAY, items=[
        _stop("a", "Ocean Park", "09:00", "12:00"),
        _meal("l", "Noodle House", "12:30", "13:30"),
    ])])
    catalog = ReviewCatalog(places=frozenset({"ocean park"}), meals=frozenset({"noodle house"}))
    reviewed, findings = review_plan(plan, TripContext(session_id="s", destination="Hong Kong"), catalog)
    assert findings == []
    assert reviewed.days[0].items[1].end == "13:30"


def test_each_fault_is_named_and_only_the_two_repairs_change_the_plan() -> None:
    plan = _plan(
        [
            DayPlan(date=MONDAY, items=[
                _stop("a1", "Ocean Park", "09:00", "10:30"),
                _stop("a2", "Temple", "11:00", "12:30"),
                _meal("l", "Long Lunch", "13:00", "16:00"),
                _stop("a3", "Senso-ji", "16:30", "18:00"),
            ]),
            DayPlan(date=TUESDAY, items=[
                _stop("b1", "Senso-ji", "09:00", "10:30", geo=HK),
                _stop("b2", "Garden", "11:00", "12:30", geo=HK),
            ]),
        ],
        segments=[
            TripSegment(id="hk", destination=Destination(name="Hong Kong", geo=HK), dates=DateRange(start=MONDAY, end=MONDAY)),
            TripSegment(id="tyo", destination=Destination(name="Tokyo", geo=TOKYO), dates=DateRange(start=MONDAY, end=TUESDAY)),
        ],
        hotels=[HotelStay(
            id="h", name="Hotel Kest", check_in=MONDAY, check_out=TUESDAY,
            price_per_night=Money(amount=900, currency="HKD"), price_kind=PriceKind.LIVE,
        )],
        tickets=[TicketOption(id="t", mode="flight", name="Made Up Air", price=Money(amount=100, currency="HKD"), price_kind=PriceKind.REFERENCE)],
        cost=CostBreakdown(
            currency="HKD",
            lines=[CostLine(label="Hotel · Hotel Kest × 1 night", amount=900)],
            total=900, budget=1000, remaining=100, status="within",
        ),
    )
    context = TripContext(
        session_id="s", destination="Hong Kong",
        preferences=["must-see: Victoria Peak"],
        hard_constraints=["must visit Kiyomizu-dera"],
    )
    catalog = ReviewCatalog(
        places=frozenset({"ocean park", "temple"}),
        meals=frozenset({"noodle house"}),
        hotels=frozenset({"peninsula"}),
        tickets=frozenset({"cathay pacific"}),
        unpriced=frozenset({"hotel kest"}),
    )
    original_end = plan.days[0].items[2].end
    reviewed, findings = review_plan(plan, context, catalog)
    assert original_end == "16:00"
    assert set(_codes(findings)) >= {
        "unknown_name", "meal_long", "no_meal", "repeat_place", "missing_place", "no_source", "unsourced_price", "wrong_city",
    }
    lunch = next(item for item in reviewed.days[0].items if item.name == "Long Lunch")
    assert lunch.end == "14:00"
    assert reviewed.hotels[0].price_per_night is None
    assert reviewed.cost.total == 0
    assert reviewed.cost.status == "unknown"
    assert any(finding.message.startswith("Victoria Peak") for finding in findings)
    assert any("Kiyomizu-dera" in finding.message for finding in findings)
    assert any(note.startswith("Plan review change:") for note in reviewed.data_quality.notes)
    assert any(note.startswith("Plan review:") for note in reviewed.data_quality.notes)


def test_a_stated_length_and_a_return_visit_and_an_estimate_are_left_alone() -> None:
    plan = _plan([
        DayPlan(date=MONDAY, items=[_meal("l", "Long Lunch", "12:00", "15:00", tags=[STATED])]),
        DayPlan(date=TUESDAY, items=[_stop("b", "Senso-ji", "10:00", "11:30", geo=TOKYO)]),
    ])
    plan.days[0].items.append(_stop("a", "Senso-ji", "10:00", "11:30", geo=HK))
    plan.tickets = [TicketOption(id="drive", mode="drive", name="Drive to the city")]
    plan.hotels = [HotelStay(
        id="h", name="Hotel Kest", check_in=MONDAY, check_out=TUESDAY,
        price_per_night=Money(amount=50, currency="HKD"), price_kind=PriceKind.ESTIMATE,
        source_refs=[_ref("Hotel Kest")],
    )]
    context = TripContext(session_id="s", destination="Hong Kong", preferences=["go back to Senso-ji again"])
    _, findings = review_plan(plan, context, ReviewCatalog())
    assert findings == []


def test_catalog_names_come_from_the_search_results() -> None:
    results = {
        "attraction": AgentResult(agent=AgentName.ATTRACTION, status=AgentStatus.OK, payload={"places": [{"name": "Ocean Park"}]}),
        "hotel": AgentResult(agent=AgentName.HOTEL, status=AgentStatus.OK, payload={"hotels": [{"name": "Peninsula", "nightly_price": {"amount": 800}}]}),
        "ticket": AgentResult(agent=AgentName.TICKET, status=AgentStatus.OK, payload={"flights": [{"airline": "Cathay", "flight_number": "CX100"}]}),
    }
    catalog = catalog_from_results(results)
    assert "ocean park" in catalog.places
    assert "peninsula" in catalog.priced
    assert "cathay cx100" in catalog.tickets


def test_rule_passes_is_the_cap_and_rejects_a_number_past_ten(tmp_path) -> None:
    from app.infrastructure.store import SQLiteStore

    settings = Settings(_env_file=None)
    store = SQLiteStore(tmp_path / "policy.db")
    with pytest.raises(ValueError, match="rule_passes"):
        store.check_runtime(settings, {"rule_passes": "11"})
    store.save_runtime({"rule_passes": "0"})
    store.apply_runtime(settings)
    assert settings.refine_max_rounds == 0


def test_a_blank_reviewer_model_is_not_called_and_a_bad_answer_is_ignored() -> None:
    from app.application.plan_review import apply_reviewer, reviewer_llm

    settings = Settings(_env_file=None)
    blank = settings.model_copy(update={"reviewer_model": "", "reviewer_api_key": "", "poe_api_key": ""})
    assert settings.reviewer_model == "deepseek-v4-pro"
    assert reviewer_llm(blank) is None
    named = settings.model_copy(update={"reviewer_model": "gemini-3.5-flash-lite", "reviewer_api_key": "", "poe_api_key": ""})
    assert reviewer_llm(named) is None
    plan = _plan([DayPlan(date=MONDAY, items=[_stop("a", "Ocean Park", "09:00", "12:00")])])

    class Answer:
        def __init__(self, text: str) -> None:
            self.text = text
            self.model = "fake"
            self.prompt_tokens = 1
            self.completion_tokens = 1
            self.latency_ms = 1
            self.reasoning = ""

    class Model:
        name = "fake"
        available = True

        def __init__(self, text: str) -> None:
            self.text = text

        async def complete(self, **_kwargs: object) -> Answer:
            return Answer(self.text)

    kept = asyncio.run(apply_reviewer(plan, [ReviewFinding("missing_place", "Victoria Peak was named for this trip and is not on the plan.")], settings, Model("not json")))
    assert kept is None
    shorter = plan.model_copy(deep=True)
    shorter.days[0].items[0].end = "11:00"
    accepted = asyncio.run(apply_reviewer(
        plan,
        [ReviewFinding("missing_place", "Victoria Peak was named for this trip and is not on the plan.")],
        settings,
        Model(shorter.model_dump_json()),
    ))
    assert accepted is not None and accepted.days[0].items[0].end == "11:00"
    added = plan.model_copy(deep=True)
    added.days[0].items.append(_stop("b", "A new place", "13:00", "14:00"))
    refused = asyncio.run(apply_reviewer(
        plan,
        [ReviewFinding("missing_place", "Victoria Peak was named for this trip and is not on the plan.")],
        settings,
        Model(added.model_dump_json()),
    ))
    assert refused is None
