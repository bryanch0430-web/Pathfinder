"""Composition root. The only module that knows every concrete adapter."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.application.asking import AskPath
from app.application.clarification import Clarifier
from app.application.llm_gateway import LLMGateway
from app.application.memory import MemoryService
from app.application.modification import ModifyPath
from app.application.planning import PlanPath
from app.application.planner import Planner
from app.application.prompts import PromptLibrary
from app.application.router import System1Router
from app.application.orchestrator import TurnService
from app.application.translation import LLMTranslator, TranslationService
from app.config import Settings, get_settings
from app.domain.ports import LLM
from app.evaluation import EvalService
from app.infrastructure.decision import JevDecisionClient
from app.infrastructure.llm import NullLLM, OpenAICompatibleLLM
from app.infrastructure.observability import TracerProxy
from app.infrastructure.providers.registry import ProviderRegistry
from app.infrastructure.store import SQLiteStore

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    store: SQLiteStore
    registry: ProviderRegistry
    llm: LLM
    gateway: LLMGateway
    tracer_proxy: TracerProxy
    router: System1Router
    memory: MemoryService
    plan_path: PlanPath
    modify_path: ModifyPath
    ask_path: AskPath
    clarifier: Clarifier
    turn_service: TurnService
    evaluation: EvalService
    translation: TranslationService | None = None

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "app": self.settings.app_name,
            "environment": self.settings.environment,
            "writer": {
                "available": bool(getattr(self.llm, "available", False)),
                "model": getattr(self.llm, "name", "offline"),
            },
            "decision_model": {
                "enabled": self.settings.decision_available,
                "model": self.settings.decision_model,
            },
            "judge": {
                "enabled": self.settings.judge_available,
                "model": self.settings.judge_model if self.settings.judge_available else "",
            },
            "langfuse": {
                "enabled": self.settings.langfuse_enabled,
                "configured": self.settings.langfuse_available,
            },
            "providers": self.registry.describe(),
            "thresholds": {
                "router": self.settings.router_threshold,
                "memory_similarity": self.settings.memory_similarity_cutoff,
                "preplan_budget_seconds": self.settings.preplan_budget_seconds,
                "max_repairs": self.settings.max_repairs,
            },
        }


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()
    settings.ensure_directories()
    store = SQLiteStore(settings.database_path, share_ttl_days=settings.share_ttl_days)
    registry = ProviderRegistry.build(settings)
    prompts = PromptLibrary(settings)
    llm: LLM = OpenAICompatibleLLM(settings) if settings.writer_available else NullLLM()
    if settings.writer_available:
        logger.info(
            "writer: label=%s vendor=%s model=%s base=%s",
            settings.llm_provider,
            settings.writer_vendor,
            settings.writer_model,
            settings.writer_base_url,
        )
        if settings.llm_provider == "deepseek" and settings.writer_vendor != "deepseek":
            logger.warning(
                "LLM_PROVIDER=deepseek but the effective base URL is %s (%s); "
                "set LLM_PROVIDER to match the vendor or change LLM_BASE_URL",
                settings.writer_base_url,
                settings.writer_vendor,
            )
        if settings.writer_is_xai and settings.writer_vendor != "xai":
            logger.warning(
                "LLM_PROVIDER=%s but the effective base URL is not xAI (%s)",
                settings.llm_provider,
                settings.writer_base_url,
            )
    else:
        logger.info("writer: not configured; turns run offline")
    tracer_proxy = TracerProxy()
    gateway = LLMGateway(llm, prompts, tracer_proxy, settings)
    decision_client = JevDecisionClient(settings, prompts) if settings.decision_available else None
    router = System1Router(
        gateway=gateway,
        settings=settings,
        decision_client=decision_client,
        use_model=True,
    )
    memory = MemoryService(store, settings)
    route_matrix = next(
        (getattr(source, "matrix", None) for source in registry.routes if hasattr(source, "matrix")),
        None,
    )
    planner = Planner(gateway, settings, registry.fx, route_matrix=route_matrix)
    plan_path = PlanPath(planner=planner, registry=registry, settings=settings)
    modify_path = ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway)
    ask_path = AskPath(gateway=gateway, registry=registry, settings=settings)
    clarifier = Clarifier(gateway)
    turn_service = TurnService(
        store=store,
        memory=memory,
        router=router,
        clarifier=clarifier,
        plan_path=plan_path,
        modify_path=modify_path,
        ask_path=ask_path,
        gateway=gateway,
        settings=settings,
        tracer_proxy=tracer_proxy,
    )
    evaluation = EvalService(store, settings)
    return Container(
        settings=settings,
        store=store,
        registry=registry,
        llm=llm,
        gateway=gateway,
        tracer_proxy=tracer_proxy,
        router=router,
        memory=memory,
        plan_path=plan_path,
        modify_path=modify_path,
        ask_path=ask_path,
        clarifier=clarifier,
        turn_service=turn_service,
        evaluation=evaluation,
        translation=TranslationService(LLMTranslator(gateway)),
    )
