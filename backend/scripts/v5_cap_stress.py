"""Cap stress: one low cap and one effective cap per part, same model and case."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.deps import Container  # noqa: E402
from app.application.memory import derive_memory  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.domain.models import ChatMessage, ChatRequest, TripContext, TripPlan  # noqa: E402
from app.eval import datasets  # noqa: E402
from app.plan.validator import audit_constraints  # noqa: E402
from app.turn.orchestrator import Dependencies, collect_turn  # noqa: E402
from app.understand.pipeline import understand  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-artifacts" / "v5-model-study"
FIXTURES = Path("eval/fixtures/v5_study")
MODEL = "gemini-3.5-flash-lite"


async def analyst_case(cap: int) -> dict:
    settings = get_settings().model_copy(
        update={"purpose_models": {"understand": MODEL}, "purpose_max_tokens": {"understand": cap}}
    )
    case = datasets.load("routing")[0]
    llm = LLMClient(settings)
    trace = TurnTrace(request_id=f"cap-analyst-{cap}")
    try:
        result = await understand(
            llm=llm, settings=settings,
            context=TripContext(session_id="cap", destination=case.destination, origin=case.origin),
            plan=None, messages=[], message=case.input, scope_label=None, scope_item_id=None, locale="en",
        )
        return {"ok": True, "detail": result.parsed.intent, "tokens": trace.total_tokens}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:300], "tokens": trace.total_tokens}
    finally:
        await llm.aclose()


async def ask_case(cap: int) -> dict:
    settings = get_settings().model_copy(
        update={"purpose_models": {"ask": MODEL}, "purpose_max_tokens": {"ask": cap}, "purpose_temperatures": {"ask": 0.0}}
    )
    plan_raw = json.loads((FIXTURES / "kyoto_plan.json").read_text(encoding="utf-8"))
    context_raw = json.loads((FIXTURES / "kyoto_context.json").read_text(encoding="utf-8"))
    store = Store(f"data/cap_ask_{cap}.db")
    store.init()
    session = await store.create_session(client_token=f"cap-ask-{cap}")
    sid = session["id"]
    await store.save_context(sid, TripContext(**{**context_raw, "session_id": sid}).model_dump())
    await store.save_plan(TripPlan(**{**plan_raw, "session_id": sid, "trip_id": f"trip-{sid}"}).model_dump(mode="json"))
    deps = Dependencies(settings=settings, store=store)
    started = time.perf_counter()
    try:
        events = await collect_turn(
            deps, ChatRequest(session_id=sid, message="What time does Kiyomizu-dera open?", locale="en", review=False),
            session_id=sid, user_message="cap",
        )
        reply = "".join(event.data.get("text", "") for event in events if event.event == "token")
        return {"ok": "6:00" in reply.lower(), "detail": reply[:300], "tokens": None, "latency_ms": round((time.perf_counter()-started)*1000)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:300], "tokens": None, "latency_ms": round((time.perf_counter()-started)*1000)}
    finally:
        await store.close()


async def verifier_case(cap: int) -> dict:
    settings = get_settings().model_copy(
        update={"purpose_models": {"verify": MODEL}, "purpose_max_tokens": {"verify": cap}}
    )
    plan_raw = json.loads((FIXTURES / "kyoto_plan.json").read_text(encoding="utf-8"))
    plan = TripPlan(**plan_raw)
    plan.constraints[0].text = "Keep the total under 100000 JPY"
    context = TripContext(session_id="cap-verify", destination="Kyoto", origin="Hong Kong",
                          start_date="2026-12-05", end_date="2026-12-07", days=2, party_size=2,
                          budget=100000, currency="JPY")
    llm = LLMClient(settings)
    try:
        result = await audit_constraints(llm=llm, plan=plan, context=context)
        return {"ok": bool(result and result.checks and result.checks[0].status == "pass"),
                "detail": result.checks[0].status if result and result.checks else "unknown", "tokens": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:300], "tokens": None}
    finally:
        await llm.aclose()


async def memory_case(cap: int) -> dict:
    settings = get_settings().model_copy(
        update={"purpose_models": {"memory": MODEL}, "purpose_max_tokens": {"memory": cap}, "purpose_temperatures": {"memory": 0.0}}
    )
    cases = json.loads((FIXTURES / "memory_cases.json").read_text(encoding="utf-8"))
    case = cases[0]
    plan = TripPlan(
        trip_id="cap-memory", session_id="cap-memory", destination=__import__("app.domain.models", fromlist=["Destination"]).Destination(name="Kyoto"),
        summary=case["summary"],
    )
    context = TripContext(session_id="cap-memory", destination="Kyoto", days=3)
    messages = [ChatMessage(id=f"m{i}", role="user", content=text) for i, text in enumerate(case["messages"])]
    llm = LLMClient(settings)
    try:
        draft = await derive_memory(llm=llm, plan=plan, context=context, messages=messages)
        prefs = list(draft.preferences) if draft else []
        return {"ok": len(prefs) >= 2, "detail": "; ".join(prefs)[:300], "tokens": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:300], "tokens": None}
    finally:
        await llm.aclose()


async def planner_case(cap: int) -> dict:
    settings = get_settings().model_copy(
        update={"purpose_models": {"plan": MODEL}, "purpose_max_tokens": {"plan": cap}, "purpose_temperatures": {"plan": 0.0}}
    )
    store = Store(f"data/cap_plan_{cap}.db")
    store.init()
    session = await store.create_session(client_token=f"cap-plan-{cap}")
    sid = session["id"]
    context = TripContext(
        session_id=sid, destination="Kyoto", origin="Hong Kong", start_date="2026-12-05",
        end_date="2026-12-07", days=2, party_size=2, budget=100000, currency="JPY",
    )
    await store.save_context(sid, context.model_dump())
    deps = Dependencies(settings=settings, store=store)
    started = time.perf_counter()
    try:
        events = await collect_turn(
            deps, ChatRequest(session_id=sid, message="Plan a 2-day Kyoto trip for 2 from Hong Kong, budget 100000 JPY.", locale="en", review=False),
            session_id=sid, user_message="cap",
        )
        plan = await store.load_plan(sid)
        return {"ok": bool(plan and plan.get("days")), "detail": f"{len(plan.get('days', [])) if plan else 0} days",
                "tokens": None, "latency_ms": round((time.perf_counter()-started)*1000)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:300], "tokens": None, "latency_ms": round((time.perf_counter()-started)*1000)}
    finally:
        await store.close()


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, function, low, high in (
        ("analyst", analyst_case, 64, 4096),
        ("ask", ask_case, 64, 1400),
        ("verifier", verifier_case, 64, 1500),
        ("memory", memory_case, 64, 700),
        ("planner", planner_case, 128, 12000),
    ):
        results[name] = {}
        for label, cap in (("low", low), ("effective", high)):
            started = time.perf_counter()
            outcome = await function(cap)
            outcome["cap"] = cap
            outcome["seconds"] = round(time.perf_counter() - started, 1)
            results[name][label] = outcome
            print(f"{name} {label} cap={cap} ok={outcome['ok']} {outcome['detail'][:100]}", flush=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = OUT / f"cap-stress-{stamp}.json"
    target.write_text(json.dumps(results, indent=2), encoding="utf-8")
    (OUT / "cap-stress-latest.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    lines = ["# v5 cap stress", "", "| Part | Low cap | Low result | Effective cap | Effective result |", "|---|---:|---|---:|---|"]
    for part, rows in results.items():
        low, high = rows["low"], rows["effective"]
        lines.append(f"| {part} | {low['cap']} | {'PASS' if low['ok'] else 'FAIL'} — {low['detail'][:80]} | {high['cap']} | {'PASS' if high['ok'] else 'FAIL'} — {high['detail'][:80]} |")
    (OUT / "cap-stress-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(target, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
