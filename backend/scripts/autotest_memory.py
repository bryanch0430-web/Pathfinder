"""Auto-test the 8 memory cases through ChatAnywhere."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.application.memory import derive_memory  # noqa: E402
from v5_memory_bench import build, hit  # noqa: E402

OUT = PROJECT / "docs" / "eval-artifacts" / "autotest"
FIX = PROJECT / "backend" / "eval" / "fixtures" / "v5_study"


def env_values():
    values = {}
    for line in (PROJECT / ".env.autotest").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


async def main():
    env = env_values()
    model = env.get("AUTOTEST_MEMORY_MODEL", "claude-sonnet-5-5")
    settings = get_settings().model_copy(
        update={
            "llm_provider": "openai",
            "openai_base_url": env.get("AUTOTEST_BASE_URL", "https://api.chatanywhere.org/v1"),
            "openai_api_key": env.get("AUTOTEST_API_KEY", ""),
            "openai_model": model,
            "llm_model": model,
            "purpose_models": {"memory": model},
            "purpose_temperatures": {"memory": 0.0},
        }
    )
    cases = json.loads((FIX / "memory_cases.json").read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        plan, context, messages = build(case)
        trace = TurnTrace(request_id=f"memory-{case['id']}")
        llm = LLMClient(settings, trace=trace)
        try:
            draft = await derive_memory(llm=llm, plan=plan, context=context, messages=messages)
            preferences = list(draft.preferences) if draft else []
            error = ""
        except Exception as exc:  # noqa: BLE001
            preferences, error = [], str(exc)[:300]
        finally:
            await llm.aclose()
        hits = [gold for gold in case["gold"] if hit(gold, preferences)]
        rows.append({
            "id": case["id"], "preferences": preferences, "gold": case["gold"], "hits": hits,
            "recall": round(len(hits) / len(case["gold"]), 3) if case["gold"] else 1.0,
            "latency_ms": round(0, 1), "tokens": trace.total_tokens, "error": error,
        })
        print(f"{case['id']} recall={rows[-1]['recall']:.0%}", flush=True)
    with_gold = [row for row in rows if row["gold"]]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "suite": "memory", "model": model,
        "correct": sum(1 for row in rows if row["recall"] == 1.0), "n": len(rows),
        "mean_recall": round(sum(row["recall"] for row in with_gold) / len(with_gold), 4),
        "rows": rows,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (OUT / f"memory-chatanywhere-{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "memory-chatanywhere-latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(payload["correct"], payload["n"], payload["mean_recall"])


if __name__ == "__main__":
    asyncio.run(main())
