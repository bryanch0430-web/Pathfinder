"""Score the frozen routing set with and without Jev.

The 84 labelled lines live in the router app's eval fixture. Rules run on every
line. Jev and the writer run only where the rules will not decide alone, which
is the only place Jev can change the result. A plan is already on the trip, the
same assumption the original routing trial uses.

    python scripts/measure_routing.py
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from pathlib import Path

import httpx

from app.routing.models import TripSnapshot
from app.routing.rules import message_flagged, rules_decide_alone
from app.routing.system1 import System1Router, build_router

FIXTURE = Path(__file__).resolve().parents[3] / "Pathfinder_Router Version_16_11" / "backend" / "eval" / "fixtures" / "routing.jsonl"


def rows() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def context(row: dict) -> tuple[TripSnapshot, dict]:
    city = row.get("destination") or "Kyoto"
    snapshot = TripSnapshot(destination=city, origin="Singapore", duration_days=3, has_plan=True, start_date="2026-11-20")
    plan = {"destination": city, "days": [{"day": 1, "base": city, "items": [{"name": "Hotel"}, {"name": "Main temple"}]}]}
    return snapshot, plan


def alone(router: System1Router, message: str, snapshot: TripSnapshot, plan: dict) -> bool:
    ruled = router.rules.route(message=message, snapshot=snapshot, plan_summary=plan)
    return (
        router.rules_first
        and ruled.confidence >= router.threshold
        and not message_flagged(message)
        and rules_decide_alone(message, ruled, snapshot=snapshot, plan_summary=plan)
    )


async def decide(router: System1Router, message: str, snapshot: TripSnapshot, plan: dict):
    started = time.perf_counter()
    decision = await router.route(message=message, snapshot=snapshot, plan_summary=plan)
    return decision, int((time.perf_counter() - started) * 1000)


def pct(hits: int, total: int) -> float:
    return round(100.0 * hits / total, 1) if total else 0.0


def summarize(records: list[dict], key: str) -> dict:
    return {
        "n": len(records),
        "intent": pct(sum(item[key]["intent_ok"] for item in records), len(records)),
        "action": pct(sum(item[key]["action_ok"] for item in records), len(records)),
        "mean_ms": round(sum(item[key]["ms"] for item in records) / len(records), 1) if records else 0,
    }


async def grok_generate(prompt: str, system_instruction: str | None = None, temperature: float = 0) -> str:
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": "Bearer " + os.environ["XAI_KEY"]},
            json={
                "model": os.environ.get("XAI_MODEL", "grok-4.7"),
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 300,
            },
        )
        response.raise_for_status()
        return (response.json()["choices"][0]["message"].get("content") or "").strip()


async def main() -> None:
    labelled = rows()
    jev_router = build_router()
    writer_router = System1Router(threshold=jev_router.threshold, rules_first=True, jev=None, use_writer=True)
    from app.routing.writer import WriterRouter
    grok_router = System1Router(
        threshold=jev_router.threshold, rules_first=True, jev=None,
        writer=WriterRouter(generate=grok_generate), use_writer=True,
    )
    calls = {"jev": 0, "writer": 0, "grok": 0}
    jev_route, writer_route, grok_route = jev_router.jev.route, writer_router.writer.route, grok_router.writer.route

    async def count_jev(**kwargs):
        calls["jev"] += 1
        return await jev_route(**kwargs)

    async def count_writer(**kwargs):
        calls["writer"] += 1
        return await writer_route(**kwargs)

    async def count_grok(**kwargs):
        calls["grok"] += 1
        return await grok_route(**kwargs)

    jev_router.jev.route = count_jev
    writer_router.writer.route = count_writer
    grok_router.writer.route = count_grok

    records = []
    for index, row in enumerate(labelled, start=1):
        snapshot, plan = context(row)
        message = row["input"]
        expected = row["expected_intent"]
        if alone(jev_router, message, snapshot, plan):
            decision, elapsed = await decide(jev_router, message, snapshot, plan)
            arms = {"rules": pack(decision, elapsed, expected)}
            print(f"{index:02d} {row['id']} rules {decision.intent.value}", flush=True)
        else:
            jev_decision, jev_ms = await decide(jev_router, message, snapshot, plan)
            writer_decision, writer_ms = await decide(writer_router, message, snapshot, plan)
            grok_decision, grok_ms = await decide(grok_router, message, snapshot, plan)
            arms = {
                "jev": pack(jev_decision, jev_ms, expected),
                "writer": pack(writer_decision, writer_ms, expected),
                "grok": pack(grok_decision, grok_ms, expected),
            }
            print(
                f"{index:02d} {row['id']} jev={jev_decision.intent.value} "
                f"deepseek={writer_decision.intent.value} grok={grok_decision.intent.value} expected={expected}",
                flush=True,
            )
        records.append({"id": row["id"], "split": row["split"], "expected": expected, **arms})

    model_rows = [item for item in records if "jev" in item]
    def pair(left: str, right: str) -> dict:
        return {
            "both_right": sum(item[left]["intent_ok"] and item[right]["intent_ok"] for item in model_rows),
            "left_only": [item["id"] for item in model_rows if item[left]["intent_ok"] and not item[right]["intent_ok"]],
            "right_only": [item["id"] for item in model_rows if item[right]["intent_ok"] and not item[left]["intent_ok"]],
            "both_wrong": sum(not item[left]["intent_ok"] and not item[right]["intent_ok"] for item in model_rows),
        }

    heldout = [item for item in model_rows if item["split"] == "heldout"]
    report = {
        "model": os.environ.get("XAI_MODEL", "grok-4.7"),
        "n": len(records),
        "rules_alone": summarize([item for item in records if "rules" in item], "rules"),
        "model_turns": {
            "all": {name: summarize(model_rows, name) for name in ("jev", "writer", "grok")},
            "heldout": {name: summarize(heldout, name) for name in ("jev", "writer", "grok")},
        },
        "paired_intent": {"jev_vs_writer": pair("jev", "writer"), "grok_vs_writer": pair("grok", "writer"), "grok_vs_jev": pair("grok", "jev")},
        "calls": calls,
    }
    print(json.dumps(report, indent=2, default=dict))


def pack(decision, elapsed: int, expected: str) -> dict:
    intent_ok = decision.intent.value == expected
    asked = decision.need_clarification or decision.intent.value == "unclear"
    action_ok = asked if expected == "unclear" else intent_ok and not decision.need_clarification
    return {
        "intent": decision.intent.value,
        "source": decision.source,
        "reason": decision.reason_code,
        "confidence": round(decision.confidence, 3),
        "ask": decision.need_clarification,
        "intent_ok": intent_ok,
        "action_ok": action_ok,
        "ms": elapsed,
    }


if __name__ == "__main__":
    asyncio.run(main())
