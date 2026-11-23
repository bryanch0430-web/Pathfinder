"""Verifier temperature ablation on the frozen 20-case suite.

The verifier is the part whose default is explicitly temperature 0 in the shipped
design, so this is the cleanest place to test whether 0.2 changes correctness.
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

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.domain.models import ConstraintCheck, TripContext, TripPlan  # noqa: E402
from app.plan.validator import audit_constraints  # noqa: E402
from v5_verifier_bench import CASES, mutate  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-artifacts" / "v5-model-study"
FIXTURES = Path(__file__).resolve().parent.parent / "eval" / "fixtures" / "v5_study"


async def main() -> None:
    base = get_settings()
    raw = json.loads((FIXTURES / "kyoto_plan.json").read_text(encoding="utf-8"))
    context = TripContext(
        session_id="verify-temperature",
        destination="Kyoto",
        origin="Hong Kong",
        start_date="2026-12-05",
        end_date="2026-12-07",
        days=2,
        party_size=2,
        budget=100000,
        currency="JPY",
    )
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "model": base.verifier_model, "arms": {}}
    for temperature in (0.0, 0.2):
        settings = base.model_copy(update={"purpose_temperatures": {"verify": temperature}})
        rows = []
        for case in CASES:
            plan = mutate(TripPlan(**raw), case["mutation"])
            plan.constraints = [ConstraintCheck(id="c1", text=case["constraint"], status="unknown", evidence="")]
            llm = LLMClient(settings)
            trace = TurnTrace(request_id=f"verify-t{temperature}-{case['id']}")
            started = time.perf_counter()
            try:
                result = await audit_constraints(llm=llm, plan=plan, context=context, trace=trace)
                status = result.checks[0].status if result and result.checks else "unknown"
                evidence = result.checks[0].evidence if result and result.checks else ""
                error = ""
            except Exception as exc:  # noqa: BLE001
                status, evidence, error = "unknown", "", str(exc)[:240]
            finally:
                await llm.aclose()
            rows.append(
                {
                    "id": case["id"],
                    "constraint": case["constraint"],
                    "mutation": case["mutation"],
                    "gold": case["gold"],
                    "predicted": status,
                    "correct": status == case["gold"],
                    "false_pass": case["gold"] == "fail" and status == "pass",
                    "false_alarm": case["gold"] == "pass" and status == "fail",
                    "evidence": evidence[:500],
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "error": error,
                }
            )
            print(f"T={temperature:.1f} {case['id']} gold={case['gold']} got={status}", flush=True)
        payload["arms"][f"temperature_{temperature:.1f}"] = {
            "temperature": temperature,
            "correct": sum(1 for row in rows if row["correct"]),
            "n": len(rows),
            "false_passes": sum(1 for row in rows if row["false_pass"]),
            "false_alarms": sum(1 for row in rows if row["false_alarm"]),
            "p50_latency_ms": round(statistics.median(row["latency_ms"] for row in rows), 1),
            "rows": rows,
        }

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (OUT / f"verifier-temperature-{stamp}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT / "verifier-temperature.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = ["# Verifier temperature ablation", "", f"Model: `{payload['model']}`", "", "| Temperature | Correct | False passes | False alarms | p50 |", "|---:|---:|---:|---:|---:|"]
    for key, arm in payload["arms"].items():
        lines.append(
            f"| {arm['temperature']:.1f} | {arm['correct']}/{arm['n']} | {arm['false_passes']} | "
            f"{arm['false_alarms']} | {arm['p50_latency_ms']} ms |"
        )
    (OUT / "verifier-temperature.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({key: {k: v for k, v in arm.items() if k != "rows"} for key, arm in payload["arms"].items()}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
