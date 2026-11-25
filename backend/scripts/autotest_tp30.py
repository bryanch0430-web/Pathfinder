"""30-case stratified TravelPlanner pilot with a fixed 2022 evaluation clock."""

from __future__ import annotations

import ast
import asyncio
import csv
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
os.environ["PATHFINDER_EVAL_TODAY"] = "2022-03-01T12:00:00+08:00"
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


def strate(row):
    tier = "A" if int(row["visiting_city_number"]) == 1 and int(row["people_number"]) <= 4 else "B"
    party = "small" if int(row["people_number"]) <= 4 else "large"
    corpus = "corpus" if row["dest"] in {"New York", "San Francisco"} else "provider"
    return (tier, row["level"], int(row["days"]), party, corpus)


def sample(rows, n=30):
    rng = random.Random(20261123)
    buckets = {}
    for row in rows:
        buckets.setdefault(strate(row), []).append(row)
    for values in buckets.values():
        rng.shuffle(values)
    # Proportional tier allocation: 10 A + 20 B, round-robin over strata.
    selected = []
    for tier, count in (("A", 10), ("B", 20)):
        keys = sorted(k for k in buckets if k[0] == tier)
        index = 0
        while len([r for r in selected if strate(r)[0] == tier]) < count:
            progressed = False
            for key in keys:
                if index < len(buckets[key]) and len([r for r in selected if strate(r)[0] == tier]) < count:
                    selected.append(buckets[key][index])
                    progressed = True
            if not progressed:
                break
            index += 1
    return selected


async def main():
    env = env_values()
    model = env.get("AUTOTEST_WRITER_MODEL", "claude-sonnet-5-5")
    verifier = env.get("AUTOTEST_VERIFIER_MODEL", "deepseek-v4.1-flash")
    base_url = env.get("AUTOTEST_BASE_URL", "https://api.chatanywhere.org/v1")
    key = env["AUTOTEST_API_KEY"]
    settings = get_settings().model_copy(update={
        "llm_provider": "openai", "openai_base_url": base_url, "openai_api_key": key,
        "openai_model": model, "llm_model": model,
        "purpose_models": {"understand": model, "plan": model, "verify": verifier},
        "verifier_base_url": base_url, "verifier_api_key": key, "verifier_model": verifier,
        "llm_temperature": 0.2, "sandbox": True,
    })
    rows = []
    for split in ("train", "validation", "test"):
        with (PROJECT / "docs" / "report" / "source-data" / "travelplanner" / f"{split}.csv").open(encoding="utf-8", newline="") as fh:
            for index, row in enumerate(csv.DictReader(fh), start=1):
                if has_local(row["local_constraint"]):
                    continue
                row["source_split"] = split
                row["source_row"] = index
                rows.append(row)
    chosen = sample(rows, 30)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "clock": "2022-03-01T12:00:00+08:00", "population": len(rows), "n": len(chosen), "cases": []}
    semaphore = asyncio.Semaphore(2)
    print("sample", len(chosen), "of", len(rows), flush=True)

    async def one(row):
        async with semaphore:
            db = PROJECT / "backend" / "data" / f"tp30_{row['source_split']}_{row['source_row']}.db"
            store = Store(str(db)); store.init()
            sid = (await store.create_session(client_token=f"tp30-{row['source_split']}-{row['source_row']}"))["id"]
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
                    "stratum": list(strate(row)), "destination": row["dest"],
                    "days": int(row["days"]), "people": int(row["people_number"]),
                    "has_plan": bool(plan), "day_count": len((plan or {}).get("days", [])),
                    "constraints": (plan or {}).get("constraints", []),
                    "why": trace.get("why", [])[:5],
                }
            except Exception as exc:  # noqa: BLE001
                return {"uid": f"TP-{row['source_split'].upper()}-{int(row['source_row']):04d}", "stratum": list(strate(row)), "has_plan": False, "error": str(exc)[:300]}
            finally:
                await store.close()

    for i, row in enumerate(chosen):
        print(f"[{i+1}/{len(chosen)}] {row['source_split']}:{row['source_row']} {row['dest']}", flush=True)
        payload["cases"].append(await one(row))
        (OUT / "travelplanner-pilot30-latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (OUT / f"travelplanner-pilot30-{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print("done", sum(1 for c in payload["cases"] if c.get("has_plan")), "/", len(payload["cases"]))


if __name__ == "__main__":
    asyncio.run(main())
