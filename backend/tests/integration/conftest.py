"""Fixtures for end-to-end scenario tests (all offline: mock model + mock providers)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date

import pytest
from pydantic import SecretStr

from backend.agents.orchestrator import OrchestratorOptions
from backend.agents.runtime.llm import LLMClient
from backend.container import Container, build_container
from backend.schemas.common import Money
from backend.schemas.trip import TripContext
from backend.settings import Settings
from backend.tools.providers.mock.faults import FaultPlan

CANARY = "PF-CANARY-test-0001"


async def no_sleep(_: float) -> None:
    """Retry backoff without waiting (delays are still computed and budgeted)."""


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "storage_backend": "memory",
        "call_log_jsonl": False,
        "canary_token": SecretStr(CANARY),
        "preplanning_time_budget_s": 10.0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def kyoto_context(**overrides: object) -> TripContext:
    data: dict[str, object] = {
        "destination": "Kyoto",
        "origin": "Tokyo",
        "start_date": date(2026, 4, 10),
        "end_date": date(2026, 4, 12),
        "party_size": 2,
        "budget": Money(amount=300_000, currency="JPY"),
    }
    data.update(overrides)
    return TripContext.model_validate(data)


ContainerFactory = Callable[..., Awaitable[Container]]


@pytest.fixture
def faults() -> FaultPlan:
    return FaultPlan()


@pytest.fixture
async def make_container(faults: FaultPlan) -> AsyncIterator[ContainerFactory]:
    built: list[Container] = []

    async def factory(
        *,
        settings: Settings | None = None,
        router_llm: LLMClient | None = None,
        agent_llm: LLMClient | None = None,
        options: OrchestratorOptions | None = None,
        fault_plan: FaultPlan | None = None,
    ) -> Container:
        container = build_container(
            settings or make_settings(),
            faults=fault_plan or faults,
            router_llm=router_llm,
            agent_llm=agent_llm,
            sinks=[],
            sleep=no_sleep,
            options=options,
        )
        await container.start()
        built.append(container)
        return container

    yield factory
    for container in built:
        await container.stop()


@pytest.fixture
async def container(make_container: ContainerFactory) -> Container:
    return await make_container()
