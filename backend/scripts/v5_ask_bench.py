"""Ask / quick-answer comparison on the frozen Kyoto plan."""

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

from app.api.deps import Container  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.domain.models import ChatRequest, TripContext, TripPlan  # noqa: E402
from app.turn.orchestrator import Dependencies, collect_turn  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-artifacts" / "v5-model-study"
FIXTURES = Path("eval/fixtures/v5_study")
ARMS = {
    "sonnet": "claude-sonnet-5.5",
    "flash": "gemini-3.5-flash-lite",
    "deepseek": "deepseek-v4-flash",
}


async def run_case(settings, model: str, case: dict, plan_raw: dict, context_raw: dict) -> dict:
    db = Path("data") / f"bench_ask_{model.replace('.', '_')}.db"
    store = Store(str(db))
    store.init()
    session = await store.create_session(client_token=f"ask-{case['id']}")
    session_id = session["id"]
    context = TripContext(**{**context_raw, "session_id": session_id})
    await store.save_context(session_id, context.model_dump())
    plan = TripPlan(**{**plan_raw, "session_id": session_id, "trip_id": f"trip-{session_id}"})
    await store.save_plan(plan.model_dump(mode="json"))
    deps = Dependencies(settings=settings, store=store)
    started = time.perf_counter()
    events = await collect_turn(
        deps,
        ChatRequest(session_id=session_id, message=case["question"], locale="en", review=False),
        session_id=session_id,
        user_message=case["question"],
    )
    reply = "".join(event.data.get("text", "") for event in events if event.event == "token")
    trace = next((event.data for event in events if event.event == "trace"), {})
    generation = next(
        (row for row in trace.get("generations", []) if row.get("purpose") == "ask"),
        {},
    )
    lowered = reply.lower()
    missing = [group for group in case.get("must", []) if not any(word.lower() in lowered for word in group)]
    forbidden = [word for word in case.get("must_not", []) if word.lower() in lowered]
    passed = not missing and not forbidden
    await store.close()
    return {
        "id": case["id"],
        "question": case["question"],
        "reply": reply[:2000],
        "correct": passed,
        "missing": missing,
        "forbidden": forbidden,
        "expect_gap": case.get("expect_gap", False),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "tokens": int(generation.get("prompt_tokens", 0)) + int(generation.get("completion_tokens", 0)),
        "model": generation.get("model") or model,
        "trace_id": trace.get("request_id") or "",
    }


async def main() -> None:
    base = get_settings()
    plan_raw = json.loads((FIXTURES / "kyoto_plan.json").read_text(encoding="utf-8"))
    context_raw = json.loads((FIXTURES / "kyoto_context.json").read_text(encoding="utf-8"))
    cases = json.loads((FIXTURES / "ask_cases.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "n": len(cases), "arms": {}}
    for arm, model in ARMS.items():
        settings = base.model_copy(
            update={"purpose_models": {"ask": model}, "purpose_temperatures": {"ask": 0.0}}
        )
        rows = []
        for case in cases:
            row = await run_case(settings, model, case, plan_raw, context_raw)
            rows.append(row)
            print(f"{arm} {case['id']} {'PASS' if row['correct'] else 'FAIL'} {row['latency_ms']} ms", flush=True)
        passed = sum(1 for row in rows if row["correct"])
        payload["arms"][arm] = {
            "model": model,
            "correct": passed,
            "n": len(rows),
            "accuracy": round(passed / len(rows), 4),
            "p50_latency_ms": round(statistics.median([row["latency_ms"] for row in rows]), 1),
            "rows": rows,
        }
        print(f"{arm}: {passed}/{len(rows)}", flush=True)
    target = OUT / f"ask-{stamp}.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (OUT / "ask-latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["# v5 Ask comparison", "", "| Arm | Model | Correct | Accuracy | p50 ms |", "|---|---|---:|---:|---:|"]
    for arm, data in payload["arms"].items():
        lines.append(f"| {arm} | {data['model']} | {data['correct']}/{data['n']} | {data['accuracy']:.1%} | {data['p50_latency_ms']} |")
    (OUT / "ask-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(target, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
