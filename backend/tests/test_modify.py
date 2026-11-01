import asyncio
from datetime import date, timedelta

from app.application.llm_gateway import LLMGateway
from app.application.modification import ModifyPath
from app.application.prompts import PromptLibrary
from app.application.planning import PlanPath
from app.application.planner import Planner
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import TripContext
from app.evaluation import SandboxRegistry
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService


def test_modify_changes_only_the_hotel_and_preserves_stops(settings: Settings) -> None:
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
    modify = ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway)
    updated, diff = asyncio.run(
        modify.run(
            plan=plan,
            context=context,
            message="Swap the hotel for the other option, keep everything else.",
            route_parts=["hotel"],
            toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )

    assert updated.version == plan.version + 1
    assert updated.trip_id == plan.trip_id
    assert {item.name for day in updated.days for item in day.items} == {
        item.name for day in plan.days for item in day.items
    }
    assert updated.hotels[0].name != plan.hotels[0].name
    assert any(change["kind"] == "hotel" for change in diff)
    assert len([change for change in diff if change["kind"] in {"added", "removed"}]) == 0
