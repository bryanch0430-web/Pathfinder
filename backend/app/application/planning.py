"""The new-trip path: geocode, four agents in parallel, grounded planner."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any, Callable

from app.application.agents import (
    AgentContext,
    AttractionAgent,
    HotelAgent,
    TicketAgent,
    WeatherAgent,
)
from app.application.hk_disruptions import apply_hong_kong_disruptions
from app.application.planner import Planner
from app.application.toolbox import Toolbox
from app.application.validation import repair_plan, tidy_data_gaps, validate_plan
from app.config import Settings
from app.domain.errors import PlanInvalidError, ProviderError
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    DataGap,
    FailureKind,
    GeoPoint,
    TripContext,
    TripPlan,
)
from app.domain.ports import Tracer

AGENT_FACTORIES = {
    AgentName.ATTRACTION: AttractionAgent,
    AgentName.HOTEL: HotelAgent,
    AgentName.WEATHER: WeatherAgent,
    AgentName.TICKET: TicketAgent,
}

#: The plan section each agent fills, as the planner names it in a data gap.
AGENT_SECTIONS = {
    AgentName.ATTRACTION: "attractions",
    AgentName.HOTEL: "hotels",
    AgentName.WEATHER: "weather",
    AgentName.TICKET: "tickets",
}

#: The reason an agent still running at the end of the pre-planning budget is
#: cancelled with; the tool call it was waiting on is logged as timed out.
BUDGET_EXCEEDED = "pre-planning time budget exceeded"

#: How long a cancelled agent gets to stop before the plan goes ahead without it.
CANCEL_GRACE_SECONDS = 2.0

logger = logging.getLogger(__name__)


class PlanPath:
    def __init__(self, *, planner: Planner, registry: Any, settings: Settings):
        self.planner = planner
        self.registry = registry
        self.settings = settings

    async def run(
        self,
        *,
        context: TripContext,
        session_id: str,
        toolbox: Toolbox,
        tracer: Tracer,
        emit: Callable[[str, dict[str, Any]], None],
        memory_summaries: list[str] | None = None,
        version: int = 1,
        trip_id: str | None = None,
        change_summary: str = "",
    ) -> TripPlan:
        fx = getattr(self.registry, "fx", None)
        if fx is not None:
            try:
                with tracer.span("fx.refresh"):
                    await toolbox.call("fx.rates", fx.refresh)
            except Exception:
                pass

        date_range = context.date_range()
        if not date_range:
            raise PlanInvalidError("The trip dates are incomplete.")
        if len(date_range.dates) > self.settings.max_days:
            raise PlanInvalidError(f"Trips longer than {self.settings.max_days} days are out of scope.")

        with tracer.span("geocode") as span:
            try:
                destination = await toolbox.call(
                    "geocode",
                    self.registry.geocoder.geocode,
                    name=context.destination,
                )
            except ProviderError as exc:
                raise PlanInvalidError(
                    f"I could not locate {context.destination!r} with the configured map sources. "
                    "Try the city and country together."
                ) from exc
            if not destination:
                raise PlanInvalidError(
                    f"I could not locate {context.destination!r}. Try the city and country together."
                )
            span.attrs["destination"] = destination.name

        timezone_resolver = getattr(self.registry, "timezone", None)
        if (
            self.settings.enable_timezone
            and timezone_resolver is not None
            and not destination.timezone
        ):
            try:
                zone = await toolbox.call(
                    "timezone.resolve", timezone_resolver.resolve, geo=destination.geo
                )
                if zone:
                    destination = destination.model_copy(update={"timezone": zone})
            except Exception:
                pass

        emit("progress", {"phase": "agents", "message": "Four agents are gathering live data.", "progress": 15})
        agent_results = await self.run_agents(
            context=context,
            destination=destination,
            date_range=date_range,
            toolbox=toolbox,
            emit=emit,
        )

        emit("progress", {"phase": "planner", "message": "Composing the itinerary from verified facts.", "progress": 70})
        plan = await self.planner.compose(
            session_id=session_id,
            context=context,
            destination=destination,
            date_range=date_range,
            results=agent_results,
            memory_summaries=memory_summaries or [],
            version=version,
            trip_id=trip_id,
            change_summary=change_summary,
            toolbox=toolbox,
            transfers=getattr(self.registry, "transfers", None),
        )
        # Hong Kong only: HKO warnings and MTR line status (Proposal TC-03).
        plan = await apply_hong_kong_disruptions(
            plan, registry=self.registry, toolbox=toolbox, settings=self.settings, results=agent_results, context=context
        )
        errors = validate_plan(plan, self.settings)
        if errors:
            emit("progress", {"phase": "repair", "message": "Repairing the itinerary.", "progress": 85})
            plan = await repair_plan(plan, errors, self.planner.gateway, self.settings)
        plan = tidy_data_gaps(plan)
        elevation_source = getattr(self.registry, "elevation", None)
        if self.settings.enable_elevation and elevation_source is not None:
            for day in plan.days:
                points = [
                    GeoPoint(lat=item.geo.lat, lng=item.geo.lng)
                    for item in day.items
                    if item.geo is not None and item.type.value != "meal"
                ]
                if len(points) < 2:
                    continue
                try:
                    gain = await toolbox.call(
                        "elevation.gain", elevation_source.gain, points=points
                    )
                except Exception:
                    continue
                if gain is not None:
                    day.elevation_gain_m = gain
        emit("progress", {"phase": "ready", "message": "Itinerary ready.", "progress": 100})
        return plan

    async def run_agents(
        self,
        *,
        context: TripContext,
        destination: Any,
        date_range: Any,
        toolbox: Toolbox,
        emit: Callable[[str, dict[str, Any]], None],
        names: set[AgentName] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, AgentResult]:
        request = AgentContext(
            context=context,
            destination=destination,
            date_range=date_range,
            request_id=toolbox.request_id,
            toolbox=toolbox,
            settings=self.settings,
            emit=emit,
            extra=extra or {},
        )
        selected = names or set(AGENT_FACTORIES)
        tasks: dict[asyncio.Task, AgentName] = {}
        agents: dict[AgentName, Any] = {}
        progress: dict[AgentName, list[dict[str, Any]]] = {}
        for agent_name in selected:
            factory = AGENT_FACTORIES[agent_name]
            agent = factory(self.registry, self.settings)
            agents[agent_name] = agent
            progress[agent_name] = []
            emit("agent", {"agent": agent_name.value, "phase": "start"})
            tasks[asyncio.create_task(agent.run(replace(request, progress=progress[agent_name])))] = agent_name

        results: dict[str, AgentResult] = {}
        started = time.perf_counter()
        try:
            done, pending = await asyncio.wait(
                tasks.keys(),
                timeout=self.settings.preplan_budget_seconds,
                return_when=asyncio.ALL_COMPLETED,
            )
        except asyncio.CancelledError:
            # The turn was abandoned (deadline or disconnect): stop the agents too.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for task in pending:
            task.cancel(BUDGET_EXCEEDED)
        if pending:
            # An agent that does not stop when cancelled must not hold the plan
            # past the budget: after a short grace period the plan goes ahead.
            _, stuck = await asyncio.wait(pending, timeout=CANCEL_GRACE_SECONDS)
            for task in stuck:
                logger.warning("%s agent did not stop when the pre-planning budget ran out", tasks[task].value)
                task.add_done_callback(_discard_outcome)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        for task, agent_name in tasks.items():
            if task in pending:
                # An agent that can say what it had found keeps that; the others are unavailable.
                keep = getattr(agents[agent_name], "partial_result", None)
                result = keep(progress[agent_name], centre=destination.geo, elapsed_ms=elapsed_ms) if keep else None
                if result is None:
                    result = _unavailable(
                        agent_name, "agent exceeded the pre-planning time budget", elapsed_ms=elapsed_ms
                    )
            else:
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    result = _unavailable(agent_name, "agent was cancelled")
                except Exception as exc:
                    # The exception's own text stays in the trace; the gap says only that it failed.
                    with toolbox.tracer.span(
                        f"agent.{agent_name.value}", status="error", error_class=type(exc).__name__, error=str(exc)[:500]
                    ):
                        pass
                    result = _unavailable(
                        agent_name, "agent failed unexpectedly", kind=FailureKind.SERVER_ERROR
                    )
            results[agent_name.value] = result
            emit(
                "agent",
                {
                    "agent": agent_name.value,
                    "phase": "done",
                    "status": result.status.value,
                    "elapsed_ms": result.elapsed_ms,
                    "summary": result.notes[0] if result.notes else "",
                    "failures": len(result.failures),
                },
            )
        # A mutable payload needs a stable copy: the planner only reads.
        for result in results.values():
            places = result.payload.get("places")
            if isinstance(places, list):
                result.payload["places"] = [
                    row for row in places if row.get("source_refs") and row.get("geo")
                ]
            meals = result.payload.get("meals")
            if isinstance(meals, list):
                result.payload["meals"] = [row for row in meals if row.get("geo")]
        return results


def _unavailable(
    agent_name: AgentName, message: str, *, kind: FailureKind = FailureKind.TIMEOUT, elapsed_ms: int = 0
) -> AgentResult:
    # The gap names the plan section, so the planner does not add a second one.
    section = AGENT_SECTIONS.get(agent_name, agent_name.value)
    return AgentResult(
        agent=agent_name,
        status=AgentStatus.UNAVAILABLE,
        payload={},
        notes=[message],
        failures=[DataGap(section=section, kind=kind, message=message, retryable=True)],
        elapsed_ms=elapsed_ms,
    )


def _discard_outcome(task: asyncio.Task) -> None:
    """Mark a late agent's outcome as seen; the plan was returned without it."""
    if not task.cancelled():
        task.exception()
