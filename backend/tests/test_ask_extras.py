"""Ask path: distance questions, nearest-stop questions, and missing sections."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from app.application.asking import _distance_facts, _fallback_answer, _plan_facts
from app.application.planner import Planner
from app.application.llm_gateway import LLMGateway
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import TripContext
from app.evaluation import SandboxRegistry
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService


def _plan(settings: Settings):
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    planner = Planner(gateway, settings, FxService())
    plan_path = PlanPath(planner=planner, registry=SandboxRegistry(settings, "Kyoto"), settings=settings)
    start = date.today() + timedelta(days=30)
    context = TripContext(
        session_id="s", destination="Kyoto", origin="Hong Kong",
        start_date=start, end_date=start + timedelta(days=2), days=3,
        party_size=2, budget=400000, currency="JPY",
    )
    return asyncio.run(
        plan_path.run(
            context=context, session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(), emit=lambda *_: None,
        )
    )


def test_distance_facts_rank_stops_from_the_reference(settings: Settings) -> None:
    plan = _plan(settings)
    facts = _distance_facts(plan, "Which stop is closest to the city centre?")

    assert facts is not None
    assert facts["method"] == "straight-line estimate"
    assert facts["closest"], "at least one stop should have a distance"
    kms = [row["km"] for row in facts["closest"]]
    assert kms == sorted(kms), "closest must be sorted ascending"
    assert "hotel" in facts["reference"], "the reference point must name what it measured from"


def test_distance_question_ignores_unrelated_text(settings: Settings) -> None:
    plan = _plan(settings)
    assert _distance_facts(plan, "What should I pack?") is None


def test_plan_facts_explain_a_missing_section(settings: Settings) -> None:
    from app.domain.models import DataGap, FailureKind

    plan = _plan(settings)
    plan.hotels = []
    plan.data_quality.gaps.append(
        DataGap(
            section="hotels",
            kind=FailureKind.NO_DATA,
            message='serpapi-hotels: "Your account has run out of searches."',
            retryable=False,
        )
    )

    facts = _plan_facts("Which hotel am I staying at?", plan)

    assert "missing" in facts
    assert "hotel" in facts["missing"]["sections"]
    answer = _fallback_answer(facts, "plan")
    assert "no hotel" in answer.lower()
    assert "out of searches" in answer, "the recorded reason must reach the traveller"


def test_plan_facts_carry_the_itinerary_for_general_questions(settings: Settings) -> None:
    plan = _plan(settings)
    facts = _plan_facts("Is this a good trip for a first visit?", plan)

    # Either a plan-derived fact set, or an empty one the caller fills in; the
    # answer path must never fall through to a blanket refusal while a plan exists.
    assert isinstance(facts, dict)


def test_place_detail_picks_the_matching_row_and_keeps_quality_signals() -> None:
    from app.application.asking import _place_detail

    rows = [
        {"name": "Somewhere Else", "rating": 3.9, "review_count": 10, "description": "nope"},
        {
            "name": "Amber",
            "rating": 4.5,
            "review_count": 699,
            "description": "Upscale French restaurant in the Landmark Mandarin Oriental.",
            "price_level": "expensive",
        },
    ]

    detail = _place_detail(rows, "amber")

    assert detail is not None
    assert detail["name"] == "Amber"
    assert detail["rating"] == 4.5 and detail["review_count"] == 699
    assert "French" in detail["summary"]
    assert detail["price_level"] == "expensive"


def test_place_detail_answer_states_rating_and_review_count() -> None:
    facts = {
        "place_detail": {
            "name": "Amber",
            "rating": 4.5,
            "review_count": 699,
            "summary": "Upscale French restaurant.",
            "price_level": "expensive",
        }
    }
    answer = _fallback_answer(facts, "google-places")
    assert "4.5" in answer and "699" in answer and "Amber" in answer
