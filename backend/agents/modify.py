"""Modify path: re-run only the agents the change needs, preserve confirmed items, check, merge.

Proposal: "When users prompt for an edit, System 1 identifies the parts to be changed. Only the
specific agents responsible will be called." and "On the modify path, only the agent for the
failed or stale item is called again, and confirmed items are preserved."
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial

from backend.agents.focus import focus_summary, locked_note, scope_change, scoped_agents
from backend.agents.merge import agents_for_change, apply_change, merge_focused, merge_modify
from backend.agents.preplanning.base import PreplanningAgent
from backend.agents.prompts import (
    MODIFY_EXTRACT_FOCUS_INSTRUCTION,
    MODIFY_EXTRACT_FOCUS_SYSTEM,
    MODIFY_EXTRACT_INSTRUCTION,
    MODIFY_EXTRACT_SYSTEM,
    Purpose,
    data_message,
    system,
)
from backend.agents.route_order import RouteOrderer
from backend.agents.runtime.caller import ModelCallError, OutputRejected
from backend.agents.runtime.deps import RuntimeDeps
from backend.agents.runtime.parallel import AgentHook, run_with_budget
from backend.agents.runtime.structured import StructuredOutputError, complete_structured
from backend.schemas.agents import AgentResult, AgentTask, WeatherData
from backend.schemas.common import ALL_AGENTS, AgentName, PathName, SectionStatus
from backend.schemas.memory import SessionState
from backend.schemas.observability import TraceContext
from backend.schemas.routing import ChangeRequest, FocusKind, PlanFocus, RouterDecision
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import TripPlan
from backend.tools.staleness import sections_needing_refresh


def plan_summary(plan: TripPlan) -> str:
    """Ids and names the change extractor may refer to."""
    return json.dumps(
        {
            "start_date": plan.start_date.isoformat(),
            "end_date": plan.end_date.isoformat(),
            "party_size": plan.party_size,
            "budget": plan.budget.model_dump() if plan.budget else None,
            "hotel": {"hotel_id": plan.hotel.hotel.hotel_id, "name": plan.hotel.hotel.name} if plan.hotel else None,
            "stops": [
                {"place_id": i.place_id, "name": i.title, "date": d.date.isoformat(), "confirmed": i.confirmed}
                for d in plan.days
                for i in d.items
            ],
            "tickets": [{"ticket_id": t.ticket_id, "direction": t.direction} for t in plan.tickets],
        },
        ensure_ascii=False,
    )


@dataclass
class ModifyOutcome:
    plan: TripPlan
    context: TripContext
    change: ChangeRequest
    agents_run: list[AgentName]
    notes: list[str] = field(default_factory=list)


class ModifyPath:
    def __init__(
        self,
        deps: RuntimeDeps,
        agents: Mapping[AgentName, PreplanningAgent],
        orderer: RouteOrderer,
    ) -> None:
        self.deps = deps
        self.agents = agents
        self.orderer = orderer

    async def extract_change(
        self,
        state: SessionState,
        message: str,
        *,
        trace: TraceContext,
        path: PathName = PathName.MODIFY,
        focus: PlanFocus | None = None,
    ) -> ChangeRequest:
        assert state.plan is not None
        data = [("plan_summary", plan_summary(state.plan)), ("trip_context", state.context.model_dump_json())]
        if focus is not None:
            data.append(("focus", focus_summary(state.plan, focus)))
        data.append(("user_message", message))
        role, instruction = (
            (MODIFY_EXTRACT_FOCUS_SYSTEM, MODIFY_EXTRACT_FOCUS_INSTRUCTION)
            if focus is not None
            else (MODIFY_EXTRACT_SYSTEM, MODIFY_EXTRACT_INSTRUCTION)
        )
        messages = [system(role, self.deps.canary), data_message(instruction, data)]
        try:
            return await complete_structured(
                self.deps.agent_caller,
                purpose=Purpose.MODIFY_EXTRACT,
                messages=messages,
                schema=ChangeRequest,
                trace=trace,
                path=path,
                max_repairs=self.deps.settings.max_model_repairs,
            )
        except (ModelCallError, OutputRejected, StructuredOutputError):
            self.deps.observability.record_event(trace, "change_extract_failed", {})
            return ChangeRequest()

    def affected_agents(
        self, plan: TripPlan, decision: RouterDecision | None, change: ChangeRequest,
        old: TripContext, new: TripContext, focus: PlanFocus | None = None,
    ) -> list[AgentName]:
        if focus is not None:
            return scoped_agents(decision.affected_parts if decision else [], focus)
        wanted: set[AgentName] = set(decision.affected_parts if decision else [])
        wanted |= agents_for_change(change, old, new)
        wanted |= set(sections_needing_refresh(plan, self.deps.settings))
        return [a for a in ALL_AGENTS if a in wanted]

    async def run(
        self,
        state: SessionState,
        message: str,
        decision: RouterDecision | None,
        *,
        trace: TraceContext,
        on_start: AgentHook | None = None,
        on_finish: AgentHook | None = None,
        focus: PlanFocus | None = None,
    ) -> ModifyOutcome:
        """With a `focus`, only that part may change (design 4.1): a locked part is left alone
        with a note and no agent runs; otherwise the change is clamped to the part, only the
        part's agent runs, and merge_focused keeps everything else as it is."""
        plan = state.plan
        assert plan is not None
        if focus is not None:
            note = locked_note(plan, focus)
            if note is not None:
                return ModifyOutcome(plan=plan, context=state.context, change=ChangeRequest(), agents_run=[], notes=[note])
        change = await self.extract_change(state, message, trace=trace, focus=focus)
        if focus is not None:
            change = scope_change(change, plan, focus)
        new_context = apply_change(state.context, change)
        affected = self.affected_agents(plan, decision, change, state.context, new_context, focus=focus)

        indoor_dates = [d.date for d in plan.days if d.forecast and d.forecast.warning_signal]
        task_for = {
            agent: AgentTask(
                context=new_context,
                preferences=state.preferences,
                exclude_ids=self._exclusions(agent, plan, change, focus),
                indoor_only_dates=indoor_dates,
                notes=change.add_requests,
            )
            for agent in affected
        }
        budget = self.deps.settings.preplanning_time_budget_s
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        jobs = {
            agent: partial(self.agents[agent].run, task_for[agent], trace=trace, path=PathName.MODIFY, deadline=deadline)
            for agent in affected
        }
        results: dict[AgentName, AgentResult] = await run_with_budget(
            jobs, budget_s=budget, on_start=on_start, on_finish=on_finish
        )

        # Cascade: a new weather warning invalidates outdoor stops that day, so the attraction
        # agent runs once more (indoor places) if it was not already part of this edit.
        weather = results.get(AgentName.WEATHER)
        warned = (
            [f.date for f in weather.data.forecasts if f.warning_signal]
            if weather and weather.status is SectionStatus.OK and isinstance(weather.data, WeatherData)
            else []
        )
        if warned and AgentName.ATTRACTION not in results:
            remaining = max(0.0, deadline - loop.time())
            task = AgentTask(
                context=new_context,
                preferences=state.preferences,
                indoor_only_dates=warned,
                notes=change.add_requests,
            )
            cascade = await run_with_budget(
                {
                    AgentName.ATTRACTION: partial(
                        self.agents[AgentName.ATTRACTION].run,
                        task,
                        trace=trace,
                        path=PathName.MODIFY,
                        deadline=loop.time() + remaining,
                    )
                },
                budget_s=remaining,
                on_start=on_start,
                on_finish=on_finish,
            )
            results.update(cascade)

        if focus is not None:
            merged = merge_focused(
                plan, results, context=new_context, change=change, focus=focus, orderer=self.orderer
            )
        else:
            merged = merge_modify(plan, results, context=new_context, change=change, orderer=self.orderer)
        return ModifyOutcome(
            plan=merged.plan,
            context=new_context,
            change=change,
            agents_run=[a for a in ALL_AGENTS if a in results],
            notes=merged.notes,
        )

    @staticmethod
    def _exclusions(
        agent: AgentName, plan: TripPlan, change: ChangeRequest, focus: PlanFocus | None = None
    ) -> list[str]:
        if agent is AgentName.HOTEL and (change.replace_hotel or change.cheaper_hotel) and plan.hotel:
            return [plan.hotel.hotel.hotel_id]
        if agent is AgentName.ATTRACTION:
            return list(change.remove_place_ids)
        if agent is AgentName.TICKET and focus is not None and focus.kind is FocusKind.TICKET and change.replace_focus:
            return [focus.id]
        return []
