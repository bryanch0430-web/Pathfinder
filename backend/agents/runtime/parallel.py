"""Run pre-planning agents concurrently under one time budget.

Proposal: "A new trip runs the four pre-planning agents together, then the planner, inside a
time budget. If that budget is exceeded, unfinished tool results are marked unavailable and the
plan is still returned, rather than holding the turn until every tool completes."
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

from backend.schemas.agents import AgentResult
from backend.schemas.common import ALL_AGENTS, AgentName, SectionStatus

logger = logging.getLogger(__name__)

AgentJob = Callable[[], Awaitable[AgentResult]]
AgentHook = Callable[[AgentName, AgentResult | None], Awaitable[None]]

BUDGET_EXCEEDED_REASON = "time budget exceeded; result unavailable"


def unavailable(agent: AgentName, reason: str) -> AgentResult:
    return AgentResult(agent=agent, status=SectionStatus.UNAVAILABLE, reason=reason)


async def run_with_budget(
    jobs: Mapping[AgentName, AgentJob],
    *,
    budget_s: float,
    on_start: AgentHook | None = None,
    on_finish: AgentHook | None = None,
) -> dict[AgentName, AgentResult]:
    """Run every job concurrently; anything unfinished at the deadline is cancelled and its
    section reported UNAVAILABLE. An agent that crashes is also UNAVAILABLE (never fatal)."""
    async def wrapped(agent: AgentName, job: AgentJob) -> AgentResult:
        result = await job()
        if on_finish:
            await on_finish(agent, result)
        return result

    tasks: dict[asyncio.Task[AgentResult], AgentName] = {}
    for agent, job in jobs.items():
        if on_start:
            await on_start(agent, None)
        tasks[asyncio.create_task(wrapped(agent, job), name=f"agent:{agent}")] = agent

    results: dict[AgentName, AgentResult] = {}
    late: list[AgentName] = []
    if tasks:
        _, pending = await asyncio.wait(tasks.keys(), timeout=budget_s)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task, agent in tasks.items():
            if task in pending:
                results[agent] = unavailable(agent, BUDGET_EXCEEDED_REASON)
                late.append(agent)
                continue
            exc = task.exception()
            if exc is not None:
                logger.error("agent %s failed: %r", agent, exc)
                results[agent] = unavailable(agent, f"agent error: {type(exc).__name__}")
                late.append(agent)
            else:
                results[agent] = task.result()
    if on_finish:
        for agent in late:
            await on_finish(agent, results[agent])
    ordered = {a: results[a] for a in ALL_AGENTS if a in results}
    return ordered
