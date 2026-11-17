"""Paired comparison of the three routing gates, on the frozen routing set.

The protocol follows 0xmdinc/jev-medical-bench (and the paired tests in
arXiv:2609.34024): every arm sees the same items once, a missing answer counts
as wrong, and confidence is read as a gate (keep at a pre-declared threshold,
escalate the rest). Thresholds 0.6, 0.8 and 0.9 are the bench's published
cutoffs; 0.8 is also this product's router threshold. Nothing here is tuned
on the results.

Rules-first is measured separately. The head-to-head skips it, so the three
models are compared on every item. The deployed row then lets the rules take
an unambiguous line and sends only the rest to that gate.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import math
import random
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.router import (
    LLMRouter,
    RuleRouter,
    gate_cloud_llm,
    gate_mode,
    rules_first_applies,
)
from app.config import get_settings
from app.dataset_splits import content_hash, load_rows
from app.domain.models import Intent, TripContext
from app.infrastructure import decision as decision_mod
from app.infrastructure.decision import JevDecisionClient
from app.infrastructure.observability import NoopTracer
from app.infrastructure.secret_box import SecretBox
from app.infrastructure.store import SQLiteStore

ARMS = ("jev", "plain", "reason")
THRESHOLDS = (0.6, 0.8, 0.9)
INTENTS = ("plan", "modify", "ask", "unclear")
# A confident wrong intent that starts the expensive path. Pre-declared, not fitted.
EXPENSIVE = {("ask", "plan"), ("unclear", "plan"), ("modify", "plan"), ("ask", "modify"), ("unclear", "modify")}

_usage: contextvars.ContextVar[dict[str, int]] = contextvars.ContextVar("gate_usage", default={})


class _CapturingClient(httpx.AsyncClient):
    async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
        response = await super().post(*args, **kwargs)
        usage = {}
        try:
            body = response.json()
        except Exception:
            body = None
        if isinstance(body, dict) and isinstance(body.get("usage"), dict):
            raw = body["usage"]
            usage = {
                "prompt": int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0),
                "completion": int(raw.get("completion_tokens") or raw.get("output_tokens") or 0),
            }
        _usage.set(usage)
        return response


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    den = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / den
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - rank) + ordered[high] * (rank - low)


def mcnemar(b: int, c: int) -> float:
    """Exact two-sided sign test on discordant pairs. b = first wrong, second right."""
    n = b + c
    if n == 0:
        return 1.0
    observed = math.comb(n, b)
    total = 0
    for k in range(n + 1):
        ways = math.comb(n, k)
        if ways <= observed:
            total += ways
    return min(1.0, total / (2**n))


def holm(p_values: list[tuple[str, float]]) -> list[dict[str, Any]]:
    ordered = sorted(p_values, key=lambda item: item[1])
    m = len(ordered)
    adjusted: list[dict[str, Any]] = []
    running = 0.0
    for index, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, (m - index) * p))
        adjusted.append({"pair": name, "p": p, "p_holm": running})
    return adjusted


def auroc(scores: list[float], correct: list[bool]) -> float | None:
    positive = [score for score, hit in zip(scores, correct) if hit]
    negative = [score for score, hit in zip(scores, correct) if not hit]
    if not positive or not negative:
        return None
    wins = ties = 0
    for left in positive:
        for right in negative:
            if left > right:
                wins += 1
            elif left == right:
                ties += 1
    return (wins + 0.5 * ties) / (len(positive) * len(negative))


def context_for(row: dict[str, Any]) -> TripContext:
    """The same trip the routing scorecard builds. A plan is present for every item."""
    start = date.today() + timedelta(days=30)
    return TripContext(
        session_id="eval",
        destination=str(row.get("destination") or "Kyoto"),
        start_date=start,
        end_date=start + timedelta(days=2),
        party_size=int(row.get("party_size") or 2),
        budget=float(row.get("budget") or 3000),
        currency=str(row.get("currency") or "USD"),
    )


async def ask_arm(
    arm: str,
    *,
    settings: Any,
    prompts: PromptLibrary,
    jev: JevDecisionClient,
    message: str,
    context: TripContext,
    plan_summary: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    prompt_tokens = completion_tokens = 0
    error = ""
    decision = None
    try:
        if arm == "jev":
            _usage.set({})
            decision = await jev.route(
                message=message, context=context, plan_summary=plan_summary, history=[]
            )
            usage = _usage.get()
            prompt_tokens = int(usage.get("prompt") or 0)
            completion_tokens = int(usage.get("completion") or 0)
            if decision is None:
                error = "no_decision"
        else:
            cloud = gate_cloud_llm(settings, arm)
            if cloud is None:
                error = "not_configured"
            else:
                gateway = LLMGateway(cloud, prompts, NoopTracer(), settings)
                decision = await LLMRouter(gateway, settings).route(
                    message=message, context=context, plan_summary=plan_summary, history=[]
                )
                prompt_tokens = gateway.prompt_tokens
                completion_tokens = gateway.completion_tokens
    except Exception as exc:
        error = type(exc).__name__
        decision = None
    latency_ms = int((time.perf_counter() - started) * 1000)
    if decision is None:
        return {
            "intent": "",
            "confidence": 0.0,
            "need_clarification": False,
            "reason_code": error or "no_decision",
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "error": error or "no_decision",
        }
    return {
        "intent": decision.intent.value,
        "confidence": round(float(decision.confidence), 4),
        "need_clarification": bool(decision.need_clarification),
        "reason_code": decision.reason_code,
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "error": "",
    }


def summarise(rows: list[dict[str, Any]], arm: str, *, split: str | None = None) -> dict[str, Any]:
    chosen = [row for row in rows if split is None or row["split"] == split]
    hits = [row for row in chosen if row["arms"][arm]["intent"] == row["expected"]]
    answered = [row for row in chosen if row["arms"][arm]["intent"]]
    low, high = wilson(len(hits), len(chosen))
    by_intent: dict[str, dict[str, int]] = {}
    for intent in INTENTS:
        subset = [row for row in chosen if row["expected"] == intent]
        correct = sum(1 for row in subset if row["arms"][arm]["intent"] == intent)
        by_intent[intent] = {"n": len(subset), "correct": correct}
    latencies = [row["arms"][arm]["latency_ms"] for row in chosen if not row["arms"][arm]["error"]]
    prompt = [row["arms"][arm]["prompt_tokens"] for row in chosen]
    completion = [row["arms"][arm]["completion_tokens"] for row in chosen]
    confidences = [row["arms"][arm]["confidence"] for row in answered]
    correct_flags = [row["arms"][arm]["intent"] == row["expected"] for row in answered]
    gates = []
    for threshold in THRESHOLDS:
        kept = [row for row in chosen if row["arms"][arm]["confidence"] >= threshold and row["arms"][arm]["intent"]]
        kept_hits = sum(1 for row in kept if row["arms"][arm]["intent"] == row["expected"])
        gates.append(
            {
                "threshold": threshold,
                "coverage": round(len(kept) / len(chosen), 4) if chosen else 0.0,
                "accuracy_on_kept": round(kept_hits / len(kept), 4) if kept else None,
                "kept": len(kept),
                "escalated": len(chosen) - len(kept),
            }
        )
    wrong_path = 0
    confusion: Counter[str] = Counter()
    for row in chosen:
        predicted = row["arms"][arm]["intent"] or "none"
        if predicted != row["expected"]:
            confusion[f"{row['expected']}→{predicted}"] += 1
        if (
            row["arms"][arm]["confidence"] >= 0.8
            and predicted != row["expected"]
            and (row["expected"], predicted) in EXPENSIVE
        ):
            wrong_path += 1
    return {
        "n": len(chosen),
        "correct": len(hits),
        "accuracy": round(len(hits) / len(chosen), 4) if chosen else 0.0,
        "wilson95": [round(low, 4), round(high, 4)],
        "answered": len(answered),
        "errors": sum(1 for row in chosen if row["arms"][arm]["error"]),
        "by_intent": by_intent,
        "latency_p50_ms": None if percentile(latencies, 0.5) is None else round(percentile(latencies, 0.5) or 0, 1),
        "latency_p95_ms": None if percentile(latencies, 0.95) is None else round(percentile(latencies, 0.95) or 0, 1),
        "prompt_tokens_mean": round(sum(prompt) / len(prompt), 1) if prompt else 0.0,
        "completion_tokens_mean": round(sum(completion) / len(completion), 1) if completion else 0.0,
        "auroc": None if auroc(confidences, correct_flags) is None else round(auroc(confidences, correct_flags) or 0, 3),
        "gates": gates,
        "confident_expensive_errors": wrong_path,
        "confusions": confusion.most_common(8),
    }


def paired(rows: list[dict[str, Any]], left: str, right: str) -> dict[str, Any]:
    both_right = left_only = right_only = both_wrong = 0
    diffs: list[float] = []
    for row in rows:
        a = row["arms"][left]["intent"] == row["expected"]
        b = row["arms"][right]["intent"] == row["expected"]
        both_right += a and b
        left_only += a and not b
        right_only += (not a) and b
        both_wrong += not a and not b
        diffs.append((1.0 if b else 0.0) - (1.0 if a else 0.0))
    # right minus left. Positive means right is more accurate.
    rng = random.Random(20261117)
    n = len(diffs)
    samples = []
    for _ in range(2000):
        draw = [diffs[rng.randrange(n)] for _ in range(n)]
        samples.append(sum(draw) / n)
    samples.sort()
    return {
        "left": left,
        "right": right,
        "left_only_correct": left_only,
        "right_only_correct": right_only,
        "both_correct": both_right,
        "both_wrong": both_wrong,
        "accuracy_diff_right_minus_left": round(sum(diffs) / n, 4) if n else 0.0,
        "bootstrap95": [round(samples[50], 4), round(samples[1949], 4)] if samples else [0.0, 0.0],
        "mcnemar_p": mcnemar(left_only, right_only),
    }


def deployed(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    correct = called = 0
    for row in rows:
        if row["rules_first"]:
            hit = row["rules_intent"] == row["expected"]
        else:
            called += 1
            hit = row["arms"][arm]["intent"] == row["expected"]
        correct += hit
    low, high = wilson(correct, len(rows))
    return {
        "accuracy": round(correct / len(rows), 4) if rows else 0.0,
        "wilson95": [round(low, 4), round(high, 4)],
        "correct": correct,
        "model_calls": called,
        "rules_first": sum(1 for row in rows if row["rules_first"]),
    }


async def run(limit: int | None) -> dict[str, Any]:
    settings = get_settings()
    store = SQLiteStore(settings.database_path)
    store.apply_runtime(settings)
    store.apply_gate_secrets(settings, SecretBox(settings))
    prompts = PromptLibrary(settings, overrides=store.prompt_override)
    decision_mod.httpx.AsyncClient = _CapturingClient
    jev = JevDecisionClient(settings, prompts)
    rows = load_rows("routing")
    if limit:
        rows = rows[:limit]
    plan_summary = {"trip_id": "x"}
    rules = RuleRouter()
    prepared = []
    for row in rows:
        context = context_for(row)
        message = str(row["input"])
        ruled = rules.route(message=message, context=context, plan_summary=plan_summary, history=[])
        prepared.append(
            {
                "id": row["id"],
                "split": row.get("split") or "dev",
                "expected": str(row.get("expected_intent") or ""),
                "input": message,
                "context": context,
                "rules_intent": ruled.intent.value,
                "rules_confidence": round(float(ruled.confidence), 4),
                "rules_first": rules_first_applies(
                    message, ruled, context=context, plan_summary=plan_summary, settings=settings
                ),
                "arms": {},
            }
        )

    # One model at a time, four requests in flight, so a slow arm does not inflate the others.
    for arm in ARMS:
        sem = asyncio.Semaphore(4)

        async def call(item: dict[str, Any], arm: str = arm, sem: asyncio.Semaphore = sem) -> None:
            async with sem:
                item["arms"][arm] = await ask_arm(
                    arm,
                    settings=settings,
                    prompts=prompts,
                    jev=jev,
                    message=item["input"],
                    context=item["context"],
                    plan_summary=plan_summary,
                )
            result = item["arms"][arm]
            print(
                f"{item['id']} {arm} {result['intent'] or result['error']} "
                f"{result['confidence']:.2f} {result['latency_ms']}ms",
                flush=True,
            )

        await asyncio.gather(*(call(item) for item in prepared))

    details = []
    for item in prepared:
        item.pop("context")
        details.append(item)
    details.sort(key=lambda row: row["id"])
    pairs = [
        paired(details, "jev", "plain"),
        paired(details, "jev", "reason"),
        paired(details, "plain", "reason"),
    ]
    return {
        "protocol": {
            "source": "https://github.com/0xmdinc/jev-medical-bench",
            "dataset": "routing",
            "dataset_hash": content_hash(load_rows("routing")),
            "n": len(details),
            "runs": 1,
            "concurrency": 4,
            "thresholds": list(THRESHOLDS),
            "product_threshold": settings.router_threshold,
            "plan_present": True,
            "rules_first_in_head_to_head": False,
        },
        "models": {
            "jev": settings.decision_model,
            "plain": settings.gate_plain_model,
            "reason": settings.gate_reason_model,
            "plain_temperature": settings.gate_plain_temperature,
            "reason_temperature": settings.gate_reason_temperature,
            "plain_reasoning": settings.gate_plain_reasoning,
            "reason_reasoning": settings.gate_reason_reasoning,
            "active_gate": gate_mode(settings),
        },
        "summary": {arm: summarise(details, arm) for arm in ARMS},
        "heldout": {arm: summarise(details, arm, split="heldout") for arm in ARMS},
        "dev": {arm: summarise(details, arm, split="dev") for arm in ARMS},
        "paired": pairs,
        "holm": holm([(f"{item['left']} vs {item['right']}", item["mcnemar_p"]) for item in pairs]),
        "deployed": {arm: deployed(details, arm) for arm in ARMS},
        "cases": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    report = asyncio.run(run(args.limit or None))
    out = Path(__file__).resolve().parents[1] / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out / f"gate-compare-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)
    for arm, row in report["summary"].items():
        print(
            arm,
            f"{row['accuracy']:.1%}",
            row["wilson95"],
            f"p50 {row['latency_p50_ms']}ms",
            f"out {row['completion_tokens_mean']}",
            f"expensive {row['confident_expensive_errors']}",
        )


if __name__ == "__main__":
    main()
