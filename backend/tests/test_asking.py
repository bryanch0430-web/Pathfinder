import asyncio
from datetime import date, timedelta

from app.application.asking import _cuisine_from_pages, _fallback_answer, _plan_facts
from app.application.trending import trending_report
from app.application.understanding import understand
from app.application.llm_gateway import LLMGateway
from app.application.modification import _add_attractions
from app.application.planning import PlanPath
from app.application.planner import Planner
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import Message, TripContext
from app.evaluation import SandboxRegistry
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService


def _plan(settings: Settings):
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    registry = SandboxRegistry(settings, "Kyoto")
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    planner = Planner(gateway, settings, FxService())
    plan_path = PlanPath(planner=planner, registry=registry, settings=settings)
    start = date.today() + timedelta(days=30)
    context = TripContext(
        session_id="s",
        destination="Kyoto",
        origin="Hong Kong",
        start_date=start,
        end_date=start + timedelta(days=2),
        days=3,
        party_size=2,
        budget=400000,
        currency="JPY",
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
    return settings, registry, gateway, planner, plan_path, context, plan


def test_month_mention_does_not_move_form_dates(settings: Settings) -> None:
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    context = TripContext(
        session_id="s",
        destination="Hong Kong",
        origin="Hong Kong",
        start_date=date(2026, 11, 10),
        end_date=date(2026, 11, 12),
        days=3,
        party_size=2,
        budget=9000,
        currency="HKD",
    )
    updated = asyncio.run(
        understand(
            "Plan a 3-day trip to Hong Kong for 2 people in November, budget 9,000 HKD.",
            context,
            gateway,
        )
    )
    assert updated.start_date == date(2026, 11, 10)
    assert updated.end_date == date(2026, 11, 12)
    assert updated.days == 3


def test_named_place_becomes_a_must_see_preference(settings: Settings) -> None:
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    updated = asyncio.run(
        understand(
            "Plan a 4-day Kyoto trip from Hong Kong for 2 in April. "
            "I want Fushimi Inari and a slow pace.",
            TripContext(session_id="s"),
            gateway,
        )
    )
    assert "slow pace" in updated.preferences
    assert "must-see: Fushimi Inari" in updated.preferences
    # A plain request without a named place adds no must-see.
    plain = asyncio.run(
        understand("Plan a 3-day trip to Kyoto in April.", TripContext(session_id="s"), gateway)
    )
    assert not [item for item in plain.preferences if item.startswith("must-see:")]


def test_destination_city_is_not_the_departure_city(settings: Settings) -> None:
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)

    def plan_from(message: str) -> TripContext:
        return asyncio.run(understand(message, TripContext(session_id="s"), gateway))

    kyoto = plan_from(
        "Plan a 4-day Kyoto trip from Hong Kong for 2 in April, budget 400,000 JPY. "
        "I want Fushimi Inari and a slow pace."
    )
    assert kyoto.destination == "Kyoto"
    assert kyoto.origin == "Hong Kong"
    assert kyoto.days == 4
    assert kyoto.currency == "JPY"
    assert kyoto.budget == 400000
    assert "slow pace" in kyoto.preferences

    shanghai = plan_from("Plan a 2-day trip to Shanghai from Hong Kong for 1 person.")
    assert shanghai.destination == "Shanghai"
    assert shanghai.origin == "Hong Kong"

    # The original behaviour still holds: destination then origin.
    hong_kong = plan_from("Plan a 3-day trip to Hong Kong from Singapore for 2 people.")
    assert hong_kong.destination == "Hong Kong"
    assert hong_kong.origin == "Singapore"

    # A bare departure city is not a destination.
    bare = plan_from("I am flying from Hong Kong next week.")
    assert bare.destination == ""
    assert bare.origin == "Hong Kong"


def test_new_destination_does_not_inherit_the_previous_trips_dates(settings: Settings) -> None:
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    context = TripContext(
        session_id="s",
        destination="Hong Kong",
        origin="Singapore",
        start_date=date(2027, 3, 1),
        end_date=date(2027, 3, 3),
        days=3,
        party_size=2,
        budget=9000,
        currency="HKD",
    )
    updated = asyncio.run(
        understand(
            "Plan a 4-day Kyoto trip from Hong Kong for 2 in April, budget 400,000 JPY.",
            context,
            gateway,
        )
    )
    assert updated.destination == "Kyoto"
    assert updated.start_date == date(2027, 4, 1)
    assert updated.end_date == date(2027, 4, 4)
    assert updated.days == 4

    # Without a month, the day count still reshapes the kept start date.
    no_month = TripContext(
        session_id="s",
        destination="Hong Kong",
        origin="Hong Kong",
        start_date=date(2027, 3, 1),
        end_date=date(2027, 3, 3),
        days=3,
        party_size=2,
    )
    reshaped = asyncio.run(
        understand("Plan a 4-day Tokyo trip from Hong Kong for 2 people.", no_month, gateway)
    )
    assert reshaped.destination == "Tokyo"
    assert reshaped.start_date == date(2027, 3, 1)
    assert reshaped.end_date == date(2027, 3, 4)


def test_ask_sentence_with_in_the_plan_does_not_replace_destination(settings: Settings) -> None:
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    context = TripContext(
        session_id="s",
        destination="Hong Kong",
        origin="Hong Kong",
        start_date=date(2026, 11, 10),
        end_date=date(2026, 11, 12),
        days=3,
        party_size=2,
        budget=9000,
        currency="HKD",
    )
    updated = asyncio.run(
        understand("What museums are in the plan? list them all out.", context, gateway)
    )
    assert updated.destination == "Hong Kong"
    assert updated.origin == "Hong Kong"


def test_day_two_weather_uses_the_second_plan_day(settings: Settings) -> None:
    _, _, _, _, _, _, plan = _plan(settings)
    facts = _plan_facts("What is the weather on day 2?", plan)
    assert "weather" in facts
    assert facts["weather"][0]["date"] == plan.days[1].date.isoformat()
    answer = _fallback_answer(facts, "plan")
    assert plan.days[1].date.isoformat() in answer


def test_listing_question_returns_items_not_a_generic_refusal(settings: Settings) -> None:
    _, _, _, _, _, _, plan = _plan(settings)
    facts = _plan_facts("List all meals in the plan.", plan)
    assert facts["matching_items"]
    assert all(row["type"] == "meal" for row in facts["matching_items"])
    answer = _fallback_answer(facts, "plan")
    assert any(row["name"] in answer for row in facts["matching_items"])


def test_museum_question_with_no_match_says_none(settings: Settings) -> None:
    _, _, _, _, _, _, plan = _plan(settings)
    facts = _plan_facts("What museums have been added so far?", plan)
    assert "matching_items" in facts
    assert facts["matching_items"] == []
    assert "No items" in _fallback_answer(facts, "plan")


def test_change_question_uses_recent_assistant_message(settings: Settings) -> None:
    _, _, _, _, _, _, plan = _plan(settings)
    history = [
        Message(session_id="s", role="user", content="Add a museum"),
        Message(
            session_id="s",
            role="assistant",
            content="Updated to version 2. Added Kyoto National Museum on Day 2 at 14:00. Everything else is unchanged.",
        ),
    ]
    facts = _plan_facts("What did you change?", plan, history)
    assert facts["recent_changes"]
    assert "Updated to version 2" in _fallback_answer(facts, "plan")


def test_trending_aggregates_only_public_plan_fields(settings: Settings) -> None:
    _, _, _, _, _, _, plan = _plan(settings)
    report = trending_report([plan, plan], "", 4)
    assert report["items"]
    first = report["items"][0]
    assert first["destination"] == "Kyoto"
    assert first["plans"] == 2
    assert first["days"] == 3
    assert "session_id" not in first
    assert "budget" not in first
    assert "party_size" not in first


def test_question_scoped_to_the_stay_uses_the_hotel_record(settings: Settings) -> None:
    _, _, _, _, _, _, plan = _plan(settings)
    assert plan.hotels, "the sandbox plan should include a stay"
    hotel = plan.hotels[0]
    facts = _plan_facts("Is breakfast included and what time is check-in?", plan, None, hotel.id)
    assert facts["hotel"]["id"] == hotel.id
    assert facts["scope"] == {"kind": "hotel", "name": hotel.name}
    # An item id still resolves the day item, not a stay.
    item_id = plan.days[0].items[0].id
    assert plan.find_hotel(item_id) is None
    item_facts = _plan_facts("Tell me about this stop", plan, None, item_id)
    assert item_facts["item"]["id"] == item_id
    assert item_facts.get("scope") is None


def test_add_attraction_appends_one_stop_without_renaming(settings: Settings) -> None:
    _, _, _, _, _, _, plan = _plan(settings)
    day = plan.days[1]
    before = [item.name for item in day.items]
    row = {
        "id": "extra-museum",
        "name": "Kyoto National Museum",
        "description": "A verified museum candidate.",
        "geo": {"lat": 34.99, "lng": 135.77},
        "address": "Kyoto",
        "source_refs": [
            {
                "title": "Test source",
                "url": "https://example.test/kyoto-national-museum",
                "source": "test",
                "fetched_at": "2026-10-30T00:00:00Z",
            }
        ],
    }
    added = _add_attractions(plan, [row], "Add one more attraction on day 2.")
    assert added == ["Kyoto National Museum"]
    assert len(day.items) == len(before) + 1
    assert {item.name for item in day.items} >= set(before) | {"Kyoto National Museum"}


def test_a_cuisine_question_is_not_answered_by_guessing() -> None:
    text = _cuisine_from_pages(
        "Café Bohème TKO is for eating japanese food??",
        [{"title": "Café Bohème", "summary": "Coffee and cake in Tseung Kwan O.", "snippet": "", "url": "https://example.test"}],
        "Café Bohème TKO",
    )
    assert "will not guess" in text
    assert "serves japanese" in text
