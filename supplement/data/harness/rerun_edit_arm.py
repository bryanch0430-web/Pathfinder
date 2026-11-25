"""Re-measure the edit arm of the paired edit-versus-replan test on the proposed plan.

An edit is stored as a proposal until the traveller accepts it, so the saved plan after an edit turn is still the
seed plan. This script reuses the seeded trips in batch_b_paired.db, runs each edit again, reads the proposed plan
from the proposal event, and writes a corrected paired result next to the original.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"C:\Users\Dies Irae\Desktop\HKU\LLM\Pathfinder v5")
OUT = Path(__file__).resolve().parent.parent / "test_results"
os.environ.setdefault("PURPOSE_MODELS", '{"edit":"claude-sonnet-5.5","ask":"claude-sonnet-5.5","search_query":"claude-sonnet-5.5"}')
os.environ.setdefault("MAX_REPAIRS", "2")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline  # noqa: E402

offline.install()
os.chdir(REPO / "backend")

from app.config import get_settings  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.domain.models import ChatRequest  # noqa: E402
from app.turn.orchestrator import Dependencies, collect_turn  # noqa: E402


def names(plan: dict | None) -> list[str]:
    return [str(i.get("name", "")).strip() for d in (plan or {}).get("days", []) for i in d.get("items", []) if i.get("name")]


def hotels(plan: dict | None) -> list[str]:
    return [str(h.get("name", "")).strip() for h in (plan or {}).get("hotels", []) if h.get("name")]


async def main() -> None:
    paired_file = sorted(glob.glob(str(OUT / "paired_edit_replan-*.json")))[-1]
    paired = json.loads(Path(paired_file).read_text(encoding="utf-8"))
    store = Store(str(OUT / "batch_b_paired.db"))
    store.init()
    deps = Dependencies(settings=get_settings(), store=store)
    con_rows = {}
    import sqlite3
    con = sqlite3.connect(str(OUT / "batch_b_paired.db"))
    for sid, token in con.execute("select id, client_token from sessions"):
        con_rows[token] = sid
    con.close()
    for row in paired["rows"]:
        seed_sid = con_rows.get(f"seed-{row['id']}")
        seed_plan = await store.load_plan(seed_sid)
        before, hotels_before = names(seed_plan), hotels(seed_plan)
        target = row["target"]
        unnamed = [n for n in before if not target or target.lower() not in n.lower()]
        context = await store.load_context(seed_sid) or {}
        sid = (await store.create_session(client_token=f"edit2-{row['id']}"))["id"]
        await store.save_context(sid, {**context, "session_id": sid})
        await store.save_plan({**seed_plan, "session_id": sid})
        started = time.perf_counter()
        events = await collect_turn(deps, ChatRequest(session_id=sid, message=row["input"], locale="en", review=True),
                                    session_id=sid, user_message=row["input"])
        ms = (time.perf_counter() - started) * 1000
        proposal = next((e.data for e in events if e.event == "proposal"), None)
        proposed = ((proposal or {}).get("proposal") or {}).get("changes", [{}])[0].get("plan") if proposal else None
        trace = next((e.data for e in events if e.event == "trace"), {}) or {}
        gens = trace.get("generations", []) or []
        after = {n.lower() for n in names(proposed)}
        kept = sum(1 for n in unnamed if n.lower() in after)
        if target:
            applied = bool(proposed) and not any(target.lower() in n for n in after)
        else:
            applied = bool(proposed) and set(hotels(proposed)) != set(hotels_before)
        reply = "".join(e.data.get("text", "") for e in events if e.event == "token")
        row["edit"] = {"calls": len(gens), "tokens": sum(int(g.get("prompt_tokens", 0)) + int(g.get("completion_tokens", 0))
                                                         for g in gens),
                       "models": sorted({str(g.get("model", "")) for g in gens}), "latency_ms": round(ms, 1),
                       "proposal": bool(proposed), "stops_after": len(after), "unnamed_kept": kept,
                       "unnamed_total": len(unnamed), "kept_share": round(kept / max(1, len(unnamed)), 3),
                       "change_applied": applied, "hotels_after": hotels(proposed), "reply": reply[:400]}
        row["valid"] = row["seed"]["calls"] > 0 and row["edit"]["calls"] > 0 and row["replan"]["calls"] > 0
        print(f"  {row['id']} proposal={bool(proposed)} kept={row['edit']['kept_share']:.0%} applied={applied} "
              f"calls={len(gens)} tokens={row['edit']['tokens']} {ms / 1000:.0f}s", flush=True)
    await store.close()
    paired["note"] = "Edit arm re-measured on the proposed plan carried in the proposal event."
    out = OUT / f"paired_edit_replan-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(paired, indent=2, ensure_ascii=False), encoding="utf-8")
    print("saved", out)
    offline.report()


if __name__ == "__main__":
    asyncio.run(main())
