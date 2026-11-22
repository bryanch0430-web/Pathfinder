"""Score the four regional sets with the rules reader. No writer, no live API."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.understanding import understand
from app.config import Settings
from app.domain.models import TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer

ROOT = Path(__file__).resolve().parents[1] / "eval" / "regional"
TODAY = date(2026, 3, 1)
REGIONS = ("hongkong", "taiwan", "japan", "china")


def _same(left: str, right: str) -> bool:
    return " ".join((left or "").casefold().split()) == " ".join((right or "").casefold().split())


async def score(region: str, gateway: LLMGateway) -> dict:
    path = ROOT / f"{region}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    hit = {"origin": 0, "destination": 0, "days": 0, "party": 0, "budget": 0, "wish_kept_out": 0}
    misses = []
    for row in rows:
        context = await understand(row["input"], TripContext(session_id="r"), gateway, today=TODAY)
        ok = {
            "origin": _same(context.origin, row["origin"]),
            "destination": _same(context.destination, row["destination"]),
            "days": context.days == row["days"],
            "party": context.party_size == row["party_size"],
            "budget": context.budget == row["budget"],
        }
        wish_ok = True
        if row["wish"]:
            wish_ok = row["wish"].casefold() not in (context.destination or "").casefold()
            ok["wish_kept_out"] = wish_ok
        else:
            ok["wish_kept_out"] = True
        for key, good in ok.items():
            hit[key] += int(good)
        if not all(ok.values()) and len(misses) < 5:
            misses.append(
                {
                    "id": row["id"],
                    "input": row["input"],
                    "gold": f"{row['origin']} -> {row['destination']}",
                    "read": f"{context.origin or '-'} -> {context.destination or '-'}",
                    "failed": [key for key, good in ok.items() if not good],
                }
            )
    total = len(rows)
    return {"region": region, "queries": total, "matched": hit, "sample_misses": misses}


async def main() -> None:
    settings = Settings()
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    reports = [await score(region, gateway) for region in REGIONS]
    for report in reports:
        total = report["queries"]
        print(report["region"], total)
        for key, count in report["matched"].items():
            print(f"  {key}: {count}/{total}")
        for miss in report["sample_misses"]:
            print("   ", miss["failed"], miss["read"], "|", miss["input"][:110])


if __name__ == "__main__":
    asyncio.run(main())
