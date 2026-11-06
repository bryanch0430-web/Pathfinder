"""The budget is told honestly: an edit recomputes the budget gap, a hotel search that the budget empties keeps the
cheapest stays marked over budget, a budget change in the trip form re-runs the priced agents, and a budget word in a
concessive clause is not a budget request. The question asked before planning is in test_budget_check.py.

Offline: the sandbox providers and the metered writer, as the rest of the suite.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from typing import Any, Callable

import pytest

from app.application.modification import ModifyPath
from app.application.orchestrator import _describe_diff
from app.application.router import REPLAN_MESSAGE, affected_parts_for
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import DataGap, FailureKind, Money, TripContext
from app.evaluation import SandboxFlights, SandboxHotels, SandboxRegistry
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers import hotels as hotels_module
from app.infrastructure.providers.fx import FxService
from app.infrastructure.providers.hotels import SerpApiHotelSource
from tests.test_modify_dates import Trip, reply_of, run_trip
from tests.test_modify_targets import _build

OLD_BUDGET_GAP = (
    "The cheapest verified combination is HKD 5,118, which is 118 over the HKD 5,000 budget. "
    "The largest line is Hotel at HKD 3,000. A shorter stay, different dates, or a higher budget is needed."
)
NOTHING_MATCHED = "Nothing in the plan matched that request, so it is unchanged."


def _gap(section: str, message: str) -> DataGap:
    return DataGap(section=section, kind=FailureKind.NO_DATA, message=message, retryable=False)


class _Writer:
    """The edit writer, with the draft of the change it is told to return."""

    available = True

    def __init__(self, **draft: Any) -> None:
        self.draft = {"description": "", "affected_parts": [], "is_question": False, "confidence": 0.6, **draft}

    async def json(self, *_: Any, **__: Any) -> dict:
        return dict(self.draft)


class _CountingHotels(SandboxHotels):
    def __init__(self, destination: str = "Kyoto") -> None:
        super().__init__(destination)
        self.budgets: list[float | None] = []

    async def search(self, **kwargs: Any):
        self.budgets.append(kwargs.get("budget"))
        return await super().search(**kwargs)


class _CountingFlights(SandboxFlights):
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, **kwargs: Any):
        self.calls += 1
        return await super().search(**kwargs)


def _edit(
    settings: Settings,
    message: str,
    *,
    writer: Any,
    route_parts: list[str] | None = None,
    plan_hook: Callable[[Any], None] | None = None,
    context_hook: Callable[[TripContext], None] | None = None,
    registry_hook: Callable[[Any], None] | None = None,
):
    """One edit of the sandbox Kyoto plan, with the parts the real rule router names for the message."""
    _, planner, plan_path, context, plan = _build(settings)
    if plan_hook is not None:
        plan_hook(plan)
    if context_hook is not None:
        context_hook(context)
    if registry_hook is not None:
        registry_hook(plan_path.registry)
    modify = ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=writer)
    updated, diff = asyncio.run(
        modify.run(
            plan=plan,
            context=context,
            message=message,
            route_parts=affected_parts_for(message) if route_parts is None else route_parts,
            toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    return plan, updated, diff


# -- 1. the budget gap is recomputed on every edit, and a no-change reply tells only this turn's news ----------------


def _over_at_5000(plan) -> None:
    plan.budget = Money(amount=5000, currency="HKD")
    plan.data_quality.gaps.append(_gap("budget", OLD_BUDGET_GAP))


def _budget_10000(context: TripContext) -> None:
    context.budget, context.currency = 10000.0, "HKD"


@pytest.mark.parametrize("message", ["more grand", "add more stop"])
def test_a_raised_budget_and_a_pace_edit_leave_no_stale_budget_gap(settings: Settings, message: str) -> None:
    _, updated, _ = _edit(
        settings,
        message,
        writer=_Writer(affected_parts=["attraction"], new_values={"places_per_day": 4}),
        route_parts=["attraction"],
        plan_hook=_over_at_5000,
        context_hook=_budget_10000,
    )

    assert updated.cost.status == "within"
    assert not [gap for gap in updated.data_quality.gaps if gap.section == "budget"]
    assert "5,000" not in updated.change_summary
    assert not any("5,000" in gap.message for gap in updated.data_quality.gaps)


def test_a_plan_still_over_its_budget_gets_the_gap_with_todays_numbers(settings: Settings) -> None:
    def tight(context: TripContext) -> None:
        context.budget, context.currency = 1000.0, "HKD"

    _, updated, _ = _edit(
        settings,
        "more grand",
        writer=_Writer(affected_parts=["attraction"], new_values={"places_per_day": 4}),
        route_parts=["attraction"],
        plan_hook=_over_at_5000,
        context_hook=tight,
    )

    budget_gaps = [gap.message for gap in updated.data_quality.gaps if gap.section == "budget"]
    assert updated.cost.status == "over"
    assert len(budget_gaps) == 1 and "HKD 1,000 budget" in budget_gaps[0] and "5,000" not in budget_gaps[0]


def test_the_no_change_reply_never_repeats_an_old_gap(settings: Settings) -> None:
    old = "No hotel price for the new trip details came back; the earlier rate is kept."

    _, updated, diff = _edit(
        settings,
        "more grand",
        writer=_Writer(affected_parts=["attraction"], new_values={"places_per_day": 4}),
        route_parts=["attraction"],
        plan_hook=lambda plan: plan.data_quality.gaps.append(_gap("hotels", old)),
    )

    assert diff == []
    assert updated.change_summary == NOTHING_MATCHED


def test_the_turn_reply_after_a_raised_budget_does_not_repeat_the_old_gap(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        context = trip.store.get_context(trip.session_id)
        context.budget, context.currency = 10000.0, "HKD"
        trip.store.save_context(context)
        trip.rig.llm.route = {"intent": "modify", "confidence": 0.95, "affected_parts": ["attraction"]}
        trip.rig.llm.replies["modify"] = json.dumps(
            {"affected_parts": ["attraction"], "new_values": {"places_per_day": 4}, "is_question": False, "confidence": 0.3}
        )

        events = await trip.turn("more grand")

        # The budget's own change ("from HKD 5,000 to HKD 10,000") may be told; the old gap may not.
        assert "over the HKD 5,000 budget" not in reply_of(events)
        latest = trip.store.get_plan(trip.session_id)
        assert not any("5,000" in gap.message for gap in latest.data_quality.gaps)

    run_trip(settings, scenario, prepare=_over_at_5000)


def test_a_repeated_edit_that_still_cannot_be_done_says_why_again(settings: Settings) -> None:
    no_cheaper = "No cheaper verified hotel was returned; the current hotel stays."

    _, updated, diff = _edit(
        settings,
        "make it cheaper",
        writer=_Writer(affected_parts=["budget"]),
        route_parts=["budget"],
        plan_hook=lambda plan: plan.data_quality.gaps.append(_gap("hotels", no_cheaper)),
        registry_hook=lambda registry: setattr(registry, "hotels", []),
    )

    assert diff == []
    assert updated.change_summary == no_cheaper



# -- 2. a hotel search the budget empties keeps the cheapest stays, marked over budget -----------------------------

KYOTO = (35.0116, 135.7681)


def _row(name: str, price: float, rating: float) -> dict:
    return {
        "name": name,
        "neighborhood": "Central",
        "gps_coordinates": {"latitude": KYOTO[0] + 0.01, "longitude": KYOTO[1] + 0.01},
        "rate_per_night": {"extracted_lowest": price},
        "overall_rating": rating,
        "reviews": 100,
        "link": f"https://example.com/{name.replace(' ', '-')}",
    }


ROWS = [
    _row("Hotel Nine Hundred", 900, 4.8),
    _row("Hotel Seven Hundred", 700, 4.1),
    _row("Hotel Six Fifty", 650, 3.9),
    _row("Hotel Eight Hundred", 800, 4.5),
    _row("Hotel Twelve Hundred", 1200, 4.9),
]


NO_PRICE = {**_row("No Price Inn", 0, 4.0), "rate_per_night": {}}


@pytest.fixture()
def serp_rows() -> list[dict]:
    return ROWS


@pytest.fixture()
def serp(settings: Settings, monkeypatch: pytest.MonkeyPatch, serp_rows: list[dict]) -> SerpApiHotelSource:
    async def fake_request_json(*_: Any, **__: Any) -> dict:
        return {"properties": [dict(row) for row in serp_rows]}

    monkeypatch.setattr(hotels_module, "request_json", fake_request_json)
    return SerpApiHotelSource(settings.model_copy(update={"serpapi_key": "test-key"}))


def _search(source: SerpApiHotelSource, budget: float, nights: int = 12):
    start = date.today() + timedelta(days=30)
    return asyncio.run(
        source.search(
            destination="Kyoto",
            check_in=start,
            check_out=start + timedelta(days=nights),
            party_size=2,
            budget=budget,
            currency="HKD",
        )
    )


def test_a_search_the_budget_empties_keeps_the_three_cheapest_marked_over_budget(serp: SerpApiHotelSource) -> None:
    found = _search(serp, 5000)  # cap: 5000 * 0.45 / 12 * 1.15, about HKD 216 a night

    assert [hotel.nightly_price.amount for hotel in found] == [650, 700, 800]
    assert all("over the trip budget" in hotel.notes.lower() for hotel in found)


def test_rows_within_the_cap_are_kept_as_before(serp: SerpApiHotelSource) -> None:
    within = _search(serp, 100000)
    assert [hotel.name for hotel in within] == [
        "Hotel Twelve Hundred",
        "Hotel Nine Hundred",
        "Hotel Eight Hundred",
        "Hotel Seven Hundred",
        "Hotel Six Fifty",
    ]
    assert all(hotel.notes == "Indicative nightly rate; availability is not confirmed." for hotel in within)
    # Some within the cap (12 nights, HKD 20,000: about HKD 862 a night): only those, as before.
    some = _search(serp, 20000)
    assert sorted(hotel.nightly_price.amount for hotel in some) == [650, 700, 800]
    assert all("over the trip budget" not in hotel.notes.lower() for hotel in some)


def test_a_plan_on_a_budget_that_empties_the_search_still_gets_a_hotel_and_says_over(
    settings: Settings, serp: SerpApiHotelSource
) -> None:
    _assert_a_priced_over_budget_stay(settings, serp)


@pytest.mark.parametrize("serp_rows", [[*ROWS, NO_PRICE]])
def test_an_unpriced_row_does_not_hide_the_cheapest_priced_ones(serp: SerpApiHotelSource) -> None:
    found = _search(serp, 5000)

    priced = [hotel for hotel in found if hotel.nightly_price is not None]
    assert [hotel.nightly_price.amount for hotel in priced] == [650, 700, 800]
    assert all("over the trip budget" in hotel.notes.lower() for hotel in priced)
    unpriced = [hotel for hotel in found if hotel.nightly_price is None]
    assert [hotel.name for hotel in unpriced] == ["No Price Inn"]
    assert "over the trip budget" not in unpriced[0].notes.lower()


@pytest.mark.parametrize("serp_rows", [[*ROWS, NO_PRICE]])
def test_an_unpriced_row_beside_a_priced_one_within_the_cap_is_kept_as_before(serp: SerpApiHotelSource) -> None:
    some = _search(serp, 20000)  # about HKD 862 a night: 650, 700 and 800 are within

    assert [hotel.name for hotel in some] == ["Hotel Eight Hundred", "Hotel Seven Hundred", "No Price Inn", "Hotel Six Fifty"]
    assert all(hotel.notes == "Indicative nightly rate; availability is not confirmed." for hotel in some)


@pytest.mark.parametrize("serp_rows", [[*ROWS, NO_PRICE]])
def test_a_plan_whose_search_has_an_unpriced_row_still_gets_a_priced_stay(
    settings: Settings, serp: SerpApiHotelSource
) -> None:
    _assert_a_priced_over_budget_stay(settings, serp)


def _assert_a_priced_over_budget_stay(settings: Settings, serp: SerpApiHotelSource) -> None:
    from app.application.llm_gateway import LLMGateway
    from app.application.planner import Planner
    from app.application.planning import PlanPath
    from app.application.prompts import PromptLibrary
    from app.infrastructure.llm import NullLLM

    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    registry = SandboxRegistry(settings, "Kyoto")
    registry.hotels = [serp]
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    plan_path = PlanPath(planner=Planner(gateway, settings, FxService()), registry=registry, settings=settings)
    start = date.today() + timedelta(days=30)
    context = TripContext(
        session_id="s", destination="Kyoto", origin="Hong Kong", start_date=start,
        end_date=start + timedelta(days=12), days=13, party_size=2, budget=5000, currency="HKD",
    )

    plan = asyncio.run(
        plan_path.run(
            context=context,
            session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )

    assert plan.hotels and plan.hotels[0].price_per_night.amount in {650, 700, 800}
    assert plan.cost.status == "over"
    assert not any("none state a nightly rate" in gap.message for gap in plan.data_quality.gaps)



# -- 3. a budget change in the trip form re-runs the hotel and the fares ------------------------------------------


def _fill(trip: Trip, **fields: Any) -> None:
    context = trip.store.get_context(trip.session_id)
    for name, value in fields.items():
        setattr(context, name, value)
    trip.store.save_context(context)


def test_a_form_budget_change_and_replan_asks_the_hotel_and_flight_sources_again(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        hotels, flights = _CountingHotels(), _CountingFlights()
        trip.rig.plan_path.registry.hotels = [hotels]
        trip.rig.plan_path.registry.flights = [flights]
        _fill(trip, budget=10000.0)

        events = await trip.turn(REPLAN_MESSAGE)

        assert not [event for event in events if event.event == "error"]
        assert hotels.budgets == [10000.0]
        assert flights.calls == 1
        latest = trip.store.get_plan(trip.session_id)
        assert latest.budget.amount == 10000.0 and latest.version == 2

    run_trip(settings, scenario)


def test_a_form_budget_change_gives_a_plan_without_a_hotel_its_hotel(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        _fill(trip, budget=10000.0)

        events = await trip.turn(REPLAN_MESSAGE)

        latest = trip.store.get_plan(trip.session_id)
        assert latest.hotels, reply_of(events)
        assert "new dates" not in reply_of(events)

    run_trip(settings, scenario, prepare=lambda plan: setattr(plan, "hotels", []))



# -- 4. a budget word in a concessive clause is not a budget request ---------------------------------------------

CONCESSIVE = [
    "add the place i live even it will out budget",
    "add a museum even if it is over budget",
    "add the night market even though it goes over the budget",
    "add a tea ceremony no matter the budget",
    "add the onsen whatever it costs",
]


@pytest.mark.parametrize("message", CONCESSIVE)
def test_a_concessive_budget_clause_names_no_budget_part(message: str) -> None:
    assert "budget" not in affected_parts_for(message)


@pytest.mark.parametrize("message", ["make it cheaper", "we are over budget, cut the cost", "lower the budget to 2000"])
def test_a_real_budget_request_still_names_the_budget(message: str) -> None:
    assert "budget" in affected_parts_for(message)


def test_add_the_place_i_live_even_over_budget_does_not_rerun_hotels_and_flights(settings: Settings) -> None:
    hotels, flights = _CountingHotels(), _CountingFlights()

    def sources(registry: Any) -> None:
        registry.hotels, registry.flights = [hotels], [flights]

    _edit(
        settings,
        "add the place i live even it will out budget",
        writer=_Writer(affected_parts=["attraction"], confidence=0.3),
        registry_hook=sources,
    )

    assert (hotels.budgets, flights.calls) == ([], 0)


@pytest.mark.parametrize("message", CONCESSIVE)
def test_no_concessive_budget_clause_reruns_hotels_and_flights_in_the_edit(settings: Settings, message: str) -> None:
    hotels, flights = _CountingHotels(), _CountingFlights()

    def sources(registry: Any) -> None:
        registry.hotels, registry.flights = [hotels], [flights]

    _edit(settings, message, writer=_Writer(affected_parts=["attraction"], confidence=0.3), registry_hook=sources)

    assert (hotels.budgets, flights.calls) == ([], 0)



# -- 5. "for the new dates" only when the dates changed --------------------------------------------------------


def test_no_hotel_for_a_budget_edit_does_not_speak_of_new_dates(settings: Settings) -> None:
    _, updated, _ = _edit(
        settings,
        "make it cheaper",
        writer=_Writer(affected_parts=["budget"]),
        route_parts=["budget"],
        plan_hook=lambda plan: setattr(plan, "hotels", []),
        registry_hook=lambda registry: setattr(registry, "hotels", []),
    )

    hotel_gaps = [gap.message for gap in updated.data_quality.gaps if gap.section == "hotels"]
    assert hotel_gaps and not any("new dates" in text for text in hotel_gaps)
    assert any("for this budget" in text for text in hotel_gaps)


def test_the_diff_reply_says_new_dates_only_when_the_dates_moved(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    removed_hotel = [{"kind": "hotel", "from": "Old Inn", "to": None}]
    removed_flight = [{"kind": "flight", "from": "PF100", "to": None}]
    moved = [{"kind": "dates", "from": "a", "to": "b"}]

    assert "new dates" not in _describe_diff(removed_hotel, plan)
    assert "new dates" not in _describe_diff(removed_flight, plan)
    assert "new dates" in _describe_diff(moved + removed_hotel, plan)
    assert "new dates" in _describe_diff(moved + removed_flight, plan)
