"""Turn the result files in test_results into the statistics the report quotes.

Reads every run that exists (missing runs are skipped), applies the validity screen (a case with zero
model tokens is excluded), and writes test_results/summary.json. No model is called.
"""

from __future__ import annotations

import glob
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(r"C:\Users\Dies Irae\Desktop\HKU\LLM\Pathfinder v5")
RESULTS = Path(__file__).resolve().parent.parent / "test_results"
sys.path.insert(0, str(REPO / "backend"))
for stream in (sys.stdout,):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if not n:
        return [0.0, 0.0]
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value from the discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def calibration(conf: list[float], correct: list[bool], bins: int = 5) -> dict:
    pairs = [(c, k) for c, k in zip(conf, correct) if c is not None]
    if not pairs:
        return {}
    ece = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        group = [(c, k) for c, k in pairs if (lo <= c < hi) or (b == bins - 1 and c == 1.0)]
        if group:
            ece += len(group) / len(pairs) * abs(statistics.mean(c for c, _ in group) - statistics.mean(k for _, k in group))
    brier = statistics.mean((c - k) ** 2 for c, k in pairs)
    pos = [c for c, k in pairs if k]
    neg = [c for c, k in pairs if not k]
    auroc = None
    if pos and neg:
        wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
        auroc = wins / (len(pos) * len(neg))
    return {"n": len(pairs), "ece": round(ece, 3), "brier": round(brier, 3),
            "auroc": round(auroc, 3) if auroc is not None else None}


def median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 1) if values else None


def mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 1) if values else None


def eval_runs(name: str) -> list[dict]:
    path = RESULTS / f"{name}.db"
    if not path.exists():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = [json.loads(r[0]) for r in con.execute("select body from eval_runs order by created_at")]
    finally:
        con.close()
    return rows


def run_cases(name: str, dataset: str) -> list[dict]:
    for run in eval_runs(name):
        if run.get("dataset_id") == dataset:
            return run.get("cases", [])
    return []


def valid(rows: list[dict]) -> list[dict]:
    return [r for r in rows if int((r.get("scores") or {}).get("tokens", 0)) > 0]


def latest(pattern: str):
    files = sorted(glob.glob(str(RESULTS / pattern)))
    return json.loads(Path(files[-1]).read_text(encoding="utf-8")) if files else None


def plan_problems(name: str) -> dict[str, list[str]]:
    """Structural problems left in each saved plan of an evaluation database (validator, no model)."""
    path = RESULTS / f"{name}.db"
    if not path.exists():
        return {}
    from app.domain.models import TripContext, TripPlan
    from app.plan.validator import check_structure

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out: dict[str, list[str]] = {}
    try:
        for sid, token in con.execute("select id, client_token from sessions"):
            if not token.startswith("eval-"):
                continue
            plan_row = con.execute("select body from plans where session_id=? order by version desc limit 1", (sid,)).fetchone()
            ctx_row = con.execute("select body from contexts where session_id=?", (sid,)).fetchone()
            if not plan_row or not ctx_row:
                continue
            try:
                plan = TripPlan(**json.loads(plan_row[0]))
                context = TripContext(**json.loads(ctx_row[0]))
                out[token[5:]] = [p.code for p in check_structure(plan, context)]
            except Exception as exc:  # noqa: BLE001
                out[token[5:]] = [f"unreadable: {exc}"[:80]]
    finally:
        con.close()
    return out


summary: dict = {}

# ---------------------------------------------------------------- routing x3
routing = {}
pooled_conf, pooled_ok = [], []
for r in (1, 2, 3):
    rows = run_cases(f"routing_r{r}", "routing")
    if not rows:
        continue
    ok = [bool(x["passed"]) for x in rows]
    conf = [(x.get("scores") or {}).get("confidence") for x in rows]
    pooled_conf += conf
    pooled_ok += ok
    routing[f"run{r}"] = {"correct": sum(ok), "n": len(rows), "wilson": wilson(sum(ok), len(rows)),
                          "no_tokens": len(rows) - len(valid(rows)),
                          "p50_ms": median(x.get("latency_ms") for x in rows)}
if pooled_ok:
    gate = {}
    for t in (0.6, 0.7, 0.8, 0.9):
        kept = [(c, k) for c, k in zip(pooled_conf, pooled_ok) if c is not None and c >= t]
        gate[str(t)] = {"coverage": round(len(kept) / len(pooled_ok), 3),
                        "accuracy_kept": round(sum(k for _, k in kept) / len(kept), 3) if kept else None}
    routing["calibration"] = calibration(pooled_conf, pooled_ok)
    routing["gate"] = gate
summary["routing"] = routing

# ---------------------------------------------------------------- plan suites x3 and the no-repair ablation
plans: dict = {}
per_case = defaultdict(list)
for r in (1, 2, 3):
    for name, datasets_ in ((f"hk_r{r}", ("hongkong",)), (f"jp_tp_r{r}", ("japan", "travelplanner"))):
        problems = plan_problems(name)
        for ds in datasets_:
            rows = run_cases(name, ds)
            if not rows:
                continue
            v = valid(rows)
            entry = plans.setdefault(ds, {})
            entry[f"run{r}"] = {
                "passed": sum(1 for x in v if x["passed"]), "n": len(rows), "valid": len(v),
                "failures": Counter(x.get("failure_class") for x in v if not x["passed"]),
                "grounding_mean": round(statistics.mean((x.get("scores") or {}).get("grounding", 0) for x in v), 3) if v else None,
                "latency_p50_s": round((median(x.get("latency_ms") for x in v) or 0) / 1000, 1),
                "tokens_mean": mean((x.get("scores") or {}).get("tokens") for x in v),
                "calls_mean": mean((x.get("scores") or {}).get("llm_calls") for x in v),
                "problems_left": sum(len(problems.get(x["case_id"], [])) for x in v),
                "plans_with_problems": sum(1 for x in v if problems.get(x["case_id"])),
            }
            for x in v:
                per_case[(ds, x["case_id"])].append(bool(x["passed"]))
for (ds, case_id), outcomes in per_case.items():
    plans.setdefault(ds, {}).setdefault("per_case", {})[case_id] = outcomes
for ds, entry in plans.items():
    cases = entry.get("per_case", {})
    entry["pass_all_runs"] = sum(1 for o in cases.values() if len(o) == 3 and all(o))
    entry["pass_any_run"] = sum(1 for o in cases.values() if len(o) == 3 and any(o))
    entry["fail_all_runs"] = sum(1 for o in cases.values() if len(o) == 3 and not any(o))
    entry["mixed"] = sum(1 for o in cases.values() if len(o) == 3 and 0 < sum(o) < 3)
    runs = [v for k, v in entry.items() if k.startswith("run")]
    passed = sum(r["passed"] for r in runs)
    tokens = sum((r["tokens_mean"] or 0) * r["valid"] for r in runs)
    entry["tokens_per_passed_plan"] = round(tokens / passed) if passed else None
ablation = {}
for ds in ("hongkong", "japan", "travelplanner"):
    rows = run_cases("norepair", ds)
    base = {x["case_id"]: bool(x["passed"]) for x in valid(run_cases("hk_r1" if ds == "hongkong" else "jp_tp_r1", ds))}
    if not rows or not base:
        continue
    v = {x["case_id"]: x for x in valid(rows)}
    shared = [c for c in v if c in base]
    b = sum(1 for c in shared if base[c] and not v[c]["passed"])
    c_ = sum(1 for c in shared if not base[c] and v[c]["passed"])
    ablation[ds] = {"repair_passed": sum(base[c] for c in shared), "norepair_passed": sum(bool(v[c]["passed"]) for c in shared),
                    "n": len(shared), "mcnemar_p": mcnemar(b, c_),
                    "norepair_latency_p50_s": round((median(v[c].get("latency_ms") for c in shared) or 0) / 1000, 1),
                    "norepair_tokens_mean": mean((v[c].get("scores") or {}).get("tokens") for c in shared)}
summary["plans"] = plans
summary["repair_ablation"] = ablation

# ---------------------------------------------------------------- direct injection, path cost, edit suite
rows = run_cases("injection_direct", "injection")
if rows:
    v = valid(rows)
    fixture = {json.loads(l)["id"]: json.loads(l) for l in (REPO / "backend/eval/fixtures/injection.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    summary["injection_direct"] = {"n": len(rows), "valid": len(v), "contained": sum(1 for x in v if x["passed"]),
                                   "heldout_valid": sum(1 for x in v if fixture[x["case_id"]]["split"] == "heldout"),
                                   "wilson": wilson(sum(1 for x in v if x["passed"]), len(v))}
rows = run_cases("efficiency", "efficiency")
if rows:
    fixture = {json.loads(l)["id"] if l.startswith("{") else None: None for l in []}
    cases = json.loads((REPO / "backend/eval/fixtures/efficiency.json").read_text(encoding="utf-8"))
    intent = {c["id"]: c["expected_intent"] for c in cases}
    by = defaultdict(list)
    for x in valid(rows):
        by[intent.get(x["case_id"], "?")].append(x)
    summary["path_cost"] = {k: {"n": len(v), "calls_mean": mean((x.get("scores") or {}).get("llm_calls") for x in v),
                                "tokens_mean": mean((x.get("scores") or {}).get("tokens") for x in v),
                                "latency_p50_s": round((median(x.get("latency_ms") for x in v) or 0) / 1000, 1)}
                            for k, v in by.items()}
    summary["path_cost"]["no_tokens"] = len(rows) - len(valid(rows))
rows = run_cases("edit", "edit_vs_replan")
if rows:
    v = valid(rows)
    summary["edit_suite"] = {"n": len(rows), "valid": len(v), "passed": sum(1 for x in v if x["passed"]),
                             "preservation_mean": mean((x.get("scores") or {}).get("preservation") for x in v),
                             "calls_mean": mean((x.get("scores") or {}).get("llm_calls") for x in v),
                             "tokens_mean": mean((x.get("scores") or {}).get("tokens") for x in v)}

# ---------------------------------------------------------------- component re-runs
components = {}
for name in ("verifier_r1", "verifier_r2"):
    path = RESULTS / f"{name}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("rows") or data.get("cases") or []
        seen, dedup = set(), []
        for row in rows:
            key = (row.get("constraint"), row.get("mutation") if isinstance(row.get("mutation"), str) else json.dumps(row.get("mutation"), sort_keys=True))
            if key not in seen:
                seen.add(key)
                dedup.append(row)
        components[name] = {"correct_20": sum(1 for r in rows if r.get("correct")), "n_20": len(rows),
                            "correct_dedup": sum(1 for r in dedup if r.get("correct")), "n_dedup": len(dedup),
                            "false_pass": sum(1 for r in dedup if r.get("false_pass")),
                            "false_alarm": sum(1 for r in dedup if r.get("false_alarm")),
                            "errors": sum(1 for r in rows if r.get("error"))}
for name in ("skills_ablation", "editor_temperature"):
    path = RESULTS / f"{name}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        components[name] = {arm: {k: v for k, v in body.items() if k in ("correct", "n", "accuracy", "p50_latency_ms", "mean_tokens")}
                            for arm, body in (data.get("arms") or {}).items()}
summary["components"] = components

# ---------------------------------------------------------------- batch B
data = latest("indirect_injection-*.json")
if data:
    rows = [r for r in data["rows"] if r.get("valid")]
    summary["injection_indirect"] = {
        **data["summary"],
        "needs_human_check": sum(1 for r in rows if r.get("needs_human_check")),
        "by_attack": {a: {"n": sum(1 for r in rows if r["attack"] == a), "leaks": sum(1 for r in rows if r["attack"] == a and r["leak"]),
                          "flagged": sum(1 for r in rows if r["attack"] == a and r["attacker_strings_in_reply"])}
                      for a in sorted({r["attack"] for r in rows})},
    }
data = latest("paired_edit_replan-*.json")
if data:
    rows = [r for r in data["rows"] if r.get("valid")]

    def arm(name, key):
        return [r[name][key] for r in rows]
    summary["edit_vs_replan"] = {
        "n": len(data["rows"]), "valid": len(rows),
        "edit": {"calls_mean": mean(arm("edit", "calls")), "tokens_mean": mean(arm("edit", "tokens")),
                 "latency_p50_s": round((median(arm("edit", "latency_ms")) or 0) / 1000, 1),
                 "kept_share_mean": round(statistics.mean(arm("edit", "kept_share")), 3) if rows else None,
                 "change_applied": sum(arm("edit", "change_applied"))},
        "replan": {"calls_mean": mean(arm("replan", "calls")), "tokens_mean": mean(arm("replan", "tokens")),
                   "latency_p50_s": round((median(arm("replan", "latency_ms")) or 0) / 1000, 1),
                   "kept_share_mean": round(statistics.mean(arm("replan", "kept_share")), 3) if rows else None,
                   "change_applied": sum(arm("replan", "change_applied"))},
        "edit_cheaper_in_tokens": sum(1 for r in rows if r["edit"]["tokens"] < r["replan"]["tokens"]),
        "edit_faster": sum(1 for r in rows if r["edit"]["latency_ms"] < r["replan"]["latency_ms"]),
        "per_case": [{"id": r["id"], "input": r["input"], "edit": {k: r["edit"][k] for k in ("calls", "tokens", "latency_ms", "kept_share", "change_applied")},
                      "replan": {k: r["replan"][k] for k in ("calls", "tokens", "latency_ms", "kept_share", "change_applied")}} for r in rows],
    }
data = latest("reasoning_ablation-*.json")
if data:
    summary["reasoning"] = {k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in data["arms"].items()}
    summary["reasoning"]["model"] = data.get("model")

# ---------------------------------------------------------------- batch C
data = latest("baseline_single_llm-*.json")
if data:
    rows = [r for r in data["rows"] if r.get("valid")]
    arms = {}
    for name in ("chat_only", "single_call"):
        a = [r for r in rows if r["arm"] == name]
        if not a:
            continue
        arms[name] = {
            "n": len(a), "passed": sum(1 for r in a if r["passed"]), "wilson": wilson(sum(1 for r in a if r["passed"]), len(a)),
            "parsed": sum(1 for r in a if r["parsed"]), "grounded_mean": round(statistics.mean(r["grounded_share"] for r in a), 3),
            "fully_grounded": sum(1 for r in a if r["grounded_share"] >= 0.99),
            "problems_mean": round(statistics.mean(len(r["problems"]) for r in a), 2),
            "plans_with_problems": sum(1 for r in a if r["problems"]),
            "problem_codes": Counter(code for r in a for code in r["problems"]).most_common(8),
            "empty_days": sum(r["empty_days"] for r in a),
            "constraints_met": sum(r["constraints_met"] or 0 for r in a), "constraints_total": sum(r["constraints_total"] or 0 for r in a),
            "disruption_handled": f"{sum(1 for r in a if r['disruption_handled'])}/{sum(1 for r in a if r['disruption_handled'] is not None)}",
            "budget_over": sum(1 for r in a if r["budget_status"] == "over"),
            "failures": Counter(r["failure"] for r in a if not r["passed"]).most_common(6),
            "latency_p50_s": round((median(r["latency_ms"] for r in a) or 0) / 1000, 1),
            "tokens_mean": mean(r["tokens"] for r in a),
        }
    summary["baseline"] = arms
    # Pathfinder on the same cases, first repeat, for a paired comparison with the single-call arm.
    pf = {}
    for ds, name in (("hongkong", "hk_r1"), ("japan", "jp_tp_r1"), ("travelplanner", "jp_tp_r1")):
        for x in valid(run_cases(name, ds)):
            pf[(ds, x["case_id"])] = bool(x["passed"])
    single = {(r["suite"], r["case"]): r["passed"] for r in rows if r["arm"] == "single_call" and r["repeat"] == 1}
    shared = [k for k in single if k in pf]
    if shared:
        b = sum(1 for k in shared if pf[k] and not single[k])
        c_ = sum(1 for k in shared if not pf[k] and single[k])
        summary["baseline_paired"] = {"n": len(shared), "pathfinder_passed": sum(pf[k] for k in shared),
                                      "single_call_passed": sum(single[k] for k in shared), "mcnemar_p": mcnemar(b, c_)}
data = latest("boundary_suite-*.json")
if data:
    rows = data["rows"]
    summary["boundary"] = {
        "n": len(rows), "met": sum(1 for r in rows if r["meets_expectation"]),
        "by_category": {c: f"{sum(1 for r in rows if r['category'] == c and r['meets_expectation'])}/{sum(1 for r in rows if r['category'] == c)}"
                        for c in ("representative", "challenging", "failure")},
        "single_llm_wrote_itinerary": sum(1 for r in rows if (r.get("single_llm") or {}).get("wrote_itinerary")),
        "single_llm_n": sum(1 for r in rows if r.get("single_llm")),
        "rows": [{k: r[k] for k in ("category", "label", "expect", "route", "clarify", "plan", "days", "days_expected", "empty_days",
                                    "stops", "grounded_share", "gaps", "budget_status", "handoff", "leak", "pii_in_storage", "calls",
                                    "latency_ms", "meets_expectation")} | {"single_llm_wrote_itinerary": (r.get("single_llm") or {}).get("wrote_itinerary")}
                 for r in rows],
    }
data = latest("travelplanner_sample-*.json")
if data:
    rows = data["rows"]
    by_level = defaultdict(list)
    for r in rows:
        by_level[r["level"]].append(r)
    in_corpus = [r for r in rows if r["stratum"][-1] == "corpus"]
    summary["travelplanner"] = {
        "n": len(rows), "plans": sum(1 for r in rows if r["plan"]), "clarified": sum(1 for r in rows if r["clarify"]),
        "days_match": sum(1 for r in rows if r["plan"] and r["days"] == r["days_requested"]),
        "no_empty_days": sum(1 for r in rows if r["plan"] and r["empty_days"] == 0),
        "fully_grounded": sum(1 for r in rows if r["plan"] and r["grounded_share"] >= 0.99),
        "stops_median": median(r["stops"] for r in rows if r["plan"]),
        "corpus_cities": {"n": len(in_corpus), "plans": sum(1 for r in in_corpus if r["plan"]),
                          "stops_median": median(r["stops"] for r in in_corpus if r["plan"])},
        "by_level": {k: f"{sum(1 for r in v if r['plan'])}/{len(v)}" for k, v in by_level.items()},
        "budget_over": sum(1 for r in rows if r["budget_status"] == "over"),
        "latency_p50_s": round((median(r["latency_ms"] for r in rows) or 0) / 1000, 1),
        "errors": sum(1 for r in rows if r["error"]),
        "no_model_call": sum(1 for r in rows if not r["calls"]),
    }
data = latest("jev_pregate-*.json")
if data:
    summary["jev"] = data["summary"]

# ---------------------------------------------------------------- batch D: skill ablation
data = latest("skills_ablation-*.json")
if data:
    skills = {}
    for part in ("analyst", "editor", "verifier"):
        block = data.get(part)
        if not block:
            continue
        rows = [r for r in block["rows"] if r.get("valid")]
        full = {r["id"]: r["correct"] for r in rows if r["condition"] == "full"}
        bare = {r["id"]: r["correct"] for r in rows if r["condition"] == "minimal"}
        shared = [k for k in full if k in bare]
        lost = sum(1 for k in shared if full[k] and not bare[k])
        gained = sum(1 for k in shared if not full[k] and bare[k])
        skills[part] = {"full": block["full"], "minimal": block["minimal"], "paired_n": len(shared),
                        "right_only_with_skill": lost, "right_only_without_skill": gained,
                        "mcnemar_p": mcnemar(lost, gained)}
        if part == "analyst":
            skills[part]["by_intent"] = block.get("by_intent")
        if part == "verifier":
            skills[part]["false_pass"] = {c: block[c].get("false_pass") for c in ("full", "minimal")}
            skills[part]["false_alarm"] = {c: block[c].get("false_alarm") for c in ("full", "minimal")}
    block = data.get("planner")
    if block:
        arms = {}
        for condition in ("full", "minimal"):
            cases = [c for c in block[condition]["cases"] if c.get("valid")]
            problems = plan_problems(f"skills_planner_{condition}")
            arms[condition] = {
                "n": len(block[condition]["cases"]), "valid": len(cases), "passed": sum(1 for c in cases if c["passed"]),
                "failures": Counter(c["failure"] for c in cases if not c["passed"]).most_common(6),
                "grounding_mean": round(statistics.mean(c["grounding"] or 0 for c in cases), 3) if cases else None,
                "constraints_met": sum(c["constraints_met"] or 0 for c in cases),
                "constraints_total": sum(c["constraints_total"] or 0 for c in cases),
                "calls_mean": mean(c["calls"] for c in cases), "tokens_mean": mean(c["tokens"] for c in cases),
                "latency_p50_s": round((median(c["latency_ms"] for c in cases) or 0) / 1000, 1),
                "problems_left": sum(len(v) for v in problems.values()),
                "plans_with_problems": sum(1 for v in problems.values() if v),
                "problem_codes": Counter(code for v in problems.values() for code in v).most_common(6),
                "by_suite": {s: f"{sum(1 for c in cases if c['suite'] == s and c['passed'])}/{sum(1 for c in cases if c['suite'] == s)}"
                             for s in ("hongkong", "japan", "travelplanner")},
            }
        full = {(c["suite"], c["case"]): c["passed"] for c in block["full"]["cases"] if c.get("valid")}
        bare = {(c["suite"], c["case"]): c["passed"] for c in block["minimal"]["cases"] if c.get("valid")}
        shared = [k for k in full if k in bare]
        lost = sum(1 for k in shared if full[k] and not bare[k])
        gained = sum(1 for k in shared if not full[k] and bare[k])
        skills["planner"] = {**arms, "paired_n": len(shared), "right_only_with_skill": lost,
                             "right_only_without_skill": gained, "mcnemar_p": mcnemar(lost, gained)}
    summary["skills"] = skills

out = RESULTS / "summary.json"
out.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print(json.dumps(summary, indent=1, ensure_ascii=False, default=str)[:20000])
print("written", out)
