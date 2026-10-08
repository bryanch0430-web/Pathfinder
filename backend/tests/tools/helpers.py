"""Test doubles for the tools layer.

Observability and SecurityAudit are implemented by another worker; these tests use small local
fakes with the same method shapes so the tools layer is verified in isolation.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date

from backend.schemas.common import AgentName, PathName
from backend.schemas.observability import ToolCallRecord, TraceContext
from backend.schemas.security import InjectionCategory, SecurityEventKind
from backend.schemas.tools import ForecastDay, ForecastRequest
from backend.settings import Settings
from backend.tools.allowlist import ToolAllowlist
from backend.tools.gateway import ScopedToolGateway, ToolGateway
from backend.tools.providers.base import MapsProvider, WeatherProvider
from backend.tools.providers.mock import FaultPlan, MockProviders, build_mock_providers
from backend.tools.registry import ToolRegistry

TRACE = TraceContext(trace_id="trace-tools", session_id="session-tools", name="tools-test")
TRAVEL_DAY = date(2026, 4, 12)


class FakeObservability:
    def __init__(self) -> None:
        self.records: list[ToolCallRecord] = []

    def record_tool_call(self, record: ToolCallRecord) -> None:
        self.records.append(record)


AuditEvent = tuple[SecurityEventKind, InjectionCategory | None, PathName | None]


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(
        self,
        kind: SecurityEventKind,
        *,
        category: InjectionCategory | None = None,
        path: PathName | None = None,
    ) -> None:
        self.events.append((kind, category, path))


class FakeSleep:
    """Records requested delays and returns immediately."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class ScriptedWeather:
    """Weather provider that raises the scripted exceptions in order, then (or, with `always`,
    never) succeeds. Counts calls."""

    name = "scripted-weather"

    def __init__(self, *script: Exception | None, always: Exception | None = None) -> None:
        self.script = list(script)
        self.always = always
        self.calls = 0

    async def forecast(self, request: ForecastRequest) -> list[ForecastDay]:
        self.calls += 1
        if self.script:
            step = self.script.pop(0)
            if step is not None:
                raise step
        elif self.always is not None:
            raise self.always
        return [
            ForecastDay(
                date=request.start_date,
                summary="Sunny",
                temp_min_c=10.0,
                temp_max_c=20.0,
                precipitation_chance=0.1,
            )
        ]


def make_settings(**overrides: float | int) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


@dataclass
class Harness:
    gateway: ToolGateway
    providers: MockProviders
    faults: FaultPlan
    observability: FakeObservability
    audit: FakeAudit
    sleep: FakeSleep
    settings: Settings
    scopes: list[ScopedToolGateway] = field(default_factory=list)

    def scope(self, path: PathName = PathName.PLAN, agent: AgentName | None = None) -> ScopedToolGateway:
        scoped = self.gateway.scoped(path=path, agent=agent, trace=TRACE)
        self.scopes.append(scoped)
        return scoped

    def total_provider_calls(self) -> int:
        p = self.providers
        return (
            p.search.call_count
            + sum(m.call_count for m in p.maps)
            + p.weather.call_count
            + p.places.call_count
            + p.tickets.call_count
        )


def make_harness(
    *,
    faults: FaultPlan | None = None,
    settings: Settings | None = None,
    seed: int = 7,
    weather: WeatherProvider | None = None,
    maps: list[MapsProvider] | None = None,
    fallback_timeout_s: float | None = None,
) -> Harness:
    faults = faults if faults is not None else FaultPlan()
    settings = settings if settings is not None else make_settings()
    providers = build_mock_providers(faults)
    registry = ToolRegistry(
        search=providers.search,
        maps=maps if maps is not None else list(providers.maps),
        weather=weather if weather is not None else providers.weather,
        places=providers.places,
        tickets=providers.tickets,
        fallback_timeout_s=fallback_timeout_s,
    )
    observability, audit, sleep = FakeObservability(), FakeAudit(), FakeSleep()
    gateway = ToolGateway(
        registry=registry,
        allowlist=ToolAllowlist(),
        settings=settings,
        observability=observability,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        sleep=sleep,
        rng=random.Random(seed),
    )
    return Harness(gateway, providers, faults, observability, audit, sleep, settings)


def forecast_json(**fields: str) -> str:
    body: dict[str, str] = {
        "operation": "forecast",
        "location": "Kyoto",
        "start_date": "2026-04-12",
        "end_date": "2026-04-14",
    }
    body.update(fields)
    return json.dumps(body)
