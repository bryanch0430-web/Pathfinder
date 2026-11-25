"""30 TravelPlanner test queries through the current Pathfinder pipeline.

10 easy, 10 medium, 10 hard, from the public test split (part of the 1,225).
The query is the only user message. Scoring uses the row's dates, party, budget,
local constraints, and the cities and place names in reference_information.

Usage:
    PYTHONPATH=backend .venv/bin/python ../report_material/evaluation/run_tp30.py --limit 1
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ["PATHFINDER_EVAL_TODAY"] = "2022-03-01T12:00:00+08:00"

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "Pathfinder" / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import get_settings  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.domain.models import ChatRequest, TripContext  # noqa: E402
from app.turn.orchestrator import Dependencies, collect_turn  # noqa: E402

DATA = Path(__file__).resolve().parent / "data" / "test.csv"
OUT = Path(__file__).resolve().parent / "tp30-results.json"
RUNS = Path(__file__).resolve().parent / "runs"


def catalogue(reference: list[dict]) -> dict:
    cities: list[str] = []
    attractions: list[str] = []
    restaurants: list[dict[str, str]] = []
    stays: list[dict[str, str]] = []
    for block in reference:
        description = str(block.get("Description") or "")
        content = str(block.get("Content") or "")
        lines = [line for line in content.splitlines() if line.strip()]
        kind, _, city = description.partition(" in ")
        if kind == "Attractions" and city and len(lines) > 1:
            cities.append(city.strip())
            for line in lines[1:]:
                match = re.match(r"\s*(.+?)\s+-?\d+\.\d+\s+-?\d+\.\d+\s+", line)
                if match:
                    attractions.append(match.group(1).strip())
        elif kind == "Restaurants" and city and len(lines) > 1:
            for line in lines[1:]:
                text = line.strip()
                if text.endswith(city.strip()):
                    text = text[: -len(city.strip())].strip()
                match = re.match(r"(.+?)\s+(\d+)\s+(.+)\s+(\d+\.\d+)$", text)
                if match:
                    restaurants.append({"name": match.group(1).strip(), "city": city.strip(), "cuisines": match.group(3).strip()})
        elif kind == "Accommodations" and city and len(lines) > 1:
            for line in lines[1:]:
                match = re.match(
                    r"(.+?)\s+(\d+(?:\.\d+)?)\s+(Entire home/apt|Private room|Shared room)\s+(.+?)\s+\d+\.\d+\s+\d+(?:\.\d+)?\s+\d+\.\d+\s+\S",
                    line.strip(),
                )
                if match:
                    stays.append(
                        {
                            "name": match.group(1).strip(),
                            "city": city.strip(),
                            "room": match.group(3).strip(),
                            "rules": match.group(4).strip(),
                        }
                    )
    return {
        "cities": list(dict.fromkeys(cities)),
        "attractions": attractions,
        "restaurants": restaurants,
        "stays": stays,
    }


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", value.lower())).strip()


def named(name: str, candidates: list[str]) -> bool:
    left = norm(name)
    if len(left) < 4:
        return False
    for candidate in candidates:
        right = norm(candidate)
        if len(right) < 4:
            continue
        if left == right or left in right or right in left:
            return True
    return False


def _constraint_text(local: dict) -> list[str]:
    text: list[str] = ["stay within budget"]
    rule = local.get("house rule")
    if rule:
        text.append(f"accommodation must allow {rule}")
    cuisines = local.get("cuisine") or []
    if cuisines:
        text.append("meals must include these cuisines: " + ", ".join(cuisines))
    room = local.get("room type")
    if room:
        text.append(f"room type must be {room}")
    transport = local.get("transportation")
    if transport:
        text.append(transport)
    return text


def load_rows() -> list[dict]:
    with DATA.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for index, row in enumerate(rows, start=1):
        row["source_row"] = index
    return rows


def sample(rows: list[dict]) -> list[dict]:
    rng = random.Random(20261124)
    chosen: list[dict] = []
    quota = {1: 4, 2: 3, 3: 3}
    for level in ("easy", "medium", "hard"):
        buckets: dict[int, list[dict]] = {1: [], 2: [], 3: []}
        for row in rows:
            if row["level"] == level:
                buckets[int(row["visiting_city_number"])].append(row)
        for bucket in buckets.values():
            rng.shuffle(bucket)
        used: set[tuple[str, str]] = set()
        picked: list[dict] = []
        for cities, count in quota.items():
            got = 0
            for row in buckets[cities]:
                key = (row["org"], row["dest"])
                if key in used:
                    continue
                used.add(key)
                picked.append(row)
                got += 1
                if got == count:
                    break
        for index, row in enumerate(picked, start=1):
            row = dict(row)
            row["case_id"] = f"TP-{level[:1].upper()}{index:02d}"
            chosen.append(row)
    return chosen


def blob(plan: dict | None) -> str:
    if not plan:
        return ""
    return json.dumps(
        {
            "destination": plan.get("destination"),
            "days": [
                {"city": day.get("city"), "items": [item.get("name") for item in day.get("items") or []]}
                for day in plan.get("days") or []
            ],
            "hotels": [hotel.get("name") for hotel in plan.get("hotels") or []],
            "summary": plan.get("summary"),
        },
        ensure_ascii=False,
    ).lower()


def item_text(item: dict) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("name", "notes", "cuisine", "address", "source_summary")
    ) + " " + " ".join(item.get("tags") or [])


def hotel_text(hotel: dict) -> str:
    match = hotel.get("match") or {}
    rooms = " ".join(str(room.get("name") or "") for room in hotel.get("room_types") or [])
    return " ".join(
        [
            str(hotel.get("name") or ""),
            str(hotel.get("notes") or ""),
            str(hotel.get("area") or ""),
            " ".join(hotel.get("amenities") or []),
            str(match.get("reason") or ""),
            " ".join(match.get("wishes") or []),
            " ".join(match.get("evidence") or []),
            rooms,
        ]
    )


def check_case(row: dict, plan: dict | None, route: dict, trace: dict) -> dict:
    local = ast.literal_eval(row["local_constraint"])
    reference = ast.literal_eval(row["reference_information"])
    book = catalogue(reference)
    dates = ast.literal_eval(row["date"])
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    intent = str(route.get("intent") or "")
    add("plan_path", intent == "plan" and plan is not None, f"router said {intent or 'nothing'}; plan {'saved' if plan else 'missing'}")
    if not plan:
        return {"checks": checks, "satisfied": False, "reference_cities": book["cities"], "tools": trace.get("tools") or []}

    add("days", len(plan.get("days") or []) == int(row["days"]), f"{len(plan.get('days') or [])} days, expected {row['days']}")
    start = ((plan.get("dates") or {}).get("start") or "")[:10]
    add("dates", start == dates[0], f"plan starts {start or 'unset'}, expected {dates[0]}")
    add("party", int(plan.get("party_size") or 0) == int(row["people_number"]), f"plan party {plan.get('party_size')}, expected {row['people_number']}")

    text = blob(plan)
    missing = [city for city in book["cities"] if city.lower() not in text]
    add("reference_cities", not missing, "cities in the reference: " + ", ".join(book["cities"]) + (f"; missing {', '.join(missing)}" if missing else ""))

    cost = plan.get("cost") or {}
    total = float(cost.get("total") or 0)
    status = str(cost.get("status") or "unknown")
    currency = str(cost.get("currency") or "")
    budget = float(row["budget"])
    budget_ok = currency in {"", "USD"} and (status == "within" or (total > 0 and total <= budget))
    add("budget", budget_ok, f"{currency or '?'} {total:.0f} / {budget:.0f} ({status})")

    meals = [item for day in plan.get("days") or [] for item in day.get("items") or [] if item.get("type") == "meal"]
    sights = [item for day in plan.get("days") or [] for item in day.get("items") or [] if item.get("type") == "attraction"]
    hotels = list(plan.get("hotels") or [])
    tickets = list(plan.get("tickets") or [])

    cuisines = local.get("cuisine") or []
    if cuisines:
        found = []
        for cuisine in cuisines:
            hit = False
            for meal in meals:
                if cuisine.lower() in item_text(meal).lower():
                    hit = True
                    break
                match = next((place for place in book["restaurants"] if named(meal.get("name") or "", [place["name"]])), None)
                if match and cuisine.lower() in match["cuisines"].lower():
                    hit = True
                    break
            found.append(hit)
        add("cuisine", all(found), "wanted " + ", ".join(cuisines))

    rule = local.get("house rule")
    if rule:
        banned = {
            "smoking": "no smoking",
            "parties": "no parties",
            "children under 10": "no children under 10",
            "visitors": "no visitors",
            "pets": "no pets",
        }[rule]
        positive = {
            "smoking": "smoking allowed",
            "parties": "parties allowed",
            "children under 10": "children welcome",
            "visitors": "visitors allowed",
            "pets": "pet",
        }[rule]
        seen = False
        broken = False
        for hotel in hotels:
            rules = ""
            match = next((stay for stay in book["stays"] if named(hotel.get("name") or "", [stay["name"]])), None)
            if match:
                rules = match["rules"].lower()
                seen = True
                if banned in rules:
                    broken = True
            else:
                own = hotel_text(hotel).lower()
                if banned in own:
                    broken = True
                    seen = True
                elif positive in own:
                    seen = True
        add("house_rule", seen and not broken, f"need accommodation that allows {rule}")

    room = local.get("room type")
    if room:
        wanted = {
            "entire room": "entire",
            "private room": "private",
            "shared room": "shared",
            "not shared room": "not shared",
        }[room]
        ok = False
        for hotel in hotels:
            match = next((stay for stay in book["stays"] if named(hotel.get("name") or "", [stay["name"]])), None)
            label = (match["room"] if match else hotel_text(hotel)).lower()
            if wanted == "not shared":
                ok = ok or ("shared" not in label and ("private" in label or "entire" in label))
            else:
                ok = ok or wanted in label
        add("room_type", ok, f"need {room}")

    transport = local.get("transportation")
    ticket_text = " ".join(
        f"{ticket.get('mode') or ''} {ticket.get('name') or ''} {' '.join(ticket.get('tags') or [])}" for ticket in tickets
    ).lower()
    if transport == "no flight":
        add("transport", bool(tickets) and "flight" not in ticket_text, "the trip must not fly")
    elif transport == "no self-driving":
        driving = any(word in ticket_text for word in ("drive", "self-driving", "self driving"))
        add("transport", bool(tickets) and not driving, "the trip must not be self-driving")

    tools = trace.get("tools") or []
    by_name = {tool.get("name"): tool.get("status") for tool in tools}
    for worker in ("attractions", "hotels", "tickets"):
        tool_status = by_name.get(worker)
        add(f"tool_{worker}", tool_status in {"ok", "partial"}, f"{worker}: {tool_status or 'not called'}")

    sourced_items = [item for day in plan.get("days") or [] for item in day.get("items") or []]
    sourced = sum(1 for item in [*sourced_items, *hotels, *tickets] if item.get("source_refs"))
    total_items = len(sourced_items) + len(hotels) + len(tickets)
    grounding = sourced / total_items if total_items else 0
    add("grounded", grounding >= 0.8, f"{sourced}/{total_items} stops cite a tool result")

    place_names = [item.get("name") or "" for item in [*sights, *meals, *hotels]]
    catalogue_names = [*book["attractions"], *[place["name"] for place in book["restaurants"]], *[stay["name"] for stay in book["stays"]]]
    matched = [name for name in place_names if named(name, catalogue_names)]
    return {
        "checks": checks,
        "satisfied": all(item["ok"] for item in checks),
        "reference_cities": book["cities"],
        "tools": [{"name": tool.get("name"), "status": tool.get("status"), "note": tool.get("note")} for tool in tools],
        "grounding": round(grounding, 3),
        "catalogue_places": {"matched": len(matched), "named": len(place_names), "examples": matched[:6]},
        "cost": {"total": total, "status": status, "currency": currency, "budget": budget},
        "stops": place_names[:12],
    }


def self_check() -> None:
    rows = load_rows()
    row = next(item for item in rows if "2 cities in Texas" in item["query"] and item["level"] == "easy")
    book = catalogue(ast.literal_eval(row["reference_information"]))
    assert book["cities"] == ["Houston", "Dallas"], book["cities"]
    chicago = next(item for item in rows if item["dest"] == "Chicago" and item["org"] == "Sarasota")
    chicago_book = catalogue(ast.literal_eval(chicago["reference_information"]))
    assert "Navy Pier" in chicago_book["attractions"], chicago_book["attractions"][:3]
    assert "Cafe" in chicago_book["restaurants"][0]["cuisines"], chicago_book["restaurants"][0]
    assert "No " in chicago_book["stays"][0]["rules"], chicago_book["stays"][0]
    assert "Entire" in chicago_book["stays"][0]["room"] or "Private" in chicago_book["stays"][0]["room"]
    print("parser ok", chicago_book["cities"], chicago_book["attractions"][0], chicago_book["stays"][0]["room"])


async def run_one(row: dict, settings) -> dict:
    db = RUNS / f"{row['case_id']}.db"
    if db.exists():
        db.unlink()
    store = Store(str(db))
    store.init()
    started = time.perf_counter()
    try:
        session = await store.create_session(client_token=row["case_id"])
        session_id = session["id"]
        dates = ast.literal_eval(row["date"])
        local = ast.literal_eval(row["local_constraint"])
        context = TripContext(
            session_id=session_id,
            origin=row["org"],
            destination=row["dest"],
            start_date=dates[0],
            end_date=dates[-1],
            days=int(row["days"]),
            party_size=int(row["people_number"]),
            budget=float(row["budget"]),
            currency="USD",
            hard_constraints=_constraint_text(local),
            language="en",
        )
        await store.save_context(session_id, context.model_dump())
        deps = Dependencies(settings=settings, store=store)

        async def ask(text: str):
            return await asyncio.wait_for(
                collect_turn(
                    deps,
                    ChatRequest(session_id=session_id, message=text, locale="en", review=True),
                    session_id=session_id,
                    user_message=text,
                ),
                timeout=300,
            )

        events = await ask(row["query"])
        plan = await store.load_plan(session_id)
        follow_up = ""
        if plan is None and any(event.event == "clarify" for event in events):
            follow_up = (
                f"USD. {row['people_number']} people. From {row['org']} to {row['dest']}, "
                f"{dates[0]} to {dates[-1]}, {row['days']} days, budget {row['budget']} USD. Please make the plan."
            )
            events = await ask(follow_up)
            plan = await store.load_plan(session_id)
        route = next((event.data for event in events if event.event == "route"), {})
        trace = next((event.data for event in reversed(events) if event.event == "trace"), {})
        clarify = next((event.data for event in events if event.event == "clarify"), {})
        error = next((event.data for event in events if event.event == "error"), None)
        scored = check_case(row, plan, route, trace)
        scored.update(
            {
                "case_id": row["case_id"],
                "level": row["level"],
                "source_row": row["source_row"],
                "org": row["org"],
                "dest": row["dest"],
                "days": int(row["days"]),
                "cities": int(row["visiting_city_number"]),
                "people": int(row["people_number"]),
                "budget": float(row["budget"]),
                "local_constraint": ast.literal_eval(row["local_constraint"]),
                "query": row["query"],
                "intent": route.get("intent"),
                "follow_up": follow_up,
                "clarify": (clarify or {}).get("question", ""),
                "error": (error or {}).get("message", "") if error else "",
                "latency_s": round(time.perf_counter() - started, 1),
            }
        )
        return scored
    except Exception as exc:  # noqa: BLE001
        return {
            "case_id": row["case_id"],
            "level": row["level"],
            "source_row": row["source_row"],
            "query": row["query"],
            "org": row["org"],
            "dest": row["dest"],
            "satisfied": False,
            "error": str(exc)[:400],
            "checks": [{"name": "run", "ok": False, "detail": str(exc)[:240]}],
            "latency_s": round(time.perf_counter() - started, 1),
        }
    finally:
        await store.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    self_check()
    RUNS.mkdir(parents=True, exist_ok=True)
    chosen = sample(load_rows())[: args.limit]
    settings = get_settings()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "clock": "2022-03-01",
        "model": settings.llm_model or settings.openai_model,
        "verifier": settings.verifier_model,
        "sandbox": settings.sandbox,
        "split": "test",
        "n": len(chosen),
        "cases": [],
    }
    semaphore = asyncio.Semaphore(args.concurrency)

    async def guarded(row: dict) -> dict:
        async with semaphore:
            print(f"start {row['case_id']} {row['level']} {row['org']} -> {row['dest']}", flush=True)
            result = await run_one(row, settings)
            mark = "PASS" if result.get("satisfied") else "FAIL"
            print(f"{mark} {row['case_id']} {result.get('latency_s')}s {result.get('error') or result.get('intent')}", flush=True)
            return result

    tasks = [asyncio.create_task(guarded(row)) for row in chosen]
    for task in asyncio.as_completed(tasks):
        payload["cases"].append(await task)
        payload["cases"].sort(key=lambda item: item.get("case_id", ""))
        OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    passed = sum(1 for case in payload["cases"] if case.get("satisfied"))
    print(f"done {passed}/{len(payload['cases'])} satisfied", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
