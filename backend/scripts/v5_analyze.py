"""Aggregate the v5 part-model study and compute the Jev + LLM hybrid."""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "docs" / "eval-artifacts" / "v5-model-study"


def load(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def wilson(success: int, total: int, z: float = 1.96) -> tuple[float, float]:
    p = success / total
    den = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / den
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / den
    return max(0, centre - margin), min(1, centre + margin)


def hybrid(base_key: str, jev_key: str, cutoff: float = 0.8) -> dict:
    base = ROUTING["cases"][base_key]
    jev = ROUTING["cases"][jev_key]
    rows = []
    for b, j in zip(base, jev):
        use_jev = j.get("confidence") is not None and j["confidence"] >= cutoff
        pred = j["predicted"] if use_jev else b["predicted"]
        rows.append(
            {
                "id": b["id"],
                "expected": b["expected"],
                "predicted": pred,
                "correct": pred == b["expected"],
                "route": "jev" if use_jev else "llm",
                "latency_ms": j["latency_ms"] + (0 if use_jev else b["latency_ms"]),
            }
        )
    passed = sum(row["correct"] for row in rows)
    lo, hi = wilson(passed, len(rows))
    jev_used = sum(row["route"] == "jev" for row in rows)
    return {
        "base": base_key,
        "jev": jev_key,
        "cutoff": cutoff,
        "correct": passed,
        "n": len(rows),
        "accuracy": round(passed / len(rows), 4),
        "wilson95": [round(lo, 4), round(hi, 4)],
        "jev_coverage": round(jev_used / len(rows), 3),
        "llm_fallbacks": len(rows) - jev_used,
        "mean_latency_ms": round(sum(row["latency_ms"] for row in rows) / len(rows), 1),
        "rows": rows,
    }


OLD = load("routing-20261122-181101.json")
NEW_JEV = load("routing-20261122-181819.json")
ROUTING = {
    **OLD,
    "summaries": {**OLD["summaries"], "jev": NEW_JEV["summaries"]["jev"]},
    "cases": {**OLD["cases"], "jev": NEW_JEV["cases"]["jev"]},
}
hybrids = {
    "jev+sonnet@0.8": hybrid("sonnet-0.0", "jev", 0.8),
    "jev+flash@0.8": hybrid("flash-lite-0.0", "jev", 0.8),
}
(OUT / "routing-merged.json").write_text(json.dumps(ROUTING, indent=2), encoding="utf-8")
(OUT / "hybrid-router.json").write_text(json.dumps(hybrids, indent=2), encoding="utf-8")

lines = ["# Routing / turn-router analysis", ""]
lines += ["| Arm | Correct | Accuracy | p50 ms |", "|---|---:|---:|---:|"]
for key, summary in ROUTING["summaries"].items():
    lines.append(
        f"| {key} | {summary['correct']}/{summary['n']} | {summary['accuracy']:.1%} | {summary['p50_latency_ms']} |"
    )
lines += ["", "## Jev as a pre-gate", "", "| Hybrid | Correct | Accuracy | Jev coverage | LLM fallbacks | Mean latency (sum of stages) |", "|---|---:|---:|---:|---:|---:|"]
for key, data in hybrids.items():
    lines.append(
        f"| {key} | {data['correct']}/{data['n']} | {data['accuracy']:.1%} | {data['jev_coverage']:.0%} | "
        f"{data['llm_fallbacks']} | {data['mean_latency_ms']:.0f} ms |"
    )
lines += [
    "",
    "The hybrid is a sequential pre-gate: Jev answers when confidence ≥ the cut-off; otherwise the analyst is called.",
    "Latency is therefore the sum of the two stages for escalated cases, not a concurrent race.",
]
(OUT / "routing-analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))

# Aggregate the dataset comparison.
datasets = load("datasets-latest.json")
summary_rows = []
for dataset, arms in datasets["datasets"].items():
    for arm, run in arms.items():
        summary_rows.append(
            {
                "dataset": dataset,
                "arm": arm,
                "model": run["model"],
                "passed": run["passed"],
                "n": run["case_count"],
                "latency_s": round(run["latency_ms"] / 1000, 1),
                "metrics": {metric["label"]: metric["value"] for metric in run["metrics"]},
            }
        )
(OUT / "dataset-summary.json").write_text(json.dumps(summary_rows, indent=2), encoding="utf-8")
print("wrote aggregate files")
