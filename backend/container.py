"""Composition root: builds every service from settings, with explicit overrides for tests and
the evaluation harness. This is the only module that knows concrete implementations; every other
module depends on interfaces (repositories, model clients, providers)."""

from __future__ import annotations

from dataclasses import dataclass

from backend.agents.orchestrator import OrchestratorOptions, TurnOrchestrator
from backend.agents.runtime.caller import ModelCaller
from backend.agents.runtime.deps import RuntimeDeps
from backend.agents.runtime.llm import (
    LLMClient,
    build_agent_client,
    build_judge_client,
    build_router_client,
)
from backend.db.factory import build_repositories
from backend.db.repositories.base import Repositories
from backend.memory.background import BackgroundWriter
from backend.memory.embeddings import EmbeddingClient, build_embedding_client
from backend.memory.reranker import build_reranker
from backend.memory.retrieval import SavedTripRetriever
from backend.memory.session import InMemorySessionStore, SessionStore
from backend.observability.recorder import CallSink, Observability
from backend.security.audit import SecurityAudit
from backend.security.canary import CanaryGuard
from backend.settings import Settings, get_settings
from backend.tools.factory import build_tool_gateway
from backend.tools.gateway import Sleep, ToolGateway
from backend.tools.providers.mock.faults import FaultPlan


@dataclass
class Container:
    settings: Settings
    observability: Observability
    audit: SecurityAudit
    canary: CanaryGuard
    tools: ToolGateway
    repos: Repositories
    embedder: EmbeddingClient
    retriever: SavedTripRetriever
    sessions: SessionStore
    writer: BackgroundWriter
    router_llm: LLMClient
    agent_llm: LLMClient
    judge_llm: LLMClient
    deps: RuntimeDeps
    orchestrator: TurnOrchestrator

    async def start(self) -> None:
        await self.writer.start()

    async def stop(self) -> None:
        await self.writer.stop()
        await self.repos.close()
        self.observability.flush()


def build_container(
    settings: Settings | None = None,
    *,
    faults: FaultPlan | None = None,
    router_llm: LLMClient | None = None,
    agent_llm: LLMClient | None = None,
    judge_llm: LLMClient | None = None,
    sinks: list[CallSink] | None = None,
    sleep: Sleep | None = None,
    options: OrchestratorOptions | None = None,
    repos: Repositories | None = None,
) -> Container:
    settings = settings or get_settings()
    observability = Observability(settings, sinks=sinks)
    audit = SecurityAudit(settings.log_dir / "security_audit.json" if sinks is None else None)
    canary = CanaryGuard(settings.resolved_canary())
    tools = build_tool_gateway(settings, observability, audit, faults=faults, sleep=sleep)
    repos = repos or build_repositories(settings)
    embedder = build_embedding_client(settings)
    retriever = SavedTripRetriever(
        embedder=embedder,
        trips=repos.trips,
        embeddings=repos.embeddings,
        reranker=build_reranker(settings),
        settings=settings,
    )
    sessions = InMemorySessionStore()
    writer = BackgroundWriter(embedder=embedder, repos=repos)
    router_llm = router_llm or build_router_client(settings)
    agent_llm = agent_llm or build_agent_client(settings)
    judge_llm = judge_llm or build_judge_client(settings)
    deps = RuntimeDeps(
        settings=settings,
        agent_caller=ModelCaller(
            agent_llm, model=settings.agent_model, observability=observability, audit=audit, canary=canary
        ),
        router_caller=ModelCaller(
            router_llm, model=settings.router_model, observability=observability, audit=audit, canary=canary
        ),
        tools=tools,
        observability=observability,
        audit=audit,
        canary=canary,
    )
    orchestrator = TurnOrchestrator(
        deps=deps, sessions=sessions, retriever=retriever, writer=writer, options=options
    )
    return Container(
        settings=settings,
        observability=observability,
        audit=audit,
        canary=canary,
        tools=tools,
        repos=repos,
        embedder=embedder,
        retriever=retriever,
        sessions=sessions,
        writer=writer,
        router_llm=router_llm,
        agent_llm=agent_llm,
        judge_llm=judge_llm,
        deps=deps,
        orchestrator=orchestrator,
    )
