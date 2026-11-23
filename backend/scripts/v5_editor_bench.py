"""Editor comparison on frozen plans (no re-seeding per arm)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
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
ARMS = {
    "sonnet": "claude-sonnet-5.5",
    "flash": "gemini-3.5-flash-lite",
    "deepseek": "deepseek-v4-flash",
}
SOURCES = {
    "tokyo_remove": ("data/bench_japan_sonnet.db", "2c0b8a6e"),
    "tokyo_move": ("data/bench_japan_sonnet.db", "dfaa4f9d"),
    "tokyo_hotel": ("data/bench_japan_sonnet.db", "dfaa4f9d"),
    "kyoto_add": ("data/bench_japan_sonnet.db", "2a68bc5c"),
    "kyoto_time": ("data/bench_japan_sonnet.db", "2a68bc5c"),
}
CASES = [
    {"id": "ed01", "source": "tokyo_remove", "input": "Remove the Ghibli Museum, keep everything else.", "check": "removed", "target": "Ghibli"},
    {"id": "ed02", "source": "tokyo_move", "input": "Move Senso-ji to day 2 in the morning, keep everything else.", "check": "day_before_noon", "target": "Senso-ji"},
    {"id": "ed03", "source": "tokyo_hotel", "input": "Swap the hotel for a different one, keep everything else.", "check": "hotel_changed"},
    {"id": "ed04", "source": "kyoto_add", "input": "Add Fushimi Inari Taisha on day 2, keep everything else.", "check": "added", "target": "Fushimi"},
    {"id": "ed05", "source": "kyoto_time", "input": "Change Kiyomizu-dera to 15:00, keep the rest.", "check": "start", "target": "Kiyomizu", "start": "15:00"},
]


def load_plan(source: str) -> tuple[dict, dict]:
    db_path, prefix = SOURCES[source]
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "select body from plans where session_id like ? order by rowid desc limit 1", (prefix + "%",)
    ).fetchone()
    if not row:
        raise RuntimeError(f"no plan for {source}")
    plan = json.loads(row[0])
    context_row = conn.execute(
        "select body from contexts where session_id like ? order by rowid desc limit 1", (prefix + "%",)
    ).fetchone()
    context = json.loads(context_row[0]) if context_row else {}
    return plan, context


def semantics(case: dict, before: dict, after: dict) -> bool:
    items_after = [item for day in after.get("days", []) for item in day.get("items", [])]
    if case["check"] == "removed":
        return not any(case["target"].lower() in item["name"].lower() for item in items_after)
    if case["check"] == "added":
        return any(case["target"].lower() in item["name"].lower() for item in items_after)
    if case["check"] == "day_before_noon":
        for day_index, day in enumerate(after.get("days", []), start=1):
            for item in day.get("items", []):
                if case["target"].lower() in item["name"].lower():
                    return day_index == 2 and (item.get("start") or "99:99") < "12:00"
        return False
    if case["check"] == "start":
        for item in items_after:
            if case["target"].lower() in item["name"].lower():
                return item.get("start") == case["start"]
        return False
    if case["check"] == "hotel_changed":
        before_h = (before.get("hotels") or [{}])[0].get("name", "")
        after_h = (after.get("hotels") or [{}])[0].get("name", "")
        return bool(after_h) and after_h != before_h
    return False


async def main() -> None:
    base = get_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "arms": {}}
    for arm, model in ARMS.items():
        settings = base.model_copy(
            update={"purpose_models": {"edit": model}, "purpose_temperatures": {"edit": 0.0}}
        )
        store = Store(f"data/bench_editor_{arm}.db")
        store.init()
        container = Container(settings=settings, store=store)
        deps = Dependencies(settings=settings, store=store)
        rows = []
        for case in CASES:
            plan_raw, context_raw = load_plan(case["source"])
            session = await store.create_session(client_token=f"editor-{case['id']}")
            sid = session["id"]
            context = TripContext(**{**context_raw, "session_id": sid})
            await store.save_context(sid, context.model_dump())
            plan = TripPlan(**{**plan_raw, "session_id": sid, "trip_id": f"trip-{sid}"})
            await store.save_plan(plan.model_dump(mode="json"))
            before = plan.model_dump(mode="json")
            started = time.perf_counter()
            events = await collect_turn(
                deps,
                ChatRequest(session_id=sid, message=case["input"], locale="en", review=False),
                session_id=sid,
                user_message=case["input"],
            )
            trace = next((event.data for event in events if event.event == "trace"), {})
            after = await store.load_plan(sid)
            after = json.loads(json.dumps(after)) if after else {}
            before_ids = {item["id"] for day in before.get("days", []) for item in day.get("items", [])}
            after_ids = {item["id"] for day in after.get("days", []) for item in day.get("items", [])}
            preserved = len(before_ids & after_ids) / max(1, len(before_ids))
            semantic = semantics(case, before, after)
            version = after.get("version", 1)
            passed = semantic and preserved >= 0.7 and version > 1
            row = {
                "id": case["id"],
                "input": case["input"],
                "check": case["check"],
                "passed": passed,
                "semantic": semantic,
                "preserved": round(preserved, 3),
                "version": version,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "tokens": sum(
                    int(gen.get("prompt_tokens", 0)) + int(gen.get("completion_tokens", 0))
                    for gen in trace.get("generations", [])
                ),
                "reply": "".join(event.data.get("text", "") for event in events if event.event == "token")[:400],
                "trace_id": trace.get("request_id", ""),
            }
            rows.append(row)
            print(f"{arm} {case['id']} {'PASS' if passed else 'FAIL'} semantic={semantic} preserved={preserved:.0%}", flush=True)
        passed = sum(1 for row in rows if row["passed"])
        payload["arms"][arm] = {
            "model": model,
            "correct": passed,
            "n": len(rows),
            "mean_preserved": round(statistics.mean(row["preserved"] for row in rows), 3),
            "p50_latency_ms": round(statistics.median(row["latency_ms"] for row in rows), 1),
            "rows": rows,
        }
        print(f"{arm}: {passed}/{len(rows)}", flush=True)
        await store.close()
    target = OUT / f"editor-{stamp}.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (OUT / "editor-latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["# v5 editor comparison", "", "| Arm | Model | Correct | Mean preserved | p50 ms |", "|---|---|---:|---:|---:|"]
    for arm, data in payload["arms"].items():
        lines.append(f"| {arm} | {data['model']} | {data['correct']}/{data['n']} | {data['mean_preserved']:.0%} | {data['p50_latency_ms']} |")
    (OUT / "editor-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(target, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
