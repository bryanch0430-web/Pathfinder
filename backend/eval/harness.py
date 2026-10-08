"""Shared plumbing for the metric drivers: offline settings, one container per scenario, turn
recording, fault injection for the Hong Kong disruptions and simulated time passing.

Each scenario gets a FRESH container (fresh FaultPlan, in-memory observability, in-memory
stores), so scenarios cannot leak state into each other and replay identically.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import TracebackType

from pydantic import SecretStr

from backend.agents.orchestrator import OrchestratorOptions, TurnMode
from backend.agents.runtime.llm import LLMClient
from backend.container import Container, build_container
from backend.eval.judge import Judge, register_mock_judge
from backend.eval.models import DisruptionSpec, HKScenario
from backend.schemas.common import Money, SectionStatus
from backend.schemas.observability import ToolCallRecord
from backend.schemas.trip import ConstraintKind, TripContext
from backend.schemas.trip_plan import DayPlan, DisruptionKind, SectionState, TripPlan
from backend.schemas.turn import TurnResult
from backend.settings import Settings
from backend.tools.providers.mock.faults import FaultPlan

# Fixed so eval runs are reproducible; never used outside the harness.
EVAL_CANARY = "PF-CANARY-EVAL-5c1e7a93d2"


def eval_settings(**overrides: object) -> Settings:
    """Settings that keep every provider on its offline mock and write no log files."""
    values: dict[str, object] = {
        "router_provider": "mock",
        "agent_llm_provider": "mock",
        "judge_provider": "mock",
        "embedding_provider": "mock",
        "reranker_provider": "mock",
        "search_provider": "mock",
        "maps_providers": ["mock"],
        "weather_provider": "mock",
        "places_provider": "mock",
        "ticket_provider": "mock",
        "storage_backend": "memory",
        "call_log_jsonl": False,
        "langfuse_public_key": SecretStr(""),
        "langfuse_secret_key": SecretStr(""),
        "canary_token": SecretStr(EVAL_CANARY),
    }
    values.update(overrides)
    return Settings.model_validate(values)


async def _no_sleep(_seconds: float) -> None:
    """Retry backoff is not slept in eval runs (deterministic and fast)."""
    return None


@dataclass
class TurnRecord:
    session_id: str
    message: str
    mode: TurnMode
    result: TurnResult


@dataclass
class EvalRig:
    """One container plus the FaultPlan wired into its mock providers."""

    container: Container
    faults: FaultPlan
    turns: list[TurnRecord] = field(default_factory=list)

    async def __aenter__(self) -> EvalRig:
        await self.container.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.container.stop()

    @property
    def judge(self) -> Judge:
        return Judge(self.container.judge_llm, model=self.container.settings.judge_model)

    async def new_session(self, context: TripContext) -> str:
        state = await self.container.orchestrator.create_session(context)
        return state.session_id

    async def turn(self, session_id: str, message: str, *, mode: TurnMode = TurnMode.ROUTED) -> TurnResult:
        result = await self.container.orchestrator.handle_turn(session_id, message, mode=mode)
        self.turns.append(TurnRecord(session_id=session_id, message=message, mode=mode, result=result))
        return result

    def trace_ids(self, session_id: str) -> list[str]:
        return [t.result.trace_id for t in self.turns if t.session_id == session_id]

    def tool_log(self, trace_ids: Sequence[str]) -> list[ToolCallRecord]:
        obs = self.container.observability
        return [r for trace_id in trace_ids for r in obs.tool_calls(trace_id)]

    def session_tool_log(self, session_id: str) -> list[ToolCallRecord]:
        return self.tool_log(self.trace_ids(session_id))

    async def context_of(self, session_id: str) -> TripContext:
        return (await self.container.orchestrator.get_session(session_id)).context

    async def clone_session(self, session_id: str) -> str:
        """A second session holding exactly the same state (plan, context, history,
        preferences): the paired baseline for the edit-path metric."""
        orch = self.container.orchestrator
        source = await orch.get_session(session_id)
        target = await orch.create_session(source.context)
        copy = source.model_copy(deep=True, update={"session_id": target.session_id})
        await orch.sessions.save(copy)
        return target.session_id

    async def accept_plan(self, session_id: str) -> TripPlan | None:
        """Confirm every itinerary item, ticket and the hotel ("accept the plan")."""
        orch = self.container.orchestrator
        state = await orch.get_session(session_id)
        if state.plan is None:
            return None
        return await orch.confirm(
            session_id,
            item_ids=[i.item_id for i in state.plan.all_items()],
            ticket_ids=[t.ticket_id for t in state.plan.tickets],
            hotel=True if state.plan.hotel else None,
        )

    async def age_sections(self, session_id: str, hours: float) -> None:
        """Simulate `hours` passing: move every section's fetched_at into the past so the
        modify path's staleness rule (settings.stale_after_*) sees old data."""
        if hours <= 0:
            return
        orch = self.container.orchestrator
        state = await orch.get_session(session_id)
        if state.plan is None:
            return
        delta = timedelta(hours=hours)
        sections: list[SectionState] = [
            s.model_copy(update={"fetched_at": s.fetched_at - delta}) if s.fetched_at else s
            for s in state.plan.sections
        ]
        state.plan = state.plan.model_copy(update={"sections": sections})
        await orch.sessions.save(state)


def build_rig(
    settings: Settings | None = None,
    *,
    faults: FaultPlan | None = None,
    router_llm: LLMClient | None = None,
    agent_llm: LLMClient | None = None,
    judge_llm: LLMClient | None = None,
    options: OrchestratorOptions | None = None,
) -> EvalRig:
    plan = faults if faults is not None else FaultPlan()
    container = build_container(
        settings or eval_settings(),
        faults=plan,
        router_llm=router_llm,
        agent_llm=agent_llm,
        judge_llm=judge_llm,
        sinks=[],
        sleep=_no_sleep,
        options=options,
    )
    register_mock_judge(container.judge_llm)
    return EvalRig(container=container, faults=plan)


# ---- synthetic state ----------------------------------------------------------------------------

DEFAULT_START = date(2026, 11, 12)
CITY_CURRENCY = {"kyoto": "JPY", "tokyo": "JPY", "osaka": "JPY", "hong kong": "HKD"}
CITY_BUDGET = {"JPY": 200_000.0, "HKD": 18_000.0}


def complete_context(city: str, *, start: date = DEFAULT_START, days: int = 3, party: int = 2) -> TripContext:
    currency = CITY_CURRENCY.get(city.casefold(), "USD")
    return TripContext(
        destination=city,
        start_date=start,
        days=days,
        party_size=party,
        budget=Money(amount=CITY_BUDGET.get(currency, 2_000.0), currency=currency),
    )


def stub_plan(context: TripContext) -> TripPlan:
    """A minimal valid plan (dates only, no facts) so the router sees "a plan exists". Used by
    the routing metric, which scores System 1 alone, without running the planner."""
    start = context.start_date or DEFAULT_START
    end = context.end_date or start + timedelta(days=2)
    span = (end - start).days + 1
    return TripPlan(
        plan_id=f"stub-{uuid.uuid4().hex[:8]}",
        destination=context.destination or "Kyoto",
        start_date=start,
        end_date=end,
        party_size=context.party_size or 2,
        budget=context.budget,
        days=[DayPlan(date=start + timedelta(days=i)) for i in range(span)],
    )


# ---- Hong Kong disruptions ------------------------------------------------------------------------


@dataclass(frozen=True)
class InjectedDisruption:
    kind: DisruptionKind
    on: date
    target: str  # venue place_id, warning location, or delayed city
    target_ids: tuple[str, ...]  # plan ids the failure invalidates (place / item / ticket ids)


def _must_visit_names(context: TripContext) -> set[str]:
    return {h.value.casefold() for h in context.hard_constraints if h.kind is ConstraintKind.MUST_VISIT}


def inject_disruption(
    faults: FaultPlan, scenario: HKScenario, plan: TripPlan
) -> InjectedDisruption | None:
    """Mutate the scenario's FaultPlan so the follow-up turn sees the failure. Returns None when
    the plan offers nothing to disrupt (e.g. no venue scheduled that day)."""
    spec: DisruptionSpec = scenario.disruption
    on = plan.start_date + timedelta(days=spec.day_index)
    if on > plan.end_date:
        on = plan.end_date
    if spec.kind is DisruptionKind.WEATHER_WARNING:
        signal = spec.signal or "T8"
        faults.weather_warning(scenario.city, on, signal)
        day = plan.day(on)
        outdoor = [
            i.place_id
            for i in (day.items if day else [])
            if (p := plan.place(i.place_id)) is not None and p.indoor is False
        ]
        return InjectedDisruption(kind=spec.kind, on=on, target=scenario.city, target_ids=tuple(outdoor))
    if spec.kind is DisruptionKind.TRANSIT_DELAY:
        city = spec.city or scenario.city
        for mode in spec.modes:
            faults.delay_transit(city, on, spec.minutes or 45, mode)
        hit = tuple(t.ticket_id for t in plan.tickets if t.depart_at.date() == on)
        return InjectedDisruption(kind=spec.kind, on=on, target=city, target_ids=hit)
    day = plan.day(on)
    if spec.venue:
        venue = spec.venue
        place = next(
            (p for p in plan.places if venue in (p.place_id, p.name) or p.name.casefold() == venue.casefold()),
            None,
        )
        ids = (place.place_id,) if place else (venue,)
        faults.close_venue(venue, on)
        return InjectedDisruption(kind=spec.kind, on=on, target=venue, target_ids=ids)
    protected = _must_visit_names(scenario.context)
    for item in day.items if day else []:
        place = plan.place(item.place_id)
        if place is None or place.name.casefold() in protected:
            continue
        faults.close_venue(place.place_id, on)
        return InjectedDisruption(kind=spec.kind, on=on, target=place.place_id, target_ids=(place.place_id,))
    return None


def section_unavailable(plan: TripPlan | None, agent_value: str) -> bool:
    if plan is None:
        return True
    state = next((s for s in plan.sections if s.agent.value == agent_value), None)
    return state is None or state.status is SectionStatus.UNAVAILABLE
