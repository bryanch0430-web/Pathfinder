"""Smoke-run the 19 Tier A corpus-ready TravelPlanner cases through v5."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import csv

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.deps import Container  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.domain.models import ChatRequest, TripContext  # noqa: E402
from app.turn.orchestrator import Dependencies, collect_turn  # noqa: E402

OUT = PROJECT / "docs" / "eval-artifacts" / "autotest"
ENV = PROJECT / ".env.autotest"


def env_values():
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def has_local(value):
    try:
        return any(v not in (None, "", [], "None") for v in ast.literal_eval(value).values())
    except Exception:
        return str(value) not in ("", "{}", "None")


def first_date(value):
    try:
        parsed = ast.literal_eval(value)
        return parsed[0] if isinstance(parsed, list) and parsed else None
    except Exception:
        return None


async def main():
    env = env_values()
    model = env.get("AUTOTEST_WRITER_MODEL", "claude-sonnet-5-5")
    base_url = env.get("AUTOTEST_BASE_URL", "https://api.chatanywhere.org/v1")
    key = env["AUTOTEST_API_KEY"]
    settings = get_settings().model_copy(update={
        "llm_provider": "openai", "openai_base_url": base_url, "openai_api_key": key,
        "openai_model": model, "llm_model": model,
        "purpose_models": {"understand": model, "plan": model, "edit": model, "ask": model, "memory": model},
        "llm_temperature": 0.2, "sandbox": True,
    })
    rows = []
    for source_split in ("train", "validation", "test"):
        with (PROJECT / "docs" / "report" / "source-data" / "travelplanner" / f"{source_split}.csv").open(encoding="utf-8", newline="") as fh:
            for index, row in enumerate(csv.DictReader(fh), start=1):
                if int(row["visiting_city_number"]) != 1 or int(row["people_number"]) > 4:
                    continue
                if has_local(row["local_constraint"]):
                    continue
                if row["dest"] not in {"New York", "San Francisco"}:
                    continue
                row["source_split"] = source_split
                row["source_row"] = index
                rows.append(row)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "tier": "A corpus-ready", "n": len(rows), "cases": []}
    semaphore = asyncio.Semaphore(2)

    async def one(row):
        async with semaphore:
            db = PROJECT / "backend" / "data" / f"tp_tier_a_{row['source_split']}_{row['source_row']}.db"
            store = Store(str(db)); store.init()
            sid = (await store.create_session(client_token=f"tp-{row['source_split']}-{row['source_row']}"))["id"]
            context = TripContext(
                session_id=sid, destination=row["dest"], origin=row["org"], start_date=first_date(row["date"]),
                days=int(row["days"]), party_size=int(row["people_number"]), budget=float(row["budget"]), currency="USD",
            )
            await store.save_context(sid, context.model_dump())
            deps = Dependencies(settings=settings, store=store)
            try:
                events = await collect_turn(
                    deps,
                    ChatRequest(session_id=sid, message=str(row["query"]), locale="en", review=False),
                    session_id=sid, user_message=str(row["query"]),
                )
                plan = await store.load_plan(sid)
                trace = next((event.data for event in events if event.event == "trace"), {})
                return {
                    "uid": f"TP-{row['source_split'].upper()}-{int(row['source_row']):04d}",
                    "destination": row["dest"], "days": int(row["days"]), "people": int(row["people_number"]),
                    "has_plan": bool(plan), "day_count": len((plan or {}).get("days", [])),
                    "constraints": (plan or {}).get("constraints", []),
                    "why": trace.get("why", [])[:5],
                }
            except Exception as exc:  # noqa: BLE001
                return {"uid": f"TP-{row['source_split'].upper()}-{int(row['source_row']):04d}", "has_plan": False, "error": str(exc)[:300]}
            finally:
                await store.close()

    for i, row in enumerate(rows):
        print(f"[{i+1}/{len(rows)}] {row['source_split']}:{row['source_row']} {row['dest']}", flush=True)
        out["cases"].append(await one(row))
    path = OUT / f"travelplanner-tier-a-{stamp}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "travelplanner-tier-a-latest.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("done", len(rows), sum(1 for c in out["cases"] if c.get("has_plan")))


if __name__ == "__main__":
    asyncio.run(main())
