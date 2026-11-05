"""A/B run of the pre-planning audit: the same planning cases with the flag off, then on.

Run from backend/ with the live writer configured (it costs LLM tokens):
    PYTHONPATH=. python scripts/audit_ab.py --cases 10 --out ../var/audit_ab.csv

Each planning case of the evaluation datasets (hongkong, japan, travelplanner) is planned
on the evaluation sandbox (``evaluation.SandboxRegistry``, the setup of
``EvalService._planning_case``) with the real configured writer, once with
``enable_preplan_audit=False`` and once with it True. One CSV row per (case, flag).

Nothing in app/ is changed to observe a run. For the length of one run the script wraps
``Planner.compose`` on that run's planner (to read the AuditReport the planner received),
``planner.apply_audit_blocks`` (to count the candidates compose really removed for the
audit's blocks) and ``planning.repair_plan`` (to read the validation errors handed to
repair), and puts a recording proxy around the writer (to read each call's purpose).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application import planner as planner_module  # noqa: E402
from app.application import planning  # noqa: E402
from app.application.llm_gateway import LLMGateway  # noqa: E402
from app.application.planner import Planner  # noqa: E402
from app.application.planning import PlanPath  # noqa: E402
from app.application.prompts import PromptLibrary  # noqa: E402
from app.application.validation import validate_plan  # noqa: E402
from app.config import Settings  # noqa: E402
from app.domain.models import AuditReport, EvalCase, TripContext  # noqa: E402
from app.eval_checks import RecordingToolbox, ToolLog  # noqa: E402
from app.evaluation import DATASETS, FIXTURES, SandboxRegistry  # noqa: E402
from app.infrastructure.llm import NullLLM, OpenAICompatibleLLM  # noqa: E402
from app.infrastructure.observability import NoopTracer  # noqa: E402

REFUSAL = "needs a live LLM key"
#: The evaluation datasets whose cases are planning cases (EvalService.run plans them).
PLANNING_DATASETS = ("hongkong", "japan", "travelplanner")
COLUMNS = (
    "case_id",
    "flag",
    "llm_calls",
    "prompt_tokens",
    "completion_tokens",
    "wall_ms",
    "findings",
    "blocked_removed",
    "follow_ups",
    "validation_errors_before_repair",
    "repair_rounds",
    "plan_valid",
)
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "var" / "audit_ab.csv"


@dataclass
class Observed:
    """What one run showed: filled by the wrappers while the plan path runs."""

    purposes: list[str] = field(default_factory=list)
    audit: AuditReport | None = None
    blocked_removed: int = 0
    repair_errors: int | None = None


@dataclass
class Row:
    case_id: str
    flag: bool
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    wall_ms: int
    findings: int
    blocked_removed: int
    follow_ups: int
    validation_errors_before_repair: int
    repair_rounds: int
    plan_valid: bool


class _RecordingLLM:
    """Passes every call to the writer and keeps its purpose."""

    def __init__(self, llm: Any, purposes: list[str]):
        self._llm = llm
        self._purposes = purposes
        self.name = getattr(llm, "name", "writer")

    @property
    def available(self) -> bool:
        return bool(getattr(self._llm, "available", False))

    async def complete(self, **kwargs: Any) -> Any:
        self._purposes.append(str(kwargs.get("purpose") or ""))
        return await self._llm.complete(**kwargs)


def live_gateway(settings: Settings) -> LLMGateway:
    """The writer the app would use (the container's rule): live with a key, NullLLM without."""
    llm = OpenAICompatibleLLM(settings) if settings.writer_available else NullLLM()
    return LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)


def load_planning_cases(limit: int, datasets: tuple[str, ...] = PLANNING_DATASETS) -> list[EvalCase]:
    """The first ``limit`` planning cases, read as EvalService.load_cases reads them."""
    cases: list[EvalCase] = []
    for name in datasets:
        path = FIXTURES / DATASETS[name]
        for row in json.loads(path.read_text(encoding="utf-8")):
            row.setdefault("dataset", name)
            row.setdefault("kind", "planning")
            cases.append(EvalCase.model_validate(row))
    return cases[: max(limit, 0)]


def build_row(
    case_id: str, flag: bool, gateway: LLMGateway, observed: Observed, wall_ms: int, plan_valid: bool
) -> Row:
    audit = observed.audit
    return Row(
        case_id=case_id,
        flag=flag,
        llm_calls=gateway.calls,
        prompt_tokens=gateway.prompt_tokens,
        completion_tokens=gateway.completion_tokens,
        wall_ms=wall_ms,
        findings=len(audit.findings) if audit else 0,
        blocked_removed=observed.blocked_removed,
        follow_ups=len(audit.evidence) if audit else 0,
        validation_errors_before_repair=observed.repair_errors or 0,
        # One round = one repair request; "plan.repair.repair" is a JSON retry inside a round.
        repair_rounds=observed.purposes.count("plan.repair"),
        plan_valid=plan_valid,
    )


async def run_case(case: EvalCase, flag: bool, llm: Any, base: Settings) -> Row:
    """One planning case on the sandbox (EvalService._planning_case's setup) with the given flag."""
    settings = base.model_copy(
        update={
            "preplan_budget_seconds": 20,
            "agent_timeout_seconds": 10,
            "enable_flights": True,
            "enable_preplan_audit": flag,
        }
    )
    start = date.today() + timedelta(days=30)
    registry = SandboxRegistry(
        settings, case.destination or "Kyoto", fault=case.fault, fault_detail=case.fault_detail, start=start
    )
    observed = Observed()
    gateway = LLMGateway(_RecordingLLM(llm, observed.purposes), PromptLibrary(settings), NoopTracer(), settings)
    planner = Planner(gateway, settings, registry.fx)
    compose = planner.compose

    async def observed_compose(**kwargs: Any) -> Any:
        observed.audit = kwargs.get("audit")
        return await compose(**kwargs)

    planner.compose = observed_compose  # type: ignore[method-assign]
    plan_path = PlanPath(planner=planner, registry=registry, settings=settings)
    context = TripContext(
        session_id="audit-ab",
        destination=case.destination or "Kyoto",
        origin=case.origin or "Hong Kong",
        start_date=start,
        end_date=start + timedelta(days=max((case.days or 3) - 1, 0)),
        days=case.days or 3,
        party_size=case.party_size or 1,
        budget=case.budget or 3000,
        currency=case.currency,
        preferences=[],
        hard_constraints=case.constraints,
    )
    log = ToolLog()
    repair = planning.repair_plan
    apply_blocks = planner_module.apply_audit_blocks

    async def observed_repair(plan: Any, errors: list[str], *args: Any, **kwargs: Any) -> Any:
        observed.repair_errors = len(errors)
        return await repair(plan, errors, *args, **kwargs)

    def observed_blocks(rows: list[dict], blocked: set[str]) -> tuple[list[dict], set[str]]:
        # compose calls this once per section, after the cap and after exempting the
        # places the traveller named and the kept hotel: what it returns is what it removed.
        kept, removed = apply_blocks(rows, blocked)
        observed.blocked_removed += len(removed)
        return kept, removed

    planning.repair_plan = observed_repair  # type: ignore[assignment]
    planner_module.apply_audit_blocks = observed_blocks  # type: ignore[assignment]
    started = time.perf_counter()
    plan = None
    try:
        plan = await plan_path.run(
            context=context,
            session_id="audit-ab",
            toolbox=RecordingToolbox(log, path="plan", request_id=f"audit-ab-{case.id}-{int(flag)}"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
            version=1,
        )
    except Exception as exc:  # a failed plan is a row with plan_valid False
        print(f"{case.id} flag={flag}: plan failed: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr)
    finally:
        planning.repair_plan = repair  # type: ignore[assignment]
        planner_module.apply_audit_blocks = apply_blocks  # type: ignore[assignment]
    wall_ms = int((time.perf_counter() - started) * 1000)
    plan_valid = plan is not None and not validate_plan(plan, settings)
    return build_row(case.id, flag, gateway, observed, wall_ms, plan_valid)


async def run_ab(cases: list[EvalCase], llm: Any, settings: Settings) -> list[Row]:
    rows: list[Row] = []
    for flag in (False, True):
        for case in cases:
            rows.append(await run_case(case, flag, llm, settings))
    return rows


def write_csv(rows: list[Row], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def main(argv: list[str] | None = None, *, gateway: LLMGateway | None = None) -> int:
    parser = argparse.ArgumentParser(description="A/B the pre-planning audit (flag off, then on).")
    parser.add_argument("--cases", type=int, default=10, help="planning cases to run (each runs twice)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="CSV file to write")
    args = parser.parse_args(argv)

    if gateway is None:
        gateway = live_gateway(Settings())
    # Refuse before any work: no case is run and no file is written without a live writer.
    if not gateway.available:
        print(REFUSAL)
        return 1

    cases = load_planning_cases(args.cases)
    rows = asyncio.run(run_ab(cases, gateway.llm, gateway.settings))
    write_csv(rows, args.out)
    print(f"wrote {len(rows)} rows for {len(cases)} cases to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
