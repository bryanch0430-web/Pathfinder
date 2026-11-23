"""Auto-test the verifier suite through ChatAnywhere at two temperatures."""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.domain.models import ConstraintCheck, TripContext, TripPlan  # noqa: E402
from app.plan.validator import audit_constraints  # noqa: E402
from v5_verifier_bench import CASES, mutate  # noqa: E402

OUT = PROJECT / "docs" / "eval-artifacts" / "autotest"
ENV = PROJECT / ".env.autotest"
FIX = PROJECT / "backend" / "eval" / "fixtures" / "v5_study"


def env_values() -> dict[str, str]:
    values = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    return values


async def main():
    env = env_values()
    base_url = env.get("AUTOTEST_BASE_URL", "https://api.chatanywhere.org/v1")
    key = env.get("AUTOTEST_API_KEY", "")
    if not key:
        raise SystemExit("AUTOTEST_API_KEY missing")
    models = [m.strip() for m in env.get("AUTOTEST_VERIFIER_MODELS", "deepseek-v4.1-flash,claude-sonnet-5-5").split(",") if m.strip()]
    raw = json.loads((FIX / "kyoto_plan.json").read_text(encoding="utf-8"))
    context = TripContext(
        session_id="verify-autotest", destination="Kyoto", origin="Hong Kong",
        start_date="2026-12-05", end_date="2026-12-07", days=2, party_size=2,
        budget=100000, currency="JPY",
    )
    base = get_settings()
    seen = set()
    cases = []
    for case in CASES:
        dedupe_key = (case["constraint"], case["mutation"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cases.append(case)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "suite": "verifier", "n": len(cases), "arms": {}}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    semaphore = asyncio.Semaphore(int(os.environ.get("AUTOTEST_CONCURRENCY", "2")))

    async def one(settings, case, temperature):
        async with semaphore:
            plan = mutate(TripPlan(**raw), case["mutation"])
            plan.constraints = [ConstraintCheck(id="c1", text=case["constraint"], status="unknown", evidence="")]
            trace = TurnTrace(request_id=f"verify-{temperature}-{case['id']}")
            llm = LLMClient(settings, trace=trace)
            started = time.perf_counter()
            try:
                result = await audit_constraints(llm=llm, plan=plan, context=context, trace=trace)
                status = result.checks[0].status if result and result.checks else "unknown"
                evidence = result.checks[0].evidence if result and result.checks else ""
                error = ""
            except Exception as exc:  # noqa: BLE001
                status, evidence, error = "error", "", str(exc)[:300]
            finally:
                await llm.aclose()
            return {
                "id": case["id"], "constraint": case["constraint"], "mutation": case["mutation"],
                "gold": case["gold"], "predicted": status, "correct": status == case["gold"],
                "false_pass": case["gold"] == "fail" and status == "pass",
                "false_alarm": case["gold"] == "pass" and status == "fail",
                "evidence": evidence[:400], "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "tokens": trace.total_tokens, "error": error,
                "why": trace.why, "generation_count": len(trace.generations),
            }

    for model in models:
        for temperature in (0.0, 0.2):
            settings = base.model_copy(update={
                "llm_provider": "openai", "openai_base_url": base_url, "openai_api_key": key,
                "openai_model": model, "llm_model": model,
                "purpose_models": {"verify": model}, "purpose_temperatures": {"verify": temperature},
                "verifier_base_url": base_url, "verifier_api_key": key, "verifier_model": model,
                "llm_temperature": temperature,
            })
            keyname = f"{model} @ {temperature:.1f}"
            print(keyname, flush=True)
            rows = await asyncio.gather(*(one(settings, case, temperature) for case in cases))
            passed = sum(1 for row in rows if row["correct"])
            payload["arms"][keyname] = {
                "model": model, "temperature": temperature, "correct": passed, "n": len(rows),
                "accuracy": round(passed / len(rows), 4),
                "false_passes": sum(1 for row in rows if row["false_pass"]),
                "false_alarms": sum(1 for row in rows if row["false_alarm"]),
                "p50_latency_ms": round(statistics.median(row["latency_ms"] for row in rows), 1),
                "rows": rows,
            }
            print(f"  {passed}/{len(rows)}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"verifier-chatanywhere-{stamp}.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "verifier-chatanywhere-latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Verifier auto-test through ChatAnywhere", "", "| Arm | Correct | False passes | False alarms | p50 |", "|---|---:|---:|---:|---:|"]
    for arm in payload["arms"].values():
        lines.append(f"| {arm['model']} @ {arm['temperature']} | {arm['correct']}/{arm['n']} | {arm['false_passes']} | {arm['false_alarms']} | {arm['p50_latency_ms']} ms |")
    (OUT / "verifier-chatanywhere-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    asyncio.run(main())
