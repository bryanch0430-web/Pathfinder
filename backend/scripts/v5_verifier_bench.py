"""Verifier model comparison on a frozen plan with injected defects."""

from __future__ import annotations

import asyncio
import copy
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
from app.domain.models import ConstraintCheck, HotelStay, Money, PlanItem, TripPlan, TripContext  # noqa: E402
from app.plan.validator import audit_constraints  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-artifacts" / "v5-model-study"
FIXTURES = Path("eval/fixtures/v5_study")
ARMS = {
    "sonnet": "claude-sonnet-5.5",
    "flash": "gemini-3.5-flash-lite",
    "deepseek": "deepseek-v4-flash",
}

CASES = [
    {"id": "v01", "constraint": "Keep the total under 100000 JPY", "mutation": "none", "gold": "pass"},
    {"id": "v02", "constraint": "Keep the total under 100000 JPY", "mutation": "budget_over", "gold": "fail"},
    {"id": "v03", "constraint": "Include Kiyomizu-dera", "mutation": "none", "gold": "pass"},
    {"id": "v04", "constraint": "Include Kiyomizu-dera", "mutation": "remove_kiyomizu", "gold": "fail"},
    {"id": "v05", "constraint": "Allow at least 30 minutes between Fushimi Inari and Nishiki Market", "mutation": "none", "gold": "pass"},
    {"id": "v06", "constraint": "Allow at least 30 minutes between Fushimi Inari and Nishiki Market", "mutation": "tight_transfer", "gold": "fail"},
    {"id": "v07", "constraint": "Every stay must be confirmed by a source", "mutation": "none", "gold": "pass"},
    {"id": "v08", "constraint": "Every stay must be confirmed by a source", "mutation": "unsourced_hotel", "gold": "fail"},
    {"id": "v09", "constraint": "All days must fall inside the trip dates", "mutation": "none", "gold": "pass"},
    {"id": "v10", "constraint": "All days must fall inside the trip dates", "mutation": "day_outside", "gold": "fail"},
]


def mutate(plan: TripPlan, mutation: str) -> TripPlan:
    plan = plan.model_copy(deep=True)
    if mutation == "budget_over":
        plan.cost.total = 120000
        plan.cost.remaining = -20000
        plan.cost.status = "over"
    elif mutation == "remove_kiyomizu":
        for day in plan.days:
            day.items = [item for item in day.items if item.name != "Kiyomizu-dera"]
    elif mutation == "tight_transfer":
        for day in plan.days:
            for item in day.items:
                if item.transfer and item.transfer.from_item_id == "a1":
                    item.transfer.minutes = 10
    elif mutation == "unsourced_hotel":
        for hotel in plan.hotels:
            hotel.source_refs = []
            hotel.confirmed = False
            hotel.data_status = "stale"
    elif mutation == "day_outside":
        plan.days[0].date = "2026-12-15"
    return plan


async def main() -> None:
    base = get_settings()
    raw = json.loads((FIXTURES / "kyoto_plan.json").read_text(encoding="utf-8"))
    context = TripContext(
        session_id="verify-bench", destination="Kyoto", origin="Hong Kong",
        start_date="2026-12-05", end_date="2026-12-07", days=2, party_size=2, budget=100000, currency="JPY",
    )
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "cases": CASES, "arms": {}}

    for arm, model in ARMS.items():
        settings = base.model_copy(
            update={"purpose_models": {"verify": model}, "purpose_temperatures": {"verify": 0.0}}
        )
        rows = []
        for case in CASES:
            plan = mutate(TripPlan(**raw), case["mutation"])
            plan.constraints = [ConstraintCheck(id="c1", text=case["constraint"], status="unknown", evidence="")]
            llm = LLMClient(settings)
            trace = TurnTrace(request_id=f"verify-{arm}-{case['id']}")
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
            correct = status == case["gold"]
            rows.append(
                {
                    "id": case["id"],
                    "constraint": case["constraint"],
                    "mutation": case["mutation"],
                    "gold": case["gold"],
                    "predicted": status,
                    "correct": correct,
                    "false_pass": case["gold"] == "fail" and status == "pass",
                    "false_alarm": case["gold"] == "pass" and status == "fail",
                    "evidence": evidence[:500],
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "error": error,
                }
            )
            print(f"{arm} {case['id']} gold={case['gold']} got={status}", flush=True)
        payload["arms"][arm] = {
            "model": model,
            "correct": sum(1 for row in rows if row["correct"]),
            "n": len(rows),
            "false_passes": sum(1 for row in rows if row["false_pass"]),
            "false_alarms": sum(1 for row in rows if row["false_alarm"]),
            "p50_latency_ms": round(statistics.median(row["latency_ms"] for row in rows), 1),
            "rows": rows,
        }
        print(f"{arm}: {payload['arms'][arm]['correct']}/{len(rows)}", flush=True)

    target = OUT / f"verifier-{stamp}.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (OUT / "verifier-latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["# v5 verifier comparison", "", "| Arm | Model | Correct | False pass | False alarm | p50 ms |", "|---|---|---:|---:|---:|---:|"]
    for arm, data in payload["arms"].items():
        lines.append(
            f"| {arm} | {data['model']} | {data['correct']}/{data['n']} | {data['false_passes']} | "
            f"{data['false_alarms']} | {data['p50_latency_ms']} |"
        )
    (OUT / "verifier-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(target, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
