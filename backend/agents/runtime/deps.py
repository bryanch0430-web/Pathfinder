"""Dependencies shared by every path and agent (explicit wiring, no globals)."""

from __future__ import annotations

from dataclasses import dataclass

from backend.agents.runtime.caller import ModelCaller
from backend.observability.recorder import Observability
from backend.security.audit import SecurityAudit
from backend.security.canary import CanaryGuard
from backend.settings import Settings
from backend.tools.gateway import ToolGateway


@dataclass(frozen=True)
class RuntimeDeps:
    settings: Settings
    agent_caller: ModelCaller  # agents, planner, note cleaning, chat (provisionally Grok 4.7)
    router_caller: ModelCaller  # System 1 router (provisionally Jev)
    tools: ToolGateway
    observability: Observability
    audit: SecurityAudit
    canary: CanaryGuard
