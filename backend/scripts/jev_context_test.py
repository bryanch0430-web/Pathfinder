import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.domain.models import Destination, TripContext, TripPlan  # noqa: E402
from app.eval import datasets  # noqa: E402
from app.understand.jev import jev_route  # noqa: E402

ENV = PROJECT / ".env.autotest"
OUT = PROJECT / "docs" / "eval-artifacts" / "autotest" / "jev-with-plan.json"
env = {}
for line in ENV.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
base = get_settings().model_copy(
    update={
        "decision_api_key": env.get("JEV_API_KEY", ""),
        "decision_api_url": env.get("DECISION_API_URL", "https://api.typesafe.ai/v1/systemone"),
        "decision_model": env.get("DECISION_MODEL", "jev-latest"),
    }
)
cases = datasets.load("routing")
sem = asyncio.Semaphore(4)


async def one(case):
    async with sem:
        plan = TripPlan(
            trip_id=f"jev-{case.id}",
            session_id=f"jev-{case.id}",
            destination=Destination(name=case.destination or "Kyoto"),
            summary="An existing plan is on file.",
        )
        decision = await jev_route(
            settings=base,
            context=TripContext(
                session_id=f"jev-{case.id}",
                destination=case.destination,
                origin=case.origin,
                currency=case.currency or base.default_currency,
            ),
            plan=plan,
            message=case.input,
            history=[],
        )
        return {
            "id": case.id,
            "expected": case.expected_intent,
            "predicted": decision.intent if decision else "error",
            "correct": bool(decision and decision.intent == case.expected_intent),
            "confidence": decision.confidence if decision else None,
        }


async def main():
    rows = await asyncio.gather(*(one(case) for case in cases))
    passed = sum(1 for row in rows if row["correct"])
    print("jev-with-plan", passed, len(rows), passed / len(rows))
    print([row for row in rows if not row["correct"]][:10])
    OUT.write_text(json.dumps({"n": len(rows), "correct": passed, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")


asyncio.run(main())
