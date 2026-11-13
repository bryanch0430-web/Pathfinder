"""The checks, the cost and the assessment of a plan with several cities."""

from __future__ import annotations

from datetime import date

from plan_samples import make_plan, make_two_city_plan

from app.application.assessment import assess_plan
from app.application.planner import Planner
from app.application.validation import _route_errors
from app.config import Settings
from app.domain.models import TripContext
from app.infrastructure.providers.fx import FxService

CONTEXT = TripContext(session_id="s2", destination="Kyoto", currency="HKD")


def test_a_complete_route_has_no_route_errors() -> None:
    assert _route_errors(make_two_city_plan()) == []


def test_a_night_with_no_stay_names_the_city_and_the_date() -> None:
    plan = make_two_city_plan()
    plan.hotels = plan.hotels[:1]

    assert _route_errors(plan) == [
        "no stay for the night of 2026-12-05 in Osaka",
        "no stay for the night of 2026-12-06 in Osaka",
    ]


def test_a_missing_leg_and_two_chosen_options_are_errors() -> None:
    plan = make_two_city_plan()
    plan.legs = [leg for leg in plan.legs if leg.id != "leg_ab"]
    plan.tickets = [t for t in plan.tickets if t.leg_id != "leg_ab"] + [t for t in plan.tickets if t.id == "t_ab_bus"]
    assert "no transport leg from Kyoto to Osaka" in _route_errors(plan)

    twice = make_two_city_plan()
    for ticket in twice.options_for("leg_out"):
        ticket.selected = True
    assert _route_errors(twice) == ["transport from Hong Kong to Kyoto has 2 chosen options"]


def test_a_leg_with_no_options_is_allowed() -> None:
    plan = make_two_city_plan()
    plan.tickets = [t for t in plan.tickets if t.leg_id != "leg_ab"]

    assert _route_errors(plan) == []


def test_the_cost_of_two_cities_is_each_stay_for_its_nights_and_every_chosen_fare(settings: Settings) -> None:
    plan = make_two_city_plan()
    planner = Planner(None, settings, FxService())

    cost = planner.cost_for_plan(TripContext(session_id="s2", currency="HKD", budget=50000), plan)

    assert [line.label.split(" × ")[0] for line in cost.lines] == ["Hotel · Hotel Granvia", "Hotel · Osaka Inn"]
    assert cost.total == 4800 and cost.status == "within"
    assert any("Kyoto to Osaka is an estimate" in note for note in cost.notes)


def test_the_cost_of_one_city_is_priced_as_it_always_was(settings: Settings) -> None:
    plan = make_plan()
    planner = Planner(None, settings, FxService())

    assert planner.cost_for_plan(CONTEXT, plan).model_dump() == planner._cost(CONTEXT, plan.first_stay, plan.tickets, plan.dates).model_dump()


def test_the_assessment_names_the_city_and_the_leg_it_could_not_settle() -> None:
    plan = make_two_city_plan()
    plan.hotels = plan.hotels[:1]
    plan.tickets = [t for t in plan.tickets if t.leg_id != "leg_ab"]

    assessment = assess_plan(plan, CONTEXT, [])

    assert "transport from Kyoto to Osaka" in assessment.missing
    assert any(signal.message == "No stay was found in Osaka." for signal in assessment.signals)


def test_a_rough_ground_estimate_is_listed_as_unverified() -> None:
    plan = make_two_city_plan()
    for ticket in plan.options_for("leg_ab"):
        ticket.mode = "ground"

    assert any("Kyoto to Osaka is a rough estimate" in text for text in assess_plan(plan, CONTEXT, []).unverified)


def test_cities_with_nights_and_a_start_day_fix_the_length_of_the_trip() -> None:
    import asyncio

    from app.application.llm_gateway import LLMGateway
    from app.application.prompts import PromptLibrary
    from app.application.understanding import understand
    from app.config import Settings as S
    from app.infrastructure.llm import NullLLM
    from app.infrastructure.observability import NoopTracer

    settings = S(_env_file=None)
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)

    context = asyncio.run(
        understand(
            "Tokyo 4 nights then Kyoto 3 nights, starting 2027-03-01",
            TripContext(session_id="s", start_date=date(2027, 3, 1)),
            gateway,
        )
    )

    assert context.days == 8 and context.stops[0].city == "Tokyo"
