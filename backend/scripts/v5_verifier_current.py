"""Re-run the frozen 20-case verifier benchmark against the current verifier.

This keeps the original `v5_verifier_bench.py` intact for the historical
Sonnet/Flash/Poe-DeepSeek comparison. It runs only the current configured
verifier, so the result answers a different question: does the shipped default
behave like the model studied before?

Run from `backend/`:

    PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_verifier_current.py
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
    settings = get_settings()
    model = settings.verifier_model if settings.verifier_api_key else settings.writer[2]
    base_url = settings.verifier_base_url if settings.verifier_api_key else settings.writer[0]
    raw = json.loads((FIXTURES / "kyoto_plan.json").read_text(encoding="utf-8"))
    context = TripContext(
        session_id="verify-current",
        destination="Kyoto",
        origin="Hong Kong",
        start_date="2026-12-05",
        end_date="2026-12-07",
        days=2,
        party_size=2,
        budget=100000,
        currency="JPY",
    )
    rows = []
    for case in CASES:
        plan = mutate(TripPlan(**raw), case["mutation"])
        plan.constraints = [
            ConstraintCheck(id="c1", text=case["constraint"], status="unknown", evidence="")
        ]
        llm = LLMClient(settings)
        trace = TurnTrace(request_id=f"verify-current-{case['id']}")
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
        print(f"{case['id']} gold={case['gold']} got={status}", flush=True)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "base_url": base_url,
        "temperature": settings.verifier_temperature,
        "max_tokens": settings.verifier_max_tokens,
        "correct": sum(1 for row in rows if row["correct"]),
        "n": len(rows),
        "false_passes": sum(1 for row in rows if row["false_pass"]),
        "false_alarms": sum(1 for row in rows if row["false_alarm"]),
        "p50_latency_ms": round(statistics.median(row["latency_ms"] for row in rows), 1),
        "rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (OUT / f"verifier-current-{stamp}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT / "verifier-current.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = [
        "# Current verifier benchmark",
        "",
        f"Model: **{model}** · endpoint: `{base_url}` · temperature {settings.verifier_temperature} · cap {settings.verifier_max_tokens}",
        "",
        "| Correct | False passes | False alarms | p50 |",
        "|---:|---:|---:|---:|",
        f"| {summary['correct']}/{summary['n']} | {summary['false_passes']} | {summary['false_alarms']} | {summary['p50_latency_ms']} ms |",
        "",
        "| Case | Gold | Predicted | Correct | Latency |",
        "|---|---|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['gold']} | {row['predicted']} | "
            f"{'yes' if row['correct'] else 'no'} | {row['latency_ms']:.0f} ms |"
        )
    (OUT / "verifier-current.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
