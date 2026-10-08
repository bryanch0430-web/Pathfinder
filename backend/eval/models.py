"""Typed fixture records for the evaluation harness (scenario 21).

Every fixture line is validated against one of these models before it is used, so a typo in a
JSONL file fails loudly at load time instead of silently scoring a different scenario.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from backend.schemas.common import AgentName, Route, StrictModel
from backend.schemas.trip import ConstraintKind, TripContext
from backend.schemas.trip_plan import DisruptionKind


class Partition(StrEnum):
    """Proposal: "The custom sets are split 70/30 into development and held-out"."""

    DEV = "dev"
    HELDOUT = "heldout"


class SplitSelector(StrEnum):
    DEV = "dev"
    HELDOUT = "heldout"
    ALL = "all"


# ---- router labels (metric 5) ----------------------------------------------------------------


class RouterLabel(StrictModel):
    """One labelled utterance. `label` is the GOLD route after the clarification gate: a plan
    request whose context is incomplete is gold `unclear`, because the turn must ask."""

    id: str = Field(min_length=1)
    utterance: str = Field(min_length=1)
    label: Route
    has_plan: bool
    context_complete: bool
    city: str = Field(min_length=1)


# ---- injection probes (metric 7) ---------------------------------------------------------------


class ProbeCategory(StrEnum):
    ROUTE_OVERRIDE = "route_override"
    INSTRUCTION_LEAKAGE = "instruction_leakage"
    DISALLOWED_TOOL = "disallowed_tool"
    TASK_DROP = "task_drop"


class ProbeTarget(StrEnum):
    ROUTER = "router"
    AGENT = "agent"


class ProbeChannel(StrEnum):
    USER_MESSAGE = "user_message"  # the chat message itself
    TOOL_RESULT = "tool_result"  # FaultPlan.inject_search_text: arrives in web-search results
    CONTEXT_FIELD = "context_field"  # a FREE_TEXT hard constraint or hotel_style


class InjectionProbe(StrictModel):
    id: str = Field(min_length=1)
    category: ProbeCategory
    target: ProbeTarget
    channel: ProbeChannel
    text: str = Field(min_length=1, max_length=500)
    city: str = Field(min_length=1)
    agent: AgentName | None = Field(
        default=None, description="Agent-targeted probes: the agent whose section must stay on task"
    )
    field: Literal["hard_constraint", "hotel_style"] | None = Field(
        default=None, description="context_field channel: where the text is placed"
    )
    message: str | None = Field(
        default=None, description="Benign chat message used when the probe is not the message"
    )


# ---- Hong Kong disruption scenarios (metrics 2, 3, 1) -----------------------------------------


class DisruptionSpec(StrictModel):
    kind: DisruptionKind
    day_index: int = Field(ge=0, description="Trip day (0 = start date) the failure hits")
    signal: str | None = Field(default=None, description="weather_warning: e.g. 'T8'")
    minutes: int | None = Field(default=None, ge=1, description="transit_delay: delay in minutes")
    modes: list[Literal["train", "flight"]] = Field(
        default_factory=lambda: ["train", "flight"], description="transit_delay: modes delayed"
    )
    city: str | None = Field(default=None, description="transit_delay: city the legs touch")
    venue: str | None = Field(
        default=None,
        description="venue_closed: name or place_id; None = the first venue scheduled that day "
        "that is not a must-visit constraint",
    )


class HKScenario(StrictModel):
    id: str = Field(min_length=1)
    city: str = Field(min_length=1)
    constraint_type: ConstraintKind
    party: Literal["single", "group"]
    context: TripContext
    initial_message: str = Field(min_length=1)
    disruption: DisruptionSpec
    age_hours: float = Field(ge=0, description="Simulated time between the two turns")
    followup: str = Field(min_length=1)
    expected_agent: AgentName = Field(description="Agent expected to fetch the replacement")


# ---- Japan scenarios (metrics 1, 3, 4, 6) -------------------------------------------------------


class EditSpec(StrictModel):
    message: str = Field(min_length=1)
    expected_agents: list[AgentName] = Field(default_factory=list)
    removes: list[str] = Field(
        default_factory=list, description="Place names the edit removes (outside preservation)"
    )
    replaces: list[Literal["hotel", "tickets"]] = Field(
        default_factory=list, description="Accepted parts the edit is allowed to replace"
    )


class JapanScenario(StrictModel):
    id: str = Field(min_length=1)
    city: str = Field(min_length=1)
    constraint_type: ConstraintKind
    context: TripContext
    message: str = Field(min_length=1)
    edit: EditSpec | None = None


CustomScenario = HKScenario | JapanScenario


def scenario_message(scenario: CustomScenario) -> str:
    """The planning message of either custom scenario type."""
    return scenario.initial_message if isinstance(scenario, HKScenario) else scenario.message
