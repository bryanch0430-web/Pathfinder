"""Generate the v5 part-model study Markdown and Excel deliverables."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from v5_workbook import write_xls, write_xlsx

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "docs" / "eval-artifacts" / "v5-model-study"
FIX = ROOT / "backend" / "eval" / "fixtures"


def load(name: str):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0, 0
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / den
    return centre - margin, centre + margin


routing = load("routing-merged.json")
hybrid = load("hybrid-router.json")
datasets = load("datasets-latest.json")
editor = load("editor-direct-latest.json")
verifier = load("verifier-latest.json")
ask = load("ask-adjudicated.json")
memory = load("memory-latest.json")
cap = load("cap-stress-latest.json")

# ---- cases -----------------------------------------------------------
routing_cases = [
    {"part": "analyst", "id": row["id"], "input": row["input"], "gold": row["expected"], "check": "intent equality"}
    for row in routing["cases"]["sonnet-0.0"]
]
planner_cases = []
for dataset, arms in datasets["datasets"].items():
    first = next(iter(arms.values()))
    for row in first["cases"]:
        planner_cases.append({"part": "planner", "id": row["case_id"], "input": row["output_summary"][:200], "gold": f"{dataset}: pass/fail", "check": "constraints/grounding"})
editor_cases = [
    {"part": "editor", "id": row["id"], "input": row["input"], "gold": row["check"], "check": row["check"]}
    for row in editor["arms"]["sonnet"]["rows"]
]
verifier_cases = [
    {"part": "verifier", "id": row["id"], "input": row["constraint"], "gold": row["gold"], "check": "audit status"}
    for row in verifier["arms"]["sonnet"]["rows"]
]
ask_cases = [
    {"part": "ask", "id": row["id"], "input": row["question"], "gold": "rubric", "check": "adjudicated in-scope"}
    for row in ask["arms"]["sonnet"]["rows"]
]
memory_cases = [
    {"part": "memory", "id": row["id"], "input": "; ".join(row["preferences"])[:200], "gold": "; ".join(row["gold"]), "check": "preference recall"}
    for row in memory["arms"]["sonnet"]["rows"]
]
cap_cases = [
    {"part": "cap", "id": part, "input": f"low {rows['low']['cap']} / effective {rows['effective']['cap']}", "gold": "pass", "check": "binding"}
    for part, rows in cap.items()
]
all_cases = routing_cases + planner_cases + editor_cases + verifier_cases + ask_cases + memory_cases + cap_cases

# ---- workbook --------------------------------------------------------
def sheet(title, rows, widths=None):
    return {"title": title, "rows": rows, "widths": widths}


def table(rows):
    if not rows:
        return []
    return [list(rows[0].keys())] + [[row.get(key, "") for key in rows[0]] for row in rows]


routing_summary = []
for key, data in routing["summaries"].items():
    routing_summary.append(
        {
            "arm": key,
            "model": data["arm"].get("model"),
            "temperature": data["arm"].get("temperature"),
            "correct": data["correct"],
            "n": data["n"],
            "accuracy": f"{data['accuracy']:.1%}",
            "wilson_low": f"{data['wilson95'][0]:.1%}",
            "wilson_high": f"{data['wilson95'][1]:.1%}",
            "p50_ms": data["p50_latency_ms"],
            "mean_tokens": data["mean_tokens"],
        }
    )

planner_summary = []
for dataset, arms in datasets["datasets"].items():
    for arm, run in arms.items():
        metrics = {metric["label"]: metric["value"] for metric in run["metrics"]}
        planner_summary.append(
            {
                "dataset": dataset,
                "arm": arm,
                "model": run["model"],
                "passed": run["passed"],
                "n": run["case_count"],
                "latency_s": round(run["latency_ms"] / 1000, 1),
                **metrics,
            }
        )

editor_summary = [
    {
        "arm": arm,
        "model": data["model"],
        "correct": data["correct"],
        "n": data["n"],
        "p50_ms": data["p50_latency_ms"],
        "mean_tokens": data["mean_tokens"],
    }
    for arm, data in editor["arms"].items()
]
verifier_summary = [
    {
        "arm": arm,
        "model": data["model"],
        "correct": data["correct"],
        "n": data["n"],
        "false_passes": data["false_passes"],
        "false_alarms": data["false_alarms"],
        "p50_ms": data["p50_latency_ms"],
    }
    for arm, data in verifier["arms"].items()
]
ask_summary = [
    {
        "arm": arm,
        "model": data["model"],
        "in_scope_correct": data["in_scope_correct"],
        "in_scope_n": data["in_scope_n"],
        "in_scope_accuracy": f"{(data['in_scope_correct'] / data['in_scope_n']):.1%}",
        "scope_cases": data["scope_n"],
        "p50_ms": data["p50_latency_ms"],
    }
    for arm, data in ask["arms"].items()
]
memory_summary = []
for arm, data in memory["arms"].items():
    invented = sum(1 for row in data["rows"] if row["gold"] == [] and row["preferences"])
    memory_summary.append(
        {
            "arm": arm,
            "model": data["model"],
            "mean_recall": f"{data['mean_recall']:.1%}",
            "no_fact_cases_with_invented_preference": invented,
            "p50_ms": data["p50_latency_ms"],
        }
    )
cap_summary = []
for part, rows in cap.items():
    cap_summary.append(
        {
            "part": part,
            "low_cap": rows["low"]["cap"],
            "low_ok": "PASS" if rows["low"]["ok"] else "FAIL",
            "low_detail": rows["low"]["detail"][:120],
            "effective_cap": rows["effective"]["cap"],
            "effective_ok": "PASS" if rows["effective"]["ok"] else "FAIL",
            "effective_detail": rows["effective"]["detail"][:120],
        }
    )

recommendations = [
    {
        "part": "Analyst / understanding",
        "choice": "Gemini 3.5 Flash-Lite",
        "temperature": "0",
        "cap": "4,096",
        "reasoning": "none (not supported by these Poe models)",
        "threshold": "0.8 (0.9 when false routes are costlier)",
        "evidence": "83/84 = 98.8% (Wilson 93.6–99.8), p50 4.1 s, 2.9k tokens; Sonnet 98.8% / 6.3 s / 4.6k. Sonnet 0 vs 0.2: identical 83/84.",
        "tradeoff": "Flash-Lite ties the best accuracy and is faster/cheaper; Sonnet has no accuracy gain here. DeepSeek V4.1 Flash also scored 98.8% but was the slowest (8.5 s).",
    },
    {
        "part": "Turn router",
        "choice": "LLM router (Flash-Lite) default; Jev as optional fast pre-gate",
        "temperature": "0",
        "cap": "4,096",
        "reasoning": "none",
        "threshold": "0.8 for the LLM; 0.8 for a Jev pre-gate",
        "evidence": "Jev alone 79/84 = 94.0%, p50 0.6 s; at 0.8 it keeps 66% at 100% and escalates 29. Jev→Flash fallback: 84/84 = 100%, ~2.0 s mean. Jev→Sonnet: 83/84, ~3.3 s.",
        "tradeoff": "Pure Jev is fastest but weaker on modify/ask. The hybrid is best but adds a second call path; it is not a shipped setting yet.",
    },
    {
        "part": "Planner",
        "choice": "Gemini 3.5 Flash-Lite",
        "temperature": "0.2 (production setting; not swept)",
        "cap": "12,000",
        "reasoning": "none",
        "threshold": "—",
        "evidence": "14/17 each (82.4%). Hong Kong constraints 85.7% vs 68.8%; Japan 6/6 both; TravelPlanner 2/3 vs 3/3. Flash 323 s total vs Sonnet 920 s.",
        "tradeoff": "Small n (17) and one run. Flash ties overall and is much faster; Sonnet is stronger on TravelPlanner-style constraints. Keep the 12k cap: planner JSON needs it.",
    },
    {
        "part": "Editor",
        "choice": "Gemini 3.5 Flash-Lite",
        "temperature": "0",
        "cap": "2,000",
        "reasoning": "none",
        "threshold": "—",
        "evidence": "8/8 for all three; p50 3.4 s (Flash) vs 5.6 s (Sonnet) and 6.6 s (DeepSeek); mean tokens 3.4k vs 11.6k (Sonnet) and 3.1k (DeepSeek).",
        "tradeoff": "All three pass the 8-case set; Flash is fastest, DeepSeek has the fewest tokens but is slower. Flash wins on the pre-declared latency tie-break.",
    },
    {
        "part": "Verifier",
        "choice": "DeepSeek V4 Flash",
        "temperature": "0",
        "cap": "1,500",
        "reasoning": "none",
        "threshold": "—",
        "evidence": "18/20, 2 false passes, p50 4.0 s; Sonnet 18/20, 2 false passes, 5.7 s; Flash 17/20, 3 false passes, 3.4 s.",
        "tradeoff": "All models missed transfer arithmetic (v06/v14); this is a verifier coverage gap, not only a model choice. DeepSeek matches Sonnet with lower latency. Use code checks for arithmetic/transfers.",
    },
    {
        "part": "Ask / quick answers",
        "choice": "DeepSeek V4 Flash",
        "temperature": "0",
        "cap": "1,400",
        "reasoning": "none",
        "threshold": "—",
        "evidence": "11/11 in-scope (100%), p50 16.1 s; Sonnet 11/11 / 20.2 s; Flash 10/11 / 14.4 s. Off-topic q11 was a scope case for all three and is excluded.",
        "tradeoff": "DeepSeek matches Sonnet with lower latency. Ask calls are slow (15–20 s) because the path re-reads the plan; that is a separate latency issue.",
    },
    {
        "part": "Memory",
        "choice": "DeepSeek V4 Flash with a no-invention check; Sonnet if precision is the priority",
        "temperature": "0",
        "cap": "700",
        "reasoning": "none",
        "threshold": "—",
        "evidence": "DeepSeek recall 91.7% vs Sonnet/Flash 79.8%; but DeepSeek invented preferences on the one 'nothing to remember' case (m06). Sonnet/Flash did not.",
        "tradeoff": "Best recall vs one false memory in 8 cases. A write-time validation (must be supported by the message/plan) is required before using DeepSeek.",
    },
]

# ---- Markdown results ------------------------------------------------
lines = [
    "# v5 part-model study — results",
    "",
    f"Generated: {datetime.now().isoformat(timespec='seconds')} (Asia/Hong_Kong)",
    "",
    "## Executive summary",
    "",
    "| Part | Chosen | Temp | Cap | Reasoning | Threshold |",
    "|---|---|---:|---:|---|---|",
]
for row in recommendations:
    lines.append(
        f"| {row['part']} | {row['choice']} | {row['temperature']} | {row['cap']} | {row['reasoning']} | {row['threshold']} |"
    )
lines += [
    "",
    "The full protocol and case catalogue are in `TEST_PLAN.md`; the spreadsheet is `v5_model_study.xlsx` (formatted) and `v5_model_study.xls` (compatibility).",
    "",
    "## 1. Analyst / router",
    "",
    "| Arm | Model | Temp | Correct | Accuracy | Wilson 95% | p50 ms | Mean tokens |",
    "|---|---|---:|---:|---:|---|---:|---:|",
]
for row in routing_summary:
    lines.append(
        f"| {row['arm']} | {row['model']} | {row['temperature']} | {row['correct']}/{row['n']} | {row['accuracy']} | "
        f"{row['wilson_low']}–{row['wilson_high']} | {row['p50_ms']} | {row['mean_tokens']} |"
    )
lines += [
    "",
    "### Confidence gate",
    "",
    "| Arm | cutoff | coverage | accuracy on kept | escalated |",
    "|---|---:|---:|---:|---:|",
]
for arm, data in routing["summaries"].items():
    for gate in data["gates"]:
        lines.append(
            f"| {arm} | {gate['cutoff']} | {gate['coverage']:.0%} | "
            f"{gate['accuracy_on_kept']:.1%} | {gate['escalated']} |"
        )
lines += [
    "",
    "### Jev pre-gate + LLM fallback",
    "",
    "| Hybrid | Correct | Accuracy | Jev coverage | LLM fallbacks | Mean latency (sequential) |",
    "|---|---:|---:|---:|---:|---:|",
]
for key, data in hybrid.items():
    lines.append(
        f"| {key} | {data['correct']}/{data['n']} | {data['accuracy']:.1%} | {data['jev_coverage']:.0%} | "
        f"{data['llm_fallbacks']} | {data['mean_latency_ms']:.0f} ms |"
    )
lines += [
    "",
    "The hybrid is sequential: Jev answers when confidence ≥ 0.8, otherwise the analyst is called. This is an option analysis, not a shipped setting.",
    "",
    "## 2. Planner",
    "",
    "| Dataset | Arm | Model | Passed | Constraints | Grounding | Latency (s) |",
    "|---|---|---|---:|---:|---:|---:|",
]
for row in planner_summary:
    lines.append(
        f"| {row['dataset']} | {row['arm']} | {row['model']} | {row['passed']}/{row['n']} | "
        f"{row.get('Constraints met', '—')} | {row.get('Grounded plans', row.get('Disruptions handled', '—'))} | {row['latency_s']} |"
    )
lines += [
    "",
    "Sonnet total 14/17 (82.4%); Flash total 14/17 (82.4%). Flash is much faster; Sonnet is 3/3 on TravelPlanner while Flash is 2/3.",
    "",
    "## 3. Editor",
    "",
    "| Arm | Model | Correct | p50 ms | Mean tokens |",
    "|---|---|---:|---:|---:|",
]
for row in editor_summary:
    lines.append(f"| {row['arm']} | {row['model']} | {row['correct']}/{row['n']} | {row['p50_ms']} | {row['mean_tokens']} |")
lines += [
    "",
    "The editor test uses four frozen plans (remove, move, time change, remove) and the same prompts for each arm. It does not include add/research edits.",
    "",
    "## 4. Verifier",
    "",
    "| Arm | Model | Correct | False pass | False alarm | p50 ms |",
    "|---|---|---:|---:|---:|---:|",
]
for row in verifier_summary:
    lines.append(
        f"| {row['arm']} | {row['model']} | {row['correct']}/{row['n']} | {row['false_passes']} | {row['false_alarms']} | {row['p50_ms']} |"
    )
lines += [
    "",
    "All arms missed v06 (a 10-minute transfer against a 30-minute constraint). The validator now receives source lines (`plan_text(with_sources=True)`) so source constraints can be audited.",
    "",
    "## 5. Ask / quick answers",
    "",
    "| Arm | Model | In-scope correct | Accuracy | p50 ms |",
    "|---|---|---:|---:|---:|",
]
for row in ask_summary:
    lines.append(f"| {row['arm']} | {row['model']} | {row['in_scope_correct']}/{row['in_scope_n']} | {row['in_scope_accuracy']} | {row['p50_ms']} |")
lines += [
    "",
    "q11 (capital of France) is a scope case: all three clarified instead of answering. It is excluded from the in-scope denominator and reported as a product scope limitation.",
    "",
    "## 6. Memory",
    "",
    "| Arm | Model | Mean recall | No-fact cases with invented prefs | p50 ms |",
    "|---|---|---:|---:|---:|",
]
for row in memory_summary:
    lines.append(f"| {row['arm']} | {row['model']} | {row['mean_recall']} | {row['no_fact_cases_with_invented_preference']} | {row['p50_ms']} |")
lines += [
    "",
    "## 7. Cap stress",
    "",
    "| Part | Low cap | Low result | Effective cap | Effective result |",
    "|---|---:|---|---:|---|",
]
for row in cap_summary:
    lines.append(
        f"| {row['part']} | {row['low_cap']} | {row['low_ok']} — {row['low_detail']} | {row['effective_cap']} | {row['effective_ok']} — {row['effective_detail']} |"
    )
lines += [
    "",
    "The ask and planner low-cap controls were inconclusive because the control turn itself clarified/failed; they are reported, not used to claim causality. Verifier and memory show the expected direction.",
    "",
    "## 8. Failures and limitations",
    "",
    "- One run per arm; temperature > 0 was not repeated except the router Sonnet 0.2 arm.",
    "- GPT-5/o-series could not run on the available Poe endpoint (400 upstream gateway failure); Gemini 3.8 Flash returned an empty smoke answer. DeepSeek's full routing arm timed out under concurrency 4 and is excluded from routing; it ran in editor/verifier/ask/memory.",
    "- Editor n=8, verifier n=20, ask n=11 in-scope, memory n=8. These are exploratory, not powered for 5-point claims.",
    "- Reasoning effort is not sent to Claude/Gemini/DeepSeek by the client; no reasoning arm is possible on this provider.",
    "- Planner temperature was not swept; 0.2 is the production setting.",
    "- Ask latency is high (15–20 s) for all models; model choice does not fix the ask path's latency.",
    "- The verifier's transfer-arithmetic weakness remains; code checks are still required.",
    "- The Jev hybrid is a derived result from saved per-case predictions, not a live end-to-end run.",
    "",
    "## 9. Trace / code map",
    "",
    "See `TRACE_INDEX.md` for the claim → code path table and sample trace IDs.",
]
(OUT / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---- raw per-case sheets ---------------------------------------------
routing_rows = []
for arm, rows in routing["cases"].items():
    for row in rows:
        routing_rows.append({
            "arm": arm, "id": row["id"], "input": row["input"], "expected": row["expected"],
            "predicted": row["predicted"], "correct": row["correct"], "confidence": row.get("confidence"),
            "latency_ms": row.get("latency_ms"), "tokens": row.get("tokens"), "error": row.get("error"),
        })

planner_inputs = {}
for filename in ("hongkong.json", "japan.json"):
    for row in json.loads((FIX / filename).read_text(encoding="utf-8")):
        planner_inputs[row["id"]] = row["input"]
planner_rows = []
for dataset, arms in datasets["datasets"].items():
    for arm, run in arms.items():
        for row in run["cases"]:
            planner_rows.append({
                "dataset": dataset, "arm": arm, "id": row["case_id"],
                "input": planner_inputs.get(row["case_id"], ""),
                "passed": row["passed"], "failure": row.get("failure_class") or "",
                "constraints": (row.get("scores") or {}).get("constraints_met", ""),
                "grounding": (row.get("scores") or {}).get("grounding", ""),
                "latency_ms": row.get("latency_ms"),
            })

editor_cases_rows = []
for arm, data in editor["arms"].items():
    for row in data["rows"]:
        editor_cases_rows.append({"arm": arm, **{k: row.get(k, "") for k in ("id", "input", "passed", "summary", "latency_ms", "tokens", "error")}})

verifier_case_rows = []
for arm, data in verifier["arms"].items():
    for row in data["rows"]:
        verifier_case_rows.append({"arm": arm, **{k: row.get(k, "") for k in ("id", "constraint", "mutation", "gold", "predicted", "correct", "false_pass", "false_alarm", "latency_ms")}})

ask_case_rows = []
for arm, data in ask["arms"].items():
    for row in data["rows"]:
        ask_case_rows.append({"arm": arm, **{k: row.get(k, "") for k in ("id", "question", "reply", "correct_adjudicated", "in_scope", "latency_ms")}})

memory_case_rows = []
for arm, data in memory["arms"].items():
    for row in data["rows"]:
        memory_case_rows.append({"arm": arm, **{k: row.get(k, "") for k in ("id", "preferences", "gold", "hits", "recall", "error")}})

# ---- workbook --------------------------------------------------------
sheets = [
    sheet("README", [
        ["v5 part-model study"],
        ["Generated", datetime.now().isoformat(timespec="seconds")],
        ["Code", "Pathfinder v5 backend; commit 08361dd + study harness"],
        ["Provider", "Poe OpenAI-compatible endpoint"],
        ["Sandbox", "PATHFINDER_SANDBOX=1"],
        ["Protocol", "One run per arm; same frozen cases; missing/error = wrong; Wilson 95%; exact McNemar for routing."],
        ["Results", "RESULTS.md / PART_MODEL_DECISIONS.md / TRACE_INDEX.md"],
    ], [30, 90]),
    sheet("Protocol", [
        ["Item", "Value"],
        ["Models", "claude-sonnet-5.5; gemini-3.5-flash-lite; deepseek-v4-flash; jev-latest"],
        ["Unavailable", "gpt-5-nano/mini (Poe 400); gemini-3.8-flash (empty); deepseek routing (timeout at c=4)"],
        ["Routing", "84 frozen utterances; 21 each plan/modify/ask/unclear"],
        ["Planner", "Hong Kong 8 + Japan 6"],
        ["Editor", "4 frozen plans: remove, move, time, remove"],
        ["Verifier", "10 plans: 5 good + 5 injected defects"],
        ["Ask", "12 questions; q11 is a separate scope case"],
        ["Memory", "8 synthetic traveller histories"],
        ["Statistics", "Wilson 95%; exact McNemar for routing; descriptive for small-n parts"],
    ], [30, 100]),
    sheet("Recommendations", table(recommendations), [20, 30, 12, 10, 30, 18, 80, 70]),
    sheet("Cases", table(all_cases), [12, 10, 80, 30, 40]),
    sheet("Routing_Cases", table(routing_rows), [16, 8, 70, 12, 12, 8, 10, 12, 10, 40]),
    sheet("Planner_Cases", table(planner_rows), [12, 10, 8, 60, 8, 16, 12, 12, 12]),
    sheet("Editor_Cases", table(editor_cases_rows), [10, 8, 50, 8, 50, 10, 10, 30]),
    sheet("Verifier_Cases", table(verifier_case_rows), [10, 8, 50, 18, 8, 8, 8, 10, 10, 10]),
    sheet("Ask_Cases", table(ask_case_rows), [10, 8, 45, 80, 12, 8, 12]),
    sheet("Memory_Cases", table(memory_case_rows), [10, 8, 80, 50, 30, 10, 30]),
    sheet("Routing", table(routing_summary), [18, 24, 12, 10, 8, 10, 12, 12, 12, 12]),
    sheet("Router_Gate", table([
        {"arm": arm, "cutoff": gate["cutoff"], "coverage": gate["coverage"], "accuracy_on_kept": gate["accuracy_on_kept"], "escalated": gate["escalated"]}
        for arm, data in routing["summaries"].items() for gate in data["gates"]
    ]), [18, 10, 12, 18, 12]),
    sheet("Hybrid", table([
        {"hybrid": key, "correct": data["correct"], "n": data["n"], "accuracy": data["accuracy"], "jev_coverage": data["jev_coverage"], "fallbacks": data["llm_fallbacks"], "mean_latency_ms": data["mean_latency_ms"]}
        for key, data in hybrid.items()
    ]), [24, 10, 8, 10, 14, 12, 16]),
    sheet("Planner", table(planner_summary), [14, 12, 26, 10, 8, 14, 16, 14, 14]),
    sheet("Editor", table(editor_summary), [12, 26, 10, 8, 12, 14]),
    sheet("Verifier", table(verifier_summary), [12, 26, 10, 8, 12, 12, 12]),
    sheet("Ask", table(ask_summary), [12, 26, 16, 10, 14, 12, 12]),
    sheet("Memory", table(memory_summary), [12, 26, 12, 28, 12]),
    sheet("Cap_Stress", table(cap_summary), [14, 12, 10, 50, 14, 14, 50]),
    sheet("Failures", table([
        {"part": "routing", "case": "r68", "arm": "sonnet/flash", "issue": "one unclear case misclassified", "detail": "same failure in both"},
        {"part": "planner", "case": "hk01", "arm": "sonnet", "issue": "constraint", "detail": "failed; Flash passed"},
        {"part": "planner", "case": "hk03", "arm": "sonnet+flash", "issue": "constraint", "detail": "both failed"},
        {"part": "planner", "case": "hk06", "arm": "sonnet", "issue": "constraint", "detail": "failed; Flash passed"},
        {"part": "verifier", "case": "v06", "arm": "all", "issue": "false pass", "detail": "10-minute transfer vs 30-minute constraint"},
        {"part": "ask", "case": "q11", "arm": "all", "issue": "scope", "detail": "off-topic question clarified, not answered"},
        {"part": "memory", "case": "m06", "arm": "deepseek", "issue": "false memory", "detail": "preferences invented for a no-fact history"},
    ]), [12, 12, 16, 30, 80]),
    sheet("Traces", [
        ["Claim", "Code path", "Sample trace / evidence"],
        ["Router chooses one path", "app/understand/pipeline.py:understand", "routing per-case rows"],
        ["Jev typed route", "app/understand/jev.py:jev_route", "routing-latest.json"],
        ["Planner composes", "app/plan/builder.py:build_plan", "datasets-latest.json"],
        ["Editor proposes/applies", "app/plan/editor.py:propose_edit", "editor-direct-latest.json"],
        ["Verifier audits", "app/plan/validator.py:audit_constraints", "verifier-latest.json"],
        ["Ask answers from plan", "app/turn/orchestrator.py ask path", "ask-adjudicated.json"],
        ["Memory extraction", "app/application/memory.py:derive_memory", "memory-latest.json"],
        ["Caps", "app/core/llm.py:PURPOSE_MAX_TOKENS/max_tokens_for", "cap-stress-latest.json"],
        ["Temperature", "app/core/llm.py:temperature_for", "routing sonnet-0.0 vs 0.2"],
        ["Threshold", "settings.router_threshold; pipeline", "Router_Gate sheet"],
    ], [32, 55, 45]),
    sheet("Limits", [
        ["Limit"],
        ["Small n for editor/verifier/ask/memory; exploratory only."],
        ["One run per arm; no repeated sampling for most temperature>0."],
        ["GPT-5/o-series unavailable; reasoning effort untested."],
        ["DeepSeek routing arm excluded after timeout."],
        ["Planner temperature not swept."],
        ["Jev hybrid derived from saved predictions, not live end-to-end."],
        ["Ask q11 scope handling is a product issue, not counted in accuracy."],
        ["Verifier misses transfer arithmetic; code checks remain."],
    ], [120]),
]

write_xlsx(OUT / "v5_model_study.xlsx", sheets)
write_xls(OUT / "v5_model_study.xls", sheets)
print("wrote", OUT / "RESULTS.md", "and workbook")
