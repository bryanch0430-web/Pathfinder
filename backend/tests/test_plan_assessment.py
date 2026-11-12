"""The plan assessment and its feedback loop: confidence from the plan's own checks, further research
for what is unclear, and a question for what stays open.

No network and no real model: the web search is a fake source, the writer is ``MeteredLLM``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

from app.application.assessment import assess_and_improve, assess_plan, reassess
from app.application.choices import apply_choice
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.prompts import PromptLibrary
from app.application.refine import PlanIssue
from app.application.toolbox import Toolbox
from app.domain.models import (
    ConstraintCheck,
    ConstraintStatus,
    CostBreakdown,
    DataGap,
    DateRange,
    DayPlan,
    Destination,
    FailureKind,
    GeoPoint,
    HotelStay,
    ItemType,
    Money,
    PlanItem,
    PriceKind,
    SourceRef,
    StayMatch,
    TripContext,
    TripPlan,
)
from app.eval_metering import MeteredLLM
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.websearch import WebResult

START = date(2027, 4, 10)
WISH = "a room with two beds"


def _stay(index: int, verdict: str | None, **extra) -> HotelStay:
    match = (
        StayMatch(
            wishes=[WISH],
            verdict=verdict,
            score=60,
            evidence=["Twin Room"] if verdict == "meets" else [],
        )
        if verdict
        else None
    )
    return HotelStay(
        id=f"hotel_h{index}",
        name=f"Hotel {index}",
        geo=GeoPoint(lat=35.0, lng=135.76),
        check_in=START,
        check_out=START + timedelta(days=2),
        price_per_night=Money(amount=100 * index, currency="USD"),
        price_kind=PriceKind.REFERENCE,
        match=match,
        **extra,
    )


def _plan(*stays: HotelStay) -> TripPlan:
    days = [
        DayPlan(
            date=START + timedelta(days=offset),
            items=[
                PlanItem(
                    id=f"a{offset}",
                    type=ItemType.ATTRACTION,
                    name=f"Temple {offset}",
                    start="10:00",
                    end="12:00",
                    geo=GeoPoint(lat=35.0 + offset * 0.01, lng=135.77),
                    hours_status="stated",
                    source_refs=[
                        SourceRef(
                            title=f"Temple {offset}",
                            url="https://example.test/t",
                            source="osm",
                        )
                    ],
                )
            ],
        )
        for offset in range(3)
    ]
    return TripPlan(
        session_id="s1",
        destination=Destination(name="Kyoto", geo=GeoPoint(lat=35.01, lng=135.77)),
        dates=DateRange(start=START, end=START + timedelta(days=2)),
        party_size=2,
        days=days,
        hotels=[stays[0]] if stays else [],
        hotel_options=list(stays),
        cost=CostBreakdown(currency="USD", total=400, budget=1000, status="within"),
    )


def _context(*wishes: str) -> TripContext:
    return TripContext(
        session_id="s1",
        destination="Kyoto",
        party_size=2,
        currency="USD",
        budget=1000,
        preferences=list(wishes),
    )


class FakeSearch:
    name, available = "fake-search", True

    def __init__(self):
        self.queries: list[str] = []

    async def search(self, *, query, count=5, retries=0):
        self.queries.append(query)
        return [
            WebResult(
                title="Hotel 2 Kyoto rooms",
                url="https://rooms.example/hotel-2",
                snippet="Hotel 2 in Kyoto offers twin rooms with two beds.",
                authority=3,
            )
        ]


class Registry:
    def __init__(self, fx, search=None):
        self.fx = fx
        self.web = [search] if search else []
        self.web_deep = []
        self.hotels = []


def _improve(
    settings,
    container,
    plan,
    context,
    *,
    reply: dict | None,
    search=None,
    llm=None,
    rounds=None,
):
    llm = llm or MeteredLLM(replies={"stay.research": json.dumps(reply or {})})
    gateway = LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)
    planner = Planner(gateway, settings, container.registry.fx)
    if rounds is not None:
        settings = settings.model_copy(
            update={"assessment_max_research_rounds": rounds}
        )
    improved = asyncio.run(
        assess_and_improve(
            plan,
            context=context,
            issues=[],
            registry=Registry(container.registry.fx, search),
            toolbox=Toolbox(
                path="plan", tracer=NoopTracer(), store=None, request_id="r1"
            ),
            planner=planner,
            settings=settings,
        )
    )
    return improved, llm


def test_a_plan_with_nothing_open_is_high_confidence_and_asks_nothing():
    assessment = assess_plan(_plan(_stay(1, None)), _context("see temples"))
    assert (assessment.confidence, assessment.level, assessment.next_action) == (
        1.0,
        "high",
        "none",
    )
    assert assessment.signals == [] and assessment.question == ""


def test_each_open_check_lowers_the_confidence_and_is_listed():
    plan = _plan(_stay(1, None))
    plan.data_quality.gaps.append(
        DataGap(section="weather", kind=FailureKind.TIMEOUT, message="No forecast.")
    )
    plan.constraints = [
        ConstraintCheck(text="Vegetarian meals", status=ConstraintStatus.UNKNOWN),
        ConstraintCheck(text="No stairs", status=ConstraintStatus.FAIL),
    ]
    issues = [
        PlanIssue(
            code="closed",
            day=0,
            item_ids=("a0",),
            severity="error",
            message="Temple 0 is closed on Monday.",
        )
    ]
    assessment = assess_plan(plan, _context(), issues)
    assert {signal.code for signal in assessment.signals} == {
        "data_gap",
        "plan_check",
        "constraint_failed",
        "constraint_unchecked",
    }
    assert assessment.confidence == round(1 - 0.08 - 0.08 - 0.10 - 0.04, 2)
    assert "Temple 0 is closed on Monday." in assessment.unverified
    assert assessment.next_action == "ask" and "No stairs" in assessment.question


def test_an_unconfirmed_stay_wish_asks_the_traveller_when_no_research_is_possible():
    assessment = assess_plan(
        _plan(_stay(1, "unclear"), _stay(2, "unclear")), _context(WISH)
    )
    assert assessment.next_action == "ask"
    assert WISH in assessment.question and "Hotel 1" in assessment.question
    assert assessment.confidence == 0.88 and assessment.missing


def test_an_unconfirmed_stay_wish_is_researched_first_when_research_is_possible():
    plan = _plan(_stay(1, "unclear"))
    assert (
        assess_plan(plan, _context(WISH), can_research=True).next_action == "research"
    )
    assert (
        assess_plan(
            plan, _context(WISH), can_research=True, research_rounds=1
        ).next_action
        == "ask"
    )


def test_further_research_moves_the_plan_to_the_stay_a_reference_page_shows_fits(
    settings, container
):
    search = FakeSearch()
    plan = _plan(_stay(1, "unclear"), _stay(2, "unclear"))
    reply = {
        "stays": [
            {
                "id": "hotel_h2",
                "verdict": "meets",
                "score": 85,
                "reason": "The page lists twin rooms.",
                "evidence": ["Hotel 2 Kyoto rooms"],
            }
        ]
    }
    improved, llm = _improve(
        settings, container, plan, _context(WISH), reply=reply, search=search
    )
    assert len(search.queries) == 2 and WISH in search.queries[0]
    assert llm.purposes == ["stay.research"]
    assert improved.hotels[0].name == "Hotel 2"
    assert (
        improved.hotels[0].match.evidence_refs[0].url == "https://rooms.example/hotel-2"
    )
    assert improved.cost.total == 400  # two nights at the new stay's rate
    assessment = improved.assessment
    assert (assessment.next_action, assessment.research_rounds) == ("none", 1)
    assert any("Hotel 2 instead of Hotel 1" in told for told in assessment.improvements)
    assert any(note.startswith("Plan change: ") for note in improved.data_quality.notes)


def test_a_locked_stay_is_never_replaced_by_research(settings, container):
    plan = _plan(_stay(1, "unclear", locked=True), _stay(2, "meets"))
    improved, llm = _improve(
        settings, container, plan, _context(WISH), reply=None, search=FakeSearch()
    )
    assert improved.hotels[0].name == "Hotel 1" and llm.purposes == []
    assert improved.assessment.next_action == "ask"


def test_research_that_finds_nothing_leaves_a_question(settings, container):
    plan = _plan(_stay(1, "unclear"))
    improved, _ = _improve(
        settings,
        container,
        plan,
        _context(WISH),
        reply={"stays": []},
        search=FakeSearch(),
    )
    assert improved.hotels[0].name == "Hotel 1"
    assert (improved.assessment.next_action, improved.assessment.research_rounds) == (
        "ask",
        1,
    )


def test_without_a_model_the_plan_is_assessed_and_not_researched(settings, container):
    search = FakeSearch()
    improved, _ = _improve(
        settings,
        container,
        _plan(_stay(1, "unclear")),
        _context(WISH),
        reply=None,
        search=search,
        llm=NullLLM(),
    )
    assert search.queries == [] and improved.assessment.next_action == "ask"


def test_choosing_another_stay_assesses_the_plan_again(settings, container):
    plan = _plan(_stay(1, "unclear"), _stay(2, "meets"))
    plan.assessment = reassess(plan, _context(WISH), settings)
    assert plan.assessment.next_action == "ask"
    planner = Planner(
        LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings),
        settings,
        container.registry.fx,
    )
    chosen = asyncio.run(
        apply_choice(
            plan,
            kind="hotel",
            target_id=None,
            option_id="hotel_h2",
            planner=planner,
            context=_context(WISH),
        )
    )
    assert chosen.hotels[0].name == "Hotel 2"
    assert (
        chosen.assessment.next_action == "none"
        and chosen.assessment.confidence > plan.assessment.confidence
    )
