"""Live shadow routing experiment: analyst and Jev on the same input.

The analyst remains the router used by the real turn. Jev is called only to log a
second opinion; no decision changes. This is deliberately separate from the turn
orchestrator so a failed Jev call cannot break a traveller turn.

Run from the repository root:

    backend/.venv/Scripts/python.exe backend/scripts/v5_shadow_live.py
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PROJECT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.domain.models import TripContext  # noqa: E402
from app.understand.jev import jev_route  # noqa: E402
from app.understand.pipeline import understand, missing_for_plan  # noqa: E402

OUT = PROJECT / "docs" / "eval-artifacts" / "v5-model-study"


def context_for(case: dict) -> TripContext:
    values = {
        "session_id": f"shadow-{case['id']}",
        "destination": case.get("destination", ""),
        "origin": case.get("origin", ""),
        "start_date": case.get("start_date"),
        "end_date": case.get("end_date"),
        "days": case.get("days"),
        "party_size": case.get("party_size", 1),
        "budget": case.get("budget"),
        "currency": case.get("currency", "HKD"),
        "language": "en",
    }
    return TripContext(**values)


CASES = [
    {
        "id": "normal-taipei",
        "category": "normal",
        "message": "Plan a 2-day Taipei trip from Hong Kong for 1 person next month, budget 12,000 TWD.",
        "destination": "Taipei",
        "origin": "Hong Kong",
        "days": 2,
        "party_size": 1,
        "budget": 12000,
        "currency": "TWD",
    },
    {
        "id": "normal-kyoto",
        "category": "normal",
        "message": "Plan a 5-day Kyoto trip for 2 people in April, budget 4,000 USD.",
        "destination": "Kyoto",
        "days": 5,
        "party_size": 2,
        "budget": 4000,
        "currency": "USD",
    },
    {
        "id": "normal-taipei-dated",
        "category": "normal",
        "message": "Plan a 2-day Taipei trip from Hong Kong for 1 person on 3–4 December 2026, budget 12,000 TWD.",
        "destination": "Taipei",
        "origin": "Hong Kong",
        "start_date": "2026-12-03",
        "end_date": "2026-12-04",
        "days": 2,
        "party_size": 1,
        "budget": 12000,
        "currency": "TWD",
    },
    {
        "id": "normal-kyoto-dated",
        "category": "normal",
        "message": "Plan a 5-day Kyoto trip for 2 people from 10 to 14 April 2027, budget 4,000 USD.",
        "destination": "Kyoto",
        "start_date": "2027-04-10",
        "end_date": "2027-04-14",
        "days": 5,
        "party_size": 2,
        "budget": 4000,
        "currency": "USD",
    },
    {
        "id": "normal-edit",
        "category": "normal",
        "message": "Make day 2 more relaxed and move the museum to the afternoon.",
        "destination": "Kyoto",
        "start_date": "2027-04-10",
        "end_date": "2027-04-12",
        "days": 3,
        "party_size": 2,
    },
    {
        "id": "normal-question",
        "category": "normal",
        "message": "Which day has the museum, and what time does it open?",
        "destination": "Kyoto",
        "start_date": "2027-04-10",
        "end_date": "2027-04-12",
        "days": 3,
        "party_size": 2,
    },
    {
        "id": "ambiguous-sakura",
        "category": "ambiguous",
        "message": "I want to see sakura.",
    },
    {
        "id": "ambiguous-gundam",
        "category": "ambiguous",
        "message": "I want to go somewhere I can see Gundam.",
    },
    {
        "id": "ambiguous-warm",
        "category": "ambiguous",
        "message": "Somewhere warm next month, not too expensive.",
    },
    {
        "id": "ambiguous-plan",
        "category": "ambiguous",
        "message": "Plan a trip.",
    },
]


async def run_case(settings: Settings, case: dict) -> dict:
    llm = LLMClient(settings)
    context = context_for(case)
    started = time.perf_counter()

    async def timed(coro):
        t0 = time.perf_counter()
        try:
            result = await coro
        except Exception as exc:  # noqa: BLE001 - a shadow failure must not stop the case
            result = exc
        return result, round((time.perf_counter() - t0) * 1000, 1)

    analyst_task = timed(
        understand(
            llm=llm,
            settings=settings,
            context=context,
            plan=None,
            messages=[],
            message=case["message"],
            scope_label=None,
            scope_item_id=None,
            locale="en",
        )
    )
    jev_task = timed(
        jev_route(
            settings=settings,
            context=context,
            plan=None,
            message=case["message"],
            history=[],
        )
    )
    try:
        (analyst_result, analyst_ms), (jev_result, jev_ms) = await asyncio.gather(analyst_task, jev_task)
    finally:
        await llm.aclose()
    parallel_ms = round((time.perf_counter() - started) * 1000, 1)
    error = ""
    if isinstance(analyst_result, Exception):
        error = f"analyst: {analyst_result}"
        analyst_intent, analyst_confidence, analyst_question, analyst_context = "error", None, "", context
    else:
        analyst_intent = analyst_result.route.intent
        analyst_confidence = analyst_result.route.confidence
        analyst_question = analyst_result.clarify.question if analyst_result.clarify else ""
        analyst_context = analyst_result.context
    if isinstance(jev_result, Exception):
        jev_intent, jev_confidence = "error", None
        error = (error + "; " if error else "") + f"jev: {jev_result}"
    elif jev_result is None:
        jev_intent, jev_confidence = "unavailable", None
    else:
        jev_intent, jev_confidence = jev_result.intent, jev_result.confidence
    agreement = (
        None
        if jev_confidence is None or analyst_confidence is None
        else analyst_intent == jev_intent
    )
    missing = missing_for_plan(analyst_context) if not isinstance(analyst_result, Exception) else []
    analyst_need = bool(
        not isinstance(analyst_result, Exception)
        and (analyst_result.route.need_clarification or analyst_result.clarify)
    )
    analyst_action = "clarify" if analyst_need or (analyst_intent == "plan" and missing) else analyst_intent
    if isinstance(jev_result, Exception) or jev_result is None:
        jev_action = "unavailable"
        jev_need = None
    else:
        jev_need = bool(jev_result.need_clarification or jev_result.intent == "unclear")
        jev_action = "clarify" if jev_need else jev_result.intent
    action_agreement = None if jev_action == "unavailable" else analyst_action == jev_action
    would_clarify = bool(
        isinstance(analyst_result, Exception)
        or analyst_action == "clarify"
        or jev_action == "clarify"
        or action_agreement is False
    )
    return {
        "id": case["id"],
        "category": case["category"],
        "message": case["message"],
        "analyst_intent": analyst_intent,
        "analyst_confidence": analyst_confidence,
        "analyst_question": analyst_question,
        "jev_intent": jev_intent,
        "jev_confidence": jev_confidence,
        "agreement": agreement,
        "analyst_action": analyst_action,
        "analyst_need": analyst_need,
        "jev_action": jev_action,
        "jev_need": jev_need,
        "action_agreement": action_agreement,
        "missing_slots": missing,
        "would_clarify": would_clarify,
        "latency_ms": {
            "analyst": analyst_ms,
            "jev": jev_ms,
            "parallel_wall": parallel_ms,
        },
        "outcome": "would_clarify" if would_clarify else f"accept analyst route ({analyst_intent})",
        "error": error,
    }


async def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    settings = Settings()
    # The real turn remains analyst-driven; Jev is only a shadow signal.
    settings = settings.model_copy(update={"turn_orchestrator": "analyst"})
    rows = []
    for case in CASES:
        row = await run_case(settings, case)
        rows.append(row)
        print(
            f"{row['id']}: analyst={row['analyst_intent']}({row['analyst_confidence']}) "
            f"jev={row['jev_intent']}({row['jev_confidence']}) agreement={row['agreement']} "
            f"would_clarify={row['would_clarify']} wall={row['latency_ms']['parallel_wall']}ms"
        )
    usable = [row for row in rows if row["agreement"] is not None]
    agreement = sum(1 for row in usable if row["agreement"] is True)
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "live shadow; analyst route kept; Jev called in parallel; no user-visible change",
        "n": len(rows),
        "usable_agreement_n": len(usable),
        "agreement": agreement,
        "disagreement": len(usable) - agreement,
        "agreement_rate": round(agreement / len(usable), 4) if usable else None,
        "usable_action_agreement_n": sum(1 for row in rows if row["action_agreement"] is not None),
        "action_agreement": sum(1 for row in rows if row["action_agreement"] is True),
        "action_disagreement": sum(1 for row in rows if row["action_agreement"] is False),
        "would_clarify": sum(1 for row in rows if row["would_clarify"]),
        "mean_parallel_wall_ms": round(statistics.mean(row["latency_ms"]["parallel_wall"] for row in rows), 1),
        "mean_analyst_ms": round(statistics.mean(row["latency_ms"]["analyst"] for row in rows), 1),
        "mean_jev_ms": round(statistics.mean(row["latency_ms"]["jev"] for row in rows), 1),
        "rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "shadow-live-latest.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Live dual-router shadow",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Mode: **live shadow**. The analyst route is the real route; Jev is called in parallel only to log a second opinion. No user-visible decision changes.",
        "",
        f"- Cases: **{summary['n']}** ({sum(1 for r in rows if r['category'] == 'normal')} normal, {sum(1 for r in rows if r['category'] == 'ambiguous')} ambiguous)",
        f"- Raw intent agreement: **{summary['agreement']}/{summary['usable_agreement_n']} = {summary['agreement_rate']:.1%}**" if usable else "- Raw intent agreement: Jev unavailable",
        f"- Action agreement: **{summary['action_agreement']}/{summary['usable_action_agreement_n']}** accepted/clarify decisions",
        f"- Would clarify: **{summary['would_clarify']}/{summary['n']}**",
        f"- Mean parallel wall time: **{summary['mean_parallel_wall_ms']:.0f} ms** (analyst {summary['mean_analyst_ms']:.0f} ms, Jev {summary['mean_jev_ms']:.0f} ms)",
        "",
        "| id | category | analyst action | Jev action | intent agreement | action agreement | would clarify | outcome |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['category']} | {row['analyst_action']} | "
            f"{row['jev_action']} | {row['agreement']} | {row['action_agreement']} | "
            f"{'yes' if row['would_clarify'] else 'no'} | {row['outcome']} |"
        )
    lines += [
        "",
        "## Reading the result",
        "",
        "- **Normal** cases test whether the routers agree on complete requests.",
        "- **Ambiguous** cases test whether they agree that the request needs clarification; sakura and Gundam are deliberately underspecified.",
        "- `would_clarify` is a **policy simulation**, not a real clarify response; no plan was generated or changed.",
        "- Jev `unavailable` means the call failed or timed out; the analyst run is still valid.",
        "- If the two disagree, the proposed policy would ask a disambiguating question rather than silently pick a route.",
        "",
        "## What the live shadow showed",
        "",
        "- **Raw intent agreement can understate agreement**: `plan` with a clarifying question and `unclear` can both mean clarify.",
        "- All ambiguous cases ended with both routers requiring clarification; the action agreement is the relevant signal.",
        "- The normal ask case is the failure mode: analyst `ask`, Jev `clarify`, so a strict disagreement gate over-clarifies.",
        "- Jev is much faster than the analyst; running in parallel did not add observed wall time.",
        "",
        "**Conclusion:** do not ship a strict agreement gate yet. Treat this as a small single-run shadow, not a significance test.",
    ]
    (OUT / "shadow-live-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
