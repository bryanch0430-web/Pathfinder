"""Metric 1 - constraint satisfaction.

Appendix C: "benchmark script; code on structured fields; judge ONLY for a free-text constraint.
Micro = constraints met / constraints stated. Macro = queries in which every constraint of that
class holds. Final pass requires both classes. A custom query passes only if every hard
constraint holds."

Classes on the custom sets:
  * hard: every stated `HardConstraint` (budget, must_visit, avoid, dates, party: code;
    free_text: judge), plus the context's own budget / dates / party size when no explicit
    constraint of that kind restates them;
  * commonsense: the four check-and-merge checks (dates, budget, route, tickets) from
    `backend.agents.checks.check_plan`, so the harness applies exactly the rules the runtime
    applies.
TravelPlanner uses the same scorer with its official commonsense / hard classes once the
official evaluation script is mapped (TODO, see datasets.load_travelplanner).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from backend.agents.checks import check_plan
from backend.eval.datasets import load_travelplanner
from backend.eval.harness import build_rig, eval_settings
from backend.eval.judge import Judge, JudgeVerdict
from backend.eval.models import CustomScenario, scenario_message
from backend.eval.report import MetricReport, SummaryRow, category_match, fmt_rate, names_match
from backend.eval.stats import rate
from backend.schemas.common import Money
from backend.schemas.observability import ToolCallRecord
from backend.schemas.trip import ConstraintKind, HardConstraint, TripContext
from backend.schemas.trip_plan import CheckName, Place, TripPlan
from backend.settings import Settings


class ConstraintClass(StrEnum):
    HARD = "hard"
    COMMONSENSE = "commonsense"


class Method(StrEnum):
    CODE = "code"
    JUDGE = "judge"


class ConstraintCheck(BaseModel):
    name: str
    cls: ConstraintClass
    kind: str
    value: str
    method: Method
    passed: bool
    detail: str = ""


class QueryConstraints(BaseModel):
    query_id: str
    plan_produced: bool
    checks: list[ConstraintCheck] = Field(default_factory=list)

    def of(self, cls: ConstraintClass) -> list[ConstraintCheck]:
        return [c for c in self.checks if c.cls is cls]

    @property
    def hard_pass(self) -> bool:
        """Custom-set pass: every hard constraint holds."""
        return self.plan_produced and all(c.passed for c in self.of(ConstraintClass.HARD))

    @property
    def all_pass(self) -> bool:
        """Final pass: both classes hold."""
        return self.plan_produced and all(c.passed for c in self.checks)

    @property
    def micro(self) -> float | None:
        return rate(sum(c.passed for c in self.checks), len(self.checks))

    def passed_names(self) -> set[str]:
        return {c.name for c in self.checks if c.passed}


# ---- parsing written constraint values ----------------------------------------------------------

_CURRENCY_WORDS = {"yen": "JPY", "¥": "JPY", "hk$": "HKD", "$": "USD", "usd": "USD", "eur": "EUR", "€": "EUR"}


def parse_money(value: str) -> Money | None:
    """'180000 JPY', 'JPY 180,000', '16000 hkd', '80000 yen' -> Money."""
    text = value.strip().casefold()
    amount = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if amount is None:
        return None
    code = re.search(r"\b([a-z]{3})\b", text)
    currency = code.group(1).upper() if code and code.group(1) not in _CURRENCY_WORDS else None
    if currency is None:
        currency = next((c for w, c in _CURRENCY_WORDS.items() if w in text), None)
    if currency is None:
        return None
    return Money(amount=float(amount.group(0).replace(",", "")), currency=currency)


def parse_date_range(value: str) -> tuple[date, date] | None:
    """'2026-12-03..2026-12-05' (or a single ISO date)."""
    found = re.findall(r"\d{4}-\d{2}-\d{2}", value)
    try:
        dates = [date.fromisoformat(d) for d in found]
    except ValueError:
        return None
    if not dates:
        return None
    return min(dates), max(dates)


def scheduled_places(plan: TripPlan) -> list[tuple[date, Place]]:
    out: list[tuple[date, Place]] = []
    for day in plan.days:
        for item in day.items:
            place = plan.place(item.place_id)
            if place is not None:
                out.append((day.date, place))
    return out


# ---- pure evaluation --------------------------------------------------------------------------


def _check(
    name: str, kind: str, value: str, passed: bool, detail: str, method: Method = Method.CODE
) -> ConstraintCheck:
    return ConstraintCheck(
        name=name, cls=ConstraintClass.HARD, kind=kind, value=value, method=method, passed=passed, detail=detail
    )


def check_budget(plan: TripPlan, budget: Money, name: str) -> ConstraintCheck:
    value = f"{budget.amount:.0f} {budget.currency}"
    cost = plan.cost
    if cost is None:
        return _check(name, ConstraintKind.BUDGET.value, value, False, "plan has no cost breakdown")
    if not cost.complete:
        return _check(name, ConstraintKind.BUDGET.value, value, False, "cost incomplete (a priced section is unavailable)")
    if cost.currency != budget.currency:
        return _check(name, ConstraintKind.BUDGET.value, value, False, f"cost in {cost.currency}")
    ok = cost.total <= budget.amount + 1e-6
    return _check(name, ConstraintKind.BUDGET.value, value, ok, f"total {cost.total:.0f} {cost.currency}")


def check_hard_constraint(
    index: int,
    constraint: HardConstraint,
    plan: TripPlan,
    verdict: JudgeVerdict | None,
) -> ConstraintCheck:
    """Code for structured kinds; the judge verdict only for FREE_TEXT."""
    kind, value = constraint.kind, constraint.value
    name = f"hard[{index}]:{kind.value}"
    if kind is ConstraintKind.FREE_TEXT:
        if verdict is None:
            return _check(name, kind.value, value, False, "not judged", Method.JUDGE)
        return _check(name, kind.value, value, verdict.passed, verdict.reason, Method.JUDGE)
    if kind is ConstraintKind.BUDGET:
        money = parse_money(value)
        if money is None:
            return _check(name, kind.value, value, False, "unparseable budget")
        return check_budget(plan, money, name)
    if kind is ConstraintKind.MUST_VISIT:
        hits = [p.name for _, p in scheduled_places(plan) if names_match(value, p.name)]
        return _check(name, kind.value, value, bool(hits), "scheduled" if hits else "not scheduled")
    if kind is ConstraintKind.AVOID:
        bad = sorted(
            {p.name for _, p in scheduled_places(plan) if names_match(value, p.name) or category_match(value, p.category)}
        )
        return _check(name, kind.value, value, not bad, ("scheduled: " + ", ".join(bad)) if bad else "avoided")
    if kind is ConstraintKind.DATES:
        span = parse_date_range(value)
        if span is None:
            return _check(name, kind.value, value, False, "unparseable dates")
        ok = (plan.start_date, plan.end_date) == span
        return _check(name, kind.value, value, ok, f"plan {plan.start_date}..{plan.end_date}")
    # PARTY
    digits = re.search(r"\d+", value)
    if digits is None:
        return _check(name, kind.value, value, False, "unparseable party size")
    ok = plan.party_size == int(digits.group(0))
    return _check(name, kind.value, value, ok, f"plan party {plan.party_size}")


def _implicit_constraints(plan: TripPlan, context: TripContext) -> list[ConstraintCheck]:
    stated = {c.kind for c in context.hard_constraints}
    out: list[ConstraintCheck] = []
    if context.budget and ConstraintKind.BUDGET not in stated:
        out.append(check_budget(plan, context.budget, "context:budget"))
    if context.start_date and context.end_date and ConstraintKind.DATES not in stated:
        ok = (plan.start_date, plan.end_date) == (context.start_date, context.end_date)
        value = f"{context.start_date}..{context.end_date}"
        out.append(_check("context:dates", ConstraintKind.DATES.value, value, ok, f"plan {plan.start_date}..{plan.end_date}"))
    if context.party_size and ConstraintKind.PARTY not in stated:
        ok = plan.party_size == context.party_size
        out.append(_check("context:party", ConstraintKind.PARTY.value, str(context.party_size), ok, f"plan party {plan.party_size}"))
    return out


def _stated_names(context: TripContext) -> list[tuple[str, str, str, Method]]:
    """(name, kind, value, method) of every hard constraint, for a query with no plan."""
    rows = [
        (
            f"hard[{i}]:{c.kind.value}",
            c.kind.value,
            c.value,
            Method.JUDGE if c.kind is ConstraintKind.FREE_TEXT else Method.CODE,
        )
        for i, c in enumerate(context.hard_constraints)
    ]
    stated = {c.kind for c in context.hard_constraints}
    if context.budget and ConstraintKind.BUDGET not in stated:
        rows.append(("context:budget", ConstraintKind.BUDGET.value, f"{context.budget.amount:.0f} {context.budget.currency}", Method.CODE))
    if context.start_date and context.end_date and ConstraintKind.DATES not in stated:
        rows.append(("context:dates", ConstraintKind.DATES.value, f"{context.start_date}..{context.end_date}", Method.CODE))
    if context.party_size and ConstraintKind.PARTY not in stated:
        rows.append(("context:party", ConstraintKind.PARTY.value, str(context.party_size), Method.CODE))
    return rows


def evaluate_constraints(
    query_id: str,
    plan: TripPlan | None,
    context: TripContext,
    verdicts: Mapping[int, JudgeVerdict] | None = None,
) -> QueryConstraints:
    """Score one custom query. `verdicts` maps the index of each FREE_TEXT hard constraint to
    the judge's verdict (computed beforehand; this function makes no model call)."""
    verdicts = verdicts or {}
    if plan is None:
        checks = [
            ConstraintCheck(name=n, cls=ConstraintClass.HARD, kind=k, value=v, method=m, passed=False, detail="no plan")
            for n, k, v, m in _stated_names(context)
        ]
        checks += [
            ConstraintCheck(
                name=f"check:{c.value}", cls=ConstraintClass.COMMONSENSE, kind=c.value, value="",
                method=Method.CODE, passed=False, detail="no plan",
            )
            for c in CheckName
        ]
        return QueryConstraints(query_id=query_id, plan_produced=False, checks=checks)
    checks = [
        check_hard_constraint(i, c, plan, verdicts.get(i)) for i, c in enumerate(context.hard_constraints)
    ]
    checks += _implicit_constraints(plan, context)
    violations = check_plan(plan, context)
    for check_name in CheckName:
        hits = [v.message for v in violations if v.check is check_name]
        checks.append(
            ConstraintCheck(
                name=f"check:{check_name.value}",
                cls=ConstraintClass.COMMONSENSE,
                kind=check_name.value,
                value="",
                method=Method.CODE,
                passed=not hits,
                detail="; ".join(hits[:3]),
            )
        )
    return QueryConstraints(query_id=query_id, plan_produced=True, checks=checks)


class ClassScore(BaseModel):
    stated: int = 0
    met: int = 0
    micro: float | None = None
    queries: int = 0
    queries_all_met: int = 0
    macro: float | None = None


def _class_score(rows: Sequence[Sequence[ConstraintCheck]]) -> ClassScore:
    rows = [r for r in rows if r]
    stated = sum(len(r) for r in rows)
    met = sum(sum(c.passed for c in r) for r in rows)
    all_met = sum(all(c.passed for c in r) for r in rows)
    return ClassScore(
        stated=stated, met=met, micro=rate(met, stated), queries=len(rows), queries_all_met=all_met,
        macro=rate(all_met, len(rows)),
    )


class ConstraintScores(BaseModel):
    queries: int
    hard: ClassScore
    commonsense: ClassScore
    per_kind: dict[str, ClassScore]
    final_pass_rate: float | None = Field(description="Queries where both classes fully hold")
    custom_pass_rate: float | None = Field(description="Queries where every hard constraint holds")


def score_constraints(results: Sequence[QueryConstraints]) -> ConstraintScores:
    """Micro / macro per class, per kind, final pass (both classes) and custom pass (hard)."""
    by_kind: dict[str, list[list[ConstraintCheck]]] = defaultdict(list)
    for r in results:
        grouped: dict[str, list[ConstraintCheck]] = defaultdict(list)
        for c in r.checks:
            grouped[f"{c.cls.value}:{c.kind}"].append(c)
        for key, checks in grouped.items():
            by_kind[key].append(checks)
    return ConstraintScores(
        queries=len(results),
        hard=_class_score([r.of(ConstraintClass.HARD) for r in results]),
        commonsense=_class_score([r.of(ConstraintClass.COMMONSENSE) for r in results]),
        per_kind={k: _class_score(v) for k, v in sorted(by_kind.items())},
        final_pass_rate=rate(sum(r.all_pass for r in results), len(results)),
        custom_pass_rate=rate(sum(r.hard_pass for r in results), len(results)),
    )


# ---- judge-assisted evaluation (async) ------------------------------------------------------


async def judge_free_text(
    judge: Judge, plan: TripPlan | None, context: TripContext, tool_log: Sequence[ToolCallRecord]
) -> dict[int, JudgeVerdict]:
    """Ask the judge about FREE_TEXT constraints only (structured kinds never reach it)."""
    out: dict[int, JudgeVerdict] = {}
    for i, c in enumerate(context.hard_constraints):
        if c.kind is ConstraintKind.FREE_TEXT:
            out[i] = await judge.judge(c.value, plan, tool_log)
    return out


async def evaluate_plan(
    query_id: str,
    plan: TripPlan | None,
    context: TripContext,
    *,
    judge: Judge,
    tool_log: Sequence[ToolCallRecord],
) -> QueryConstraints:
    verdicts = await judge_free_text(judge, plan, context, tool_log)
    return evaluate_constraints(query_id, plan, context, verdicts)


# ---- report + driver --------------------------------------------------------------------------


class TravelPlannerStatus(BaseModel):
    status: Literal["not_run", "loaded_not_scored", "missing"]
    split: str | None = None
    queries: int = 0
    note: str = ""


class ConstraintReport(MetricReport):
    metric: str = "constraints"
    scores: ConstraintScores
    judge_calls: int = 0
    travelplanner: TravelPlannerStatus
    items: list[QueryConstraints] = Field(default_factory=list)

    def summary_row(self) -> SummaryRow:
        s = self.scores
        return SummaryRow(
            metric=self.metric,
            split=self.split,
            n=s.queries,
            headline=(
                f"custom pass {fmt_rate(s.custom_pass_rate)}; hard micro {fmt_rate(s.hard.micro)} "
                f"macro {fmt_rate(s.hard.macro)}; commonsense micro {fmt_rate(s.commonsense.micro)} "
                f"macro {fmt_rate(s.commonsense.macro)}; final {fmt_rate(s.final_pass_rate)}"
            ),
            target="report (no numeric target)",
            met=None,
        )


async def run_constraints(
    scenarios: Sequence[CustomScenario],
    *,
    split_name: str = "all",
    settings: Settings | None = None,
    travelplanner_path: Path | None = None,
    travelplanner_split: Literal["train", "validation", "test"] = "validation",
) -> ConstraintReport:
    """Plan each custom scenario once (fresh container each) and score its constraints."""
    items: list[QueryConstraints] = []
    judge_calls = 0
    for scenario in scenarios:
        async with build_rig(settings or eval_settings()) as rig:
            session = await rig.new_session(scenario.context)
            result = await rig.turn(session, scenario_message(scenario))
            context = await rig.context_of(session)
            judge_calls += sum(1 for c in context.hard_constraints if c.kind is ConstraintKind.FREE_TEXT)
            items.append(
                await evaluate_plan(
                    scenario.id, result.plan, context, judge=rig.judge, tool_log=rig.session_tool_log(session)
                )
            )
    tp = TravelPlannerStatus(status="not_run", note="pass --travelplanner PATH to load the official split")
    if travelplanner_path is not None:
        try:
            queries = load_travelplanner(travelplanner_split, travelplanner_path)
            tp = TravelPlannerStatus(
                status="loaded_not_scored",
                split=travelplanner_split,
                queries=len(queries),
                note="TODO(eval): map plans to the official TravelPlanner evaluation script",
            )
        except FileNotFoundError as exc:
            tp = TravelPlannerStatus(status="missing", split=travelplanner_split, note=str(exc))
    return ConstraintReport(
        split=split_name,
        scores=score_constraints(items),
        judge_calls=judge_calls,
        travelplanner=tp,
        items=items,
    )
