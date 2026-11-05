"""Web searches the audit asked for, run through the toolbox.

A follow-up checks one finding by looking for reference pages. It is a ``web.search`` tool call like
any other: logged, traced as ``tool.web.search`` and counted against the turn's cap. One deadline
bounds all follow-ups together. What comes back is reference material only (a title, a link and where
it came from): no price, no temperature and no page text is copied into the audit.

Nothing here raises for a failed search. A search that fails, is refused, finds nothing or runs out of
time becomes a ``gap`` on its evidence entry. Cancelling the caller still cancels the searches, and
a canary leak is never swallowed.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

from app.application.toolbox import Toolbox
from app.application.web_lookup import _gap_section, lookup_web_references, web_sources
from app.config import Settings
from app.domain.errors import CanaryLeak, ProviderError
from app.domain.models import AgentName, AuditFinding, AuditReport, DataGap, FailureKind, FollowUpEvidence, SourceRef

#: The web lookup names the ticket agent's section "flight"; any other name matches no page.
_LOOKUP_SECTION = {AgentName.TICKET: "flight"}


async def run_follow_ups(
    report: AuditReport,
    *,
    registry: Any,
    toolbox: Toolbox,
    city: str,
    settings: Settings,
) -> AuditReport:
    """A copy of ``report`` with one evidence entry per finding that asked for a follow-up.

    A skipped report, or one with no follow-up, comes back unchanged and no search is made.
    """
    jobs = [(index, finding) for index, finding in enumerate(report.findings) if finding.follow_up is not None]
    if report.skipped or not jobs:
        return report

    deepseek, grok = web_sources(registry, settings, toolbox)
    done: dict[int, FollowUpEvidence] = {}

    async def one(index: int, finding: AuditFinding) -> None:
        done[index] = await _follow_up(
            index, finding, city=city, deepseek=deepseek, grok=grok, tracer=getattr(toolbox, "tracer", None)
        )

    tasks = [asyncio.ensure_future(one(index, finding)) for index, finding in jobs]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=settings.audit_follow_up_seconds)
    except asyncio.TimeoutError:
        # Searches that did finish keep their result; each one still running gets a timeout gap.
        pass
    finally:
        # A canary leak out of one search must not leave its siblings running.
        for task in tasks:
            if not task.done():
                task.cancel()

    evidence: list[FollowUpEvidence] = []
    for index, finding in jobs:
        entry = done.get(index)
        if entry is None:
            entry = _evidence(
                index,
                finding,
                gap=DataGap(
                    section=_gap_section(_section(finding)),
                    kind=FailureKind.TIMEOUT,
                    message="The follow-up search did not finish in time.",
                    retryable=True,
                ),
            )
        evidence.append(entry)
    return report.model_copy(update={"evidence": evidence})


async def _follow_up(
    index: int,
    finding: AuditFinding,
    *,
    city: str,
    deepseek: Any | None,
    grok: Any | None,
    tracer: Any | None,
) -> FollowUpEvidence:
    section = _section(finding)
    if deepseek is None and grok is None:
        return _evidence(
            index,
            finding,
            gap=DataGap(
                section=_gap_section(section),
                kind=FailureKind.NO_DATA,
                message="No web search is configured for the audit's follow-up.",
                retryable=False,
            ),
        )
    assert finding.follow_up is not None
    try:
        found = await lookup_web_references(
            section=section,
            city=city,
            query=finding.follow_up.query,
            deepseek=deepseek,
            grok=grok,
            tracer=tracer,
        )
    except CanaryLeak:
        raise
    except ProviderError as exc:
        return _evidence(
            index,
            finding,
            gap=DataGap(section=_gap_section(section), kind=exc.kind, message=str(exc), retryable=exc.retryable),
        )
    except Exception:
        # The audit fails open: a surprise in one search is a gap on that search only.
        return _evidence(
            index,
            finding,
            gap=DataGap(
                section=_gap_section(section),
                kind=FailureKind.SERVER_ERROR,
                message="The follow-up search failed.",
                retryable=False,
            ),
        )
    references = _source_refs(found.references)
    gap = found.gap if not references else None
    return _evidence(index, finding, references=references, gap=gap)


def _section(finding: AuditFinding) -> str:
    return _LOOKUP_SECTION.get(finding.agent, finding.agent.value)


def _evidence(
    index: int,
    finding: AuditFinding,
    *,
    references: list[SourceRef] | None = None,
    gap: DataGap | None = None,
) -> FollowUpEvidence:
    assert finding.follow_up is not None
    return FollowUpEvidence(
        finding_index=index, query=finding.follow_up.query, references=references or [], gap=gap
    )


def _source_refs(rows: list[dict[str, Any]]) -> list[SourceRef]:
    """Reference pages only. A row with no link is dropped; nothing else of the row is kept."""
    refs: list[SourceRef] = []
    for row in rows:
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        title = str(row.get("title") or "").strip() or urlparse(url).netloc or url
        refs.append(SourceRef(title=title, url=url, source=str(row.get("source") or "web-search")))
    return refs
