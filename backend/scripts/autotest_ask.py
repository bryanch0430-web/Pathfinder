"""Auto-test the 12 ask cases through ChatAnywhere."""

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
from v5_ask_bench import run_case  # noqa: E402

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
    model = env.get("AUTOTEST_ASK_MODEL", "claude-sonnet-5-5")
    settings = get_settings().model_copy(
        update={
            "llm_provider": "openai",
            "openai_base_url": env.get("AUTOTEST_BASE_URL", "https://api.chatanywhere.org/v1"),
            "openai_api_key": env.get("AUTOTEST_API_KEY", ""),
            "openai_model": model,
            "llm_model": model,
            "purpose_models": {"ask": model},
            "purpose_temperatures": {"ask": 0.0},
        }
    )
    plan = json.loads((FIX / "kyoto_plan.json").read_text(encoding="utf-8"))
    context = json.loads((FIX / "kyoto_context.json").read_text(encoding="utf-8"))
    cases = json.loads((FIX / "ask_cases.json").read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        row = await run_case(settings, model, case, plan, context)
        rows.append(row)
        print(f"{case['id']} {'PASS' if row['correct'] else 'FAIL'} {row['latency_ms']} ms", flush=True)
    passed = sum(1 for row in rows if row["correct"])
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "ask", "model": model, "correct": passed, "n": len(rows),
        "accuracy": round(passed / len(rows), 4), "rows": rows,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (OUT / f"ask-chatanywhere-{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "ask-chatanywhere-latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(payload["correct"], payload["n"], payload["accuracy"])


if __name__ == "__main__":
    asyncio.run(main())
