"""Router scorecard breakdown for the trial report.

About 80 utterances labelled new trip, modify, quick question or unclear score
the router. The run's headline `routing_accuracy` is every case. This adds the
same count per labelled intent, so the Evaluation page shows where the router misses.
"""

from __future__ import annotations

from app.dataset_splits import load_rows
from app.domain.models import EvalCaseResult, EvalMetric

INTENT_LABELS = {"plan": "new trip", "modify": "modify", "ask": "quick question", "unclear": "unclear"}


def _pct(passed: int, total: int) -> float:
    return round(100.0 * passed / total, 1) if total else 0.0


def expected_intents() -> dict[str, str]:
    return {str(row["id"]): str(row.get("expected_intent") or "") for row in load_rows("routing")}


def routing_breakdown(results: list[EvalCaseResult]) -> list[EvalMetric]:
    metrics: list[EvalMetric] = []
    expected = expected_intents()
    for intent, label in INTENT_LABELS.items():
        subset = [result for result in results if expected.get(result.case_id) == intent]
        if subset:
            metrics.append(
                EvalMetric(
                    key=f"routing_accuracy_{intent}",
                    label=f"Routing accuracy: {label}",
                    value=_pct(sum(result.passed for result in subset), len(subset)),
                )
            )
    # Proposal TC-10: how many utterances the router's rules-first shortcut would
    # decide with no model call, and how often those decisions are right.
    alone = [result for result in results if result.scores.get("rules_alone")]
    metrics.append(
        EvalMetric(
            key="rules_first_share",
            label="Decided by the rules alone (no model call)",
            value=_pct(len(alone), len(results)),
        )
    )
    if alone:
        metrics.append(
            EvalMetric(
                key="rules_first_accuracy",
                label=f"Accuracy of rules-alone decisions ({sum(r.passed for r in alone)}/{len(alone)})",
                value=_pct(sum(result.passed for result in alone), len(alone)),
            )
        )
    return metrics
