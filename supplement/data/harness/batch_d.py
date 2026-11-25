"""Batch D: skill ablation. Each part runs twice on the same cases: with its skill file, and with a minimal
prompt that keeps only the role and the JSON output contract (the domain guidance is removed).

    analyst   84 routing utterances (intent, clarification, details)
    planner   17 plan cases (8 Hong Kong, 6 Japan, 3 TravelPlanner-style) as full turns
    editor    8 edits on frozen plans
    verifier  17 altered plans with a person's labels

Run from PowerShell (live sources blocked; only model calls go out):
    & "C:\\Users\\Dies Irae\\Desktop\\HKU\\LLM\\Pathfinder v5\\backend\\.venv\\Scripts\\python.exe" `
      "C:\\Users\\Dies Irae\\Desktop\\HKU\\LLM\\Final Project\\Deliverable\\tests\\batch_d.py"
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"C:\Users\Dies Irae\Desktop\HKU\LLM\Pathfinder v5")
OUT = Path(os.environ.get("BATCH_OUT") or Path(__file__).resolve().parent.parent / "test_results")
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PURPOSE_MODELS", '{"edit":"claude-sonnet-5.5","ask":"claude-sonnet-5.5","search_query":"claude-sonnet-5.5"}')
os.environ.setdefault("MAX_REPAIRS", "2")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline  # noqa: E402

offline.install()
os.chdir(REPO / "backend")
sys.path.insert(0, str(REPO / "backend" / "scripts"))

from app.api.deps import Container  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.domain.models import ConstraintCheck, TripContext, TripPlan  # noqa: E402
from app.eval import datasets, runner  # noqa: E402
from app.evidence.models import Bundle  # noqa: E402
from app.evidence.providers import Providers  # noqa: E402
from app.plan.editor import propose_edit  # noqa: E402
from app.plan.validator import audit_constraints  # noqa: E402
from app.understand.pipeline import understand  # noqa: E402
from app.understand.schemas import EditDraft, PlanDraft, VerifyResult  # noqa: E402
from v5_editor_direct import CASES as EDIT_CASES, check as edit_check, load as edit_load  # noqa: E402
from v5_skills_ablation import MINIMAL as MINIMAL_ANALYST  # noqa: E402
from v5_verifier_bench import CASES as VERIFY_CASES, mutate  # noqa: E402

CONCURRENCY = int(os.environ.get("BATCH_CONCURRENCY", "2"))
LIMIT = int(os.environ.get("BATCH_LIMIT", "0"))

MINIMAL_PLANNER = (
    "You are the planning step of a trip planner. You receive candidate places with ref ids, the trip context and "
    "the day dates. Return one JSON object that matches this schema:\n"
    + json.dumps(PlanDraft.model_json_schema())
    + "\nChoose stops from the candidates by their ref and give each a start and end time (HH:MM), with one entry per "
    "date of the trip."
)
MINIMAL_EDITOR = (
    "You are the editing step of a trip planner. You receive the current plan and the traveller's request. Return "
    "one JSON object that matches this schema:\n" + json.dumps(EditDraft.model_json_schema())
    + "\nExpress the requested change as operations on the plan's items."
)
MINIMAL_VERIFIER = (
    "You check a trip plan against the traveller's hard constraints. Return one JSON object that matches this "
    "schema:\n" + json.dumps(VerifyResult.model_json_schema())
    + "\nFor each constraint give pass, fail or unknown with short evidence."
)


def commit() -> str:
    import subprocess
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          check=False).stdout.strip()


def save(name: str, payload: dict) -> None:
    path = OUT / f"{name}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("saved", path, flush=True)


def summary(rows: list[dict], key: str = "correct") -> dict:
    ok = [r for r in rows if r.get("valid", True)]
    lat = [r["latency_ms"] for r in ok if r.get("latency_ms") is not None]
    return {"n": len(rows), "valid": len(ok), "correct": sum(1 for r in ok if r.get(key)),
            "p50_ms": round(statistics.median(lat), 1) if lat else None,
            "mean_tokens": round(statistics.mean(r.get("tokens") or 0 for r in ok), 1) if ok else None}


async def analyst(settings) -> dict:
    cases = datasets.load("routing")[: LIMIT or None]
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(case, condition):
        async with semaphore:
            trace = TurnTrace(request_id=f"skill-analyst-{condition}-{case.id}")
            llm = LLMClient(settings, trace=trace)
            started = time.perf_counter()
            try:
                result = await understand(
                    llm=llm, settings=settings,
                    context=TripContext(session_id=f"sa-{case.id}", destination=case.destination, origin=case.origin,
                                        currency=case.currency or settings.default_currency, language="en"),
                    plan=None, messages=[], message=case.input, scope_label=None, scope_item_id=None, locale="en",
                    skill_overrides={"understand": MINIMAL_ANALYST} if condition == "minimal" else None)
                intent, clarify, error = result.parsed.intent, bool(result.parsed.need_clarification), ""
            except Exception as exc:  # noqa: BLE001
                intent, clarify, error = "error", None, str(exc)[:200]
            finally:
                await llm.aclose()
            return {"id": case.id, "condition": condition, "expected": case.expected_intent, "predicted": intent,
                    "correct": intent == case.expected_intent, "clarify": clarify, "error": error,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1), "tokens": trace.total_tokens,
                    "valid": trace.total_tokens > 0}

    rows = []
    for condition in ("full", "minimal"):
        rows += await asyncio.gather(*(one(case, condition) for case in cases))
        part = [r for r in rows if r["condition"] == condition]
        print(f"  analyst {condition}: {sum(r['correct'] for r in part)}/{len(part)}", flush=True)
    by_intent = {}
    for condition in ("full", "minimal"):
        for intent in ("plan", "modify", "ask", "unclear"):
            part = [r for r in rows if r["condition"] == condition and r["expected"] == intent]
            by_intent[f"{condition}:{intent}"] = f"{sum(r['correct'] for r in part)}/{len(part)}"
    return {"full": summary([r for r in rows if r["condition"] == "full"]),
            "minimal": summary([r for r in rows if r["condition"] == "minimal"]), "by_intent": by_intent, "rows": rows}


async def planner(settings) -> dict:
    results = {}
    for condition in ("full", "minimal"):
        store = Store(str(OUT / f"skills_planner_{condition}.db"))
        store.init()
        if condition == "minimal":
            await store.set_kv("prompts", {"plan": MINIMAL_PLANNER})
        c = Container(settings=settings, store=store)
        cases = []
        for dataset_id in ("hongkong", "japan", "travelplanner"):
            run = await runner.run_dataset(c, dataset_id, f"skills-planner-{condition}", "all")
            for row in run["cases"]:
                scores = row.get("scores") or {}
                cases.append({"suite": dataset_id, "case": row["case_id"], "passed": row["passed"],
                              "failure": row.get("failure_class", ""), "grounding": scores.get("grounding"),
                              "constraints_met": scores.get("constraints_met"),
                              "constraints_total": scores.get("constraints_total"), "calls": scores.get("llm_calls"),
                              "tokens": scores.get("tokens"), "latency_ms": row.get("latency_ms"),
                              "valid": int(scores.get("tokens") or 0) > 0})
            print(f"  planner {condition} {dataset_id}: {run['passed']}/{run['case_count']}", flush=True)
        await store.close()
        results[condition] = {"summary": summary(cases, "passed"), "cases": cases,
                              "calls_mean": round(statistics.mean(c_["calls"] or 0 for c_ in cases), 2) if cases else None}
    return results


async def editor(settings) -> dict:
    cases = EDIT_CASES[: LIMIT or None]
    rows = []
    for condition in ("full", "minimal"):
        for case in cases:
            raw, context_raw = edit_load(case["plan"], case["prefix"])
            trace = TurnTrace(request_id=f"skill-editor-{condition}-{case['id']}")
            llm = LLMClient(settings, trace=trace)
            providers = Providers(settings, trace)
            started = time.perf_counter()
            try:
                outcome = await propose_edit(
                    llm=llm, providers=providers, settings=settings, context=TripContext(**context_raw),
                    plan=TripPlan(**raw), bundle=Bundle(), message=case["input"], scope_label=None, scope_item_id=None,
                    trace=trace, skill_overrides={"edit": MINIMAL_EDITOR} if condition == "minimal" else None)
                passed, error = edit_check(case, outcome.plan), ""
            except Exception as exc:  # noqa: BLE001
                passed, error = False, str(exc)[:200]
            finally:
                await llm.aclose()
                await providers.aclose()
            rows.append({"id": case["id"], "condition": condition, "correct": bool(passed), "error": error,
                         "latency_ms": round((time.perf_counter() - started) * 1000, 1), "tokens": trace.total_tokens,
                         "valid": trace.total_tokens > 0})
        part = [r for r in rows if r["condition"] == condition]
        print(f"  editor {condition}: {sum(r['correct'] for r in part)}/{len(part)}", flush=True)
    return {"full": summary([r for r in rows if r["condition"] == "full"]),
            "minimal": summary([r for r in rows if r["condition"] == "minimal"]), "rows": rows}


async def verifier(settings) -> dict:
    raw = json.loads((REPO / "backend/eval/fixtures/v5_study/kyoto_plan.json").read_text(encoding="utf-8"))
    context = TripContext(session_id="skill-verify", destination="Kyoto", origin="Hong Kong", start_date="2026-12-05",
                          end_date="2026-12-07", days=2, party_size=2, budget=100000, currency="JPY")
    seen, cases = set(), []
    for case in VERIFY_CASES:
        key = (case["constraint"], case["mutation"])
        if key not in seen:
            seen.add(key)
            cases.append(case)
    cases = cases[: LIMIT or None]
    rows = []
    for condition in ("full", "minimal"):
        for case in cases:
            plan = mutate(TripPlan(**raw), case["mutation"])
            plan.constraints = [ConstraintCheck(id="c1", text=case["constraint"], status="unknown", evidence="")]
            trace = TurnTrace(request_id=f"skill-verify-{condition}-{case['id']}")
            llm = LLMClient(settings, trace=trace)
            started = time.perf_counter()
            try:
                result = await audit_constraints(llm=llm, plan=plan, context=context, trace=trace,
                                                 skill_overrides={"verify": MINIMAL_VERIFIER} if condition == "minimal" else None)
                status = result.checks[0].status if result and result.checks else "unknown"
                error = ""
            except Exception as exc:  # noqa: BLE001
                status, error = "unknown", str(exc)[:200]
            finally:
                await llm.aclose()
            rows.append({"id": case["id"], "condition": condition, "gold": case["gold"], "predicted": status,
                         "correct": status == case["gold"], "false_pass": case["gold"] == "fail" and status == "pass",
                         "false_alarm": case["gold"] == "pass" and status == "fail", "error": error,
                         "latency_ms": round((time.perf_counter() - started) * 1000, 1), "tokens": trace.total_tokens,
                         "valid": trace.total_tokens > 0})
        part = [r for r in rows if r["condition"] == condition]
        print(f"  verifier {condition}: {sum(r['correct'] for r in part)}/{len(part)}", flush=True)
    out = {}
    for condition in ("full", "minimal"):
        part = [r for r in rows if r["condition"] == condition]
        out[condition] = {**summary(part), "false_pass": sum(r["false_pass"] for r in part),
                          "false_alarm": sum(r["false_alarm"] for r in part)}
    out["rows"] = rows
    return out


async def main() -> None:
    settings = get_settings()
    jobs = sys.argv[1:] or ["analyst", "editor", "verifier", "planner"]
    payload = {"commit": commit(), "writer": settings.writer[2], "verifier_model": settings.verifier_model,
               "temperature": settings.llm_temperature, "max_repairs": settings.max_repairs,
               "minimal_prompts": {"analyst": MINIMAL_ANALYST[:400], "planner": MINIMAL_PLANNER[:400],
                                   "editor": MINIMAL_EDITOR[:400], "verifier": MINIMAL_VERIFIER[:400]}}
    for name in jobs:
        print(f"=== skills: {name} ===", flush=True)
        payload[name] = await {"analyst": analyst, "planner": planner, "editor": editor, "verifier": verifier}[name](settings)
        save("skills_ablation_partial", payload)
    save("skills_ablation", payload)
    offline.report()


if __name__ == "__main__":
    asyncio.run(main())
