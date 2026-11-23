"""Shadow analysis for a dual-router (analyst + Jev) policy.

This is a retrospective shadow run over the saved per-case routing predictions.
It does not change the runtime and does not call a provider. The point is to answer
the design question: if both signals had been collected for the same input, what
would agreement/abstention have done to accuracy, coverage and calibration?

Run:
    backend/.venv/Scripts/python.exe backend/scripts/v5_shadow_router.py
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "docs" / "eval-artifacts" / "v5-model-study"
SOURCE = OUT / "routing-merged.json"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / den
    return max(0.0, centre - margin), min(1.0, centre + margin)


def auroc(rows: list[dict]) -> float | None:
    positive = [row["confidence"] for row in rows if row["correct"]]
    negative = [row["confidence"] for row in rows if not row["correct"]]
    if not positive or not negative:
        return None
    wins = sum(1 for p in positive for q in negative if p > q)
    ties = sum(1 for p in positive for q in negative if p == q)
    return (wins + 0.5 * ties) / (len(positive) * len(negative))


def calibration(rows: list[dict]) -> dict:
    values = [(float(row.get("confidence") or 0.0), 1.0 if row["correct"] else 0.0) for row in rows]
    bins: list[list[tuple[float, float]]] = [[] for _ in range(5)]
    for confidence, target in values:
        bins[min(4, max(0, int(confidence * 5)))].append((confidence, target))
    ece = 0.0
    brier = 0.0
    table = []
    for index, bucket in enumerate(bins):
        if not bucket:
            continue
        mean_confidence = statistics.mean(value[0] for value in bucket)
        accuracy = statistics.mean(value[1] for value in bucket)
        ece += len(bucket) / len(values) * abs(mean_confidence - accuracy)
        brier += sum((confidence - target) ** 2 for confidence, target in bucket) / len(values)
        table.append(
            {
                "bin": index,
                "range": f"{index / 5:.1f}–{(index + 1) / 5:.1f}",
                "n": len(bucket),
                "mean_confidence": round(mean_confidence, 3),
                "accuracy": round(accuracy, 3),
            }
        )
    return {
        "n": len(values),
        "ece": round(ece, 4),
        "brier": round(brier, 4),
        "auroc": round(auroc(rows), 4) if auroc(rows) is not None else None,
        "bins": table,
    }


def policy(rows: list[tuple[dict, dict]], name: str, decide) -> dict:
    accepted: list[tuple[dict, dict, str]] = []
    clarified = 0
    for analyst, jev in rows:
        decision = decide(analyst, jev)
        if decision is None:
            clarified += 1
        else:
            accepted.append((analyst, jev, decision))
    correct = sum(1 for analyst, _jev, prediction in accepted if prediction == analyst["expected"])
    lo, hi = wilson(correct, len(accepted))
    return {
        "policy": name,
        "accepted": len(accepted),
        "clarified": clarified,
        "coverage": round(len(accepted) / len(rows), 4) if rows else 0.0,
        "correct": correct,
        "accuracy_on_accepted": round(correct / len(accepted), 4) if accepted else None,
        "wilson95": [round(lo, 4), round(hi, 4)],
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    cases = data["cases"]
    primary = "flash-lite-0.0"
    analyst_rows = {row["id"]: row for row in cases[primary]}
    jev_rows = {row["id"]: row for row in cases["jev"]}
    sonnet_rows = {row["id"]: row for row in cases["sonnet-0.0"]}
    ids = [row["id"] for row in cases[primary]]
    paired = [(analyst_rows[row_id], jev_rows[row_id]) for row_id in ids]
    agreements = [analyst["predicted"] == jev["predicted"] for analyst, jev in paired]
    disagreement = [
        {
            "id": analyst["id"],
            "input": analyst.get("input", ""),
            "expected": analyst["expected"],
            "analyst": analyst["predicted"],
            "analyst_confidence": analyst.get("confidence"),
            "analyst_correct": analyst["correct"],
            "jev": jev["predicted"],
            "jev_confidence": jev.get("confidence"),
            "jev_correct": jev["correct"],
        }
        for analyst, jev in paired
        if analyst["predicted"] != jev["predicted"]
    ]
    analyst_calibration = calibration(list(analyst_rows.values()))
    jev_calibration = calibration(list(jev_rows.values()))
    sonnet_calibration = calibration(list(sonnet_rows.values()))
    policies = [
        policy(paired, "analyst only (Flash-Lite)", lambda a, j: a["predicted"]),
        policy(paired, "Jev only", lambda a, j: j["predicted"]),
        policy(
            paired,
            "agreement gate (accept if both agree; otherwise clarify)",
            lambda a, j: a["predicted"] if a["predicted"] == j["predicted"] else None,
        ),
        policy(
            paired,
            "agreement + analyst confidence ≥ 0.8",
            lambda a, j: a["predicted"]
            if a["predicted"] == j["predicted"] and float(a.get("confidence") or 0) >= 0.8
            else None,
        ),
        policy(
            paired,
            "both confidences ≥ 0.8 (analyst route)",
            lambda a, j: a["predicted"]
            if float(a.get("confidence") or 0) >= 0.8 and float(j.get("confidence") or 0) >= 0.8
            else None,
        ),
    ]
    parallel_latency = [
        max(float(a.get("latency_ms") or 0), float(j.get("latency_ms") or 0))
        for a, j in paired
    ]
    output = {
        "generated_at": "2026-11-23T13:45:00+08:00",
        "method": "retrospective shadow over saved one-run per-case routing predictions; no provider calls; no runtime change",
        "dataset": data.get("dataset", "routing"),
        "n": len(ids),
        "primary_analyst": primary,
        "agreement": {
            "n": len(ids),
            "agree": sum(agreements),
            "disagree": len(ids) - sum(agreements),
            "rate": round(sum(agreements) / len(ids), 4),
            "accuracy_when_agree": round(
                sum(1 for (a, _j), same in zip(paired, agreements) if same and a["correct"]) / max(1, sum(agreements)),
                4,
            ),
            "both_wrong": sum(1 for a, j in paired if not a["correct"] and not j["correct"]),
        },
        "accuracy": {
            "flash_lite": round(sum(1 for row in analyst_rows.values() if row["correct"]) / len(ids), 4),
            "jev": round(sum(1 for row in jev_rows.values() if row["correct"]) / len(ids), 4),
            "sonnet": round(sum(1 for row in sonnet_rows.values() if row["correct"]) / len(ids), 4),
        },
        "calibration": {
            "flash_lite": analyst_calibration,
            "jev": jev_calibration,
            "sonnet": sonnet_calibration,
        },
        "policies": policies,
        "latency_ms": {
            "flash_lite_mean": round(statistics.mean(float(row["latency_ms"]) for row in analyst_rows.values()), 1),
            "jev_mean": round(statistics.mean(float(row["latency_ms"]) for row in jev_rows.values()), 1),
            "parallel_max_mean": round(statistics.mean(parallel_latency), 1),
            "parallel_max_p50": round(statistics.median(parallel_latency), 1),
        },
        "disagreements": disagreement,
        "interpretation": {
            "headline": "Agreement was perfect on this 84-case retrospective set: all 78 agreed predictions were right, and the six disagreements contained the analyst's only error. The gate would have clarified 7.1% of cases and accepted 92.9% at 100% accuracy.",
            "caveat": "One run per case, one model/provider/date, n=84, and no live concurrent shadow. The agreement gate was more accurate than analyst-only on this sample but at a coverage cost; this is a hypothesis, not a general result.",
        },
    }
    (OUT / "shadow-router.json").write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Dual-router shadow analysis",
        "",
        "**Status:** retrospective shadow over saved per-case routing predictions. No live provider calls and no runtime change.",
        "",
        f"- Dataset: `{output['dataset']}` · n = **{output['n']}**",
        f"- Primary analyst: **{primary}** (Gemini 3.5 Flash-Lite)",
        f"- Agreement: **{output['agreement']['agree']}/{output['n']} = {output['agreement']['rate']:.1%}**",
        f"- Accuracy when agreed: **{output['agreement']['accuracy_when_agree']:.1%}**",
        f"- Disagreements: **{output['agreement']['disagree']}** · both wrong: **{output['agreement']['both_wrong']}**",
        "",
        "## Policies",
        "",
        "| Policy | Accepted | Clarified | Coverage | Correct | Accuracy on accepted | Wilson 95% |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in policies:
        acc = f"{row['accuracy_on_accepted']:.1%}" if row["accuracy_on_accepted"] is not None else "—"
        lines.append(
            f"| {row['policy']} | {row['accepted']} | {row['clarified']} | {row['coverage']:.1%} | "
            f"{row['correct']} | {acc} | {row['wilson95'][0]:.1%}–{row['wilson95'][1]:.1%} |"
        )
    lines += [
        "",
        "## Confidence calibration (descriptive)",
        "",
        "| Router | ECE | Brier | AUROC | Mean confidence |",
        "|---|---:|---:|---:|---:|",
        f"| Flash-Lite analyst | {analyst_calibration['ece']:.3f} | {analyst_calibration['brier']:.3f} | {analyst_calibration['auroc']:.3f} | {statistics.mean(float(r.get('confidence') or 0) for r in analyst_rows.values()):.3f} |",
        f"| Jev | {jev_calibration['ece']:.3f} | {jev_calibration['brier']:.3f} | {jev_calibration['auroc']:.3f} | {statistics.mean(float(r.get('confidence') or 0) for r in jev_rows.values()):.3f} |",
        f"| Sonnet analyst | {sonnet_calibration['ece']:.3f} | {sonnet_calibration['brier']:.3f} | {sonnet_calibration['auroc']:.3f} | {statistics.mean(float(r.get('confidence') or 0) for r in sonnet_rows.values()):.3f} |",
        "",
        "ECE uses five equal-width bins; AUROC is confidence as a detector of correctness. Small n and one run per case: descriptive only.",
        "",
        "## Latency",
        "",
        "| Arm | Mean ms |",
        "|---|---:|",
        f"| Flash-Lite analyst | {output['latency_ms']['flash_lite_mean']:.0f} |",
        f"| Jev | {output['latency_ms']['jev_mean']:.0f} |",
        f"| Parallel max (no overhead) | {output['latency_ms']['parallel_max_mean']:.0f} (p50 {output['latency_ms']['parallel_max_p50']:.0f}) |",
        "",
        "This is the maximum of saved per-arm latencies, not a measured concurrent run.",
        "",
        "## Disagreements",
        "",
        "| id | expected | analyst | Jev | analyst correct | Jev correct |",
        "|---|---|---|---|---:|---:|",
    ]
    for row in disagreement:
        lines.append(
            f"| {row['id']} | {row['expected']} | {row['analyst']} ({row['analyst_confidence']}) | "
            f"{row['jev']} ({row['jev_confidence']}) | {'yes' if row['analyst_correct'] else 'no'} | "
            f"{'yes' if row['jev_correct'] else 'no'} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        output["interpretation"]["headline"],
        "",
        output["interpretation"]["caveat"],
        "",
        "The agreement gate looks promising but does not establish that it generalises. The next step is a **live shadow mode** that logs both routes on real turns without changing the decision, followed by calibration on a held-out set.",
    ]
    (OUT / "shadow-router.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
