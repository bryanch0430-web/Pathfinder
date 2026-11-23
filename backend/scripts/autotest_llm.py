"""Test-only model probe for ChatAnywhere.

Reads `.env.autotest` from the project root. The key is never written to the
result artifact. Any model id supplied on the command line is passed through
unchanged; there is no allow-list.

Usage:
    python scripts/autotest_llm.py
    python scripts/autotest_llm.py --models claude-sonnet-5-5,gpt-5.5
    python scripts/autotest_llm.py --all --limit 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel
from typing import Literal

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.core.prompts import skill  # noqa: E402
from app.understand.schemas import ParsedIntent  # noqa: E402
from app.understand.jev import jev_route  # noqa: E402
from app.domain.models import ChatMessage, TripContext  # noqa: E402


class Probe(BaseModel):
    route: Literal["plan", "modify", "ask", "clarify"]
    confidence: float
    note: str = ""

OUT = PROJECT / "docs" / "eval-artifacts" / "autotest"

DEFAULT_MODELS = [
    "claude-sonnet-5-5",
    "gemini-3.5-flash-lite",
    "deepseek-v4.1-flash",
    "gpt-5.5",
]

EXCLUDE = ("tts", "whisper", "embedding", "image", "audio", "transcribe", "realtime", "moderation", "search-api")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


async def list_models(base_url: str, key: str) -> list[str]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"})
        response.raise_for_status()
        data = response.json().get("data", [])
    return [item["id"] for item in data if item.get("id")]


async def raw_probe(model: str, base_url: str, key: str) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with one short sentence: OK."}],
        "temperature": 0,
        "max_tokens": 32,
    }
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        content = (((data.get("choices") or [{}])[0].get("message")) or {}).get("content") or ""
        usage = data.get("usage") or {}
        return {
            "model": model,
            "ok": bool(content.strip()),
            "intent": content.strip()[:80],
            "confidence": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "tokens": int(usage.get("total_tokens") or 0),
            "error": "" if content.strip() else "empty content",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "model": model,
            "ok": False,
            "intent": "",
            "confidence": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "tokens": None,
            "error": str(exc)[:300],
        }


async def probe(model: str, base_url: str, key: str, mode: str) -> dict:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "openai",
            "openai_base_url": base_url,
            "openai_api_key": key,
            "openai_model": model,
            "llm_model": model,
            "llm_temperature": 0.0,
            "purpose_models": {"understand": model},
            "purpose_temperatures": {"understand": 0.0},
        }
    )
    trace = TurnTrace(request_id=f"autotest-{model}")
    llm = LLMClient(settings, trace=trace)
    user = json.dumps(
        {
            "today": "2026-11-23",
            "timezone": "Asia/Hong_Kong",
            "interface_locale": "en",
            "trip": {},
            "plan": None,
            "recent_turns": [],
            "selected_stop": None,
            "message": "Plan a 3-day Kyoto trip for two people in April 2027, budget 4000 USD.",
        },
        ensure_ascii=False,
    )
    started = time.perf_counter()
    try:
        if mode == "understand":
            system = skill("understand")
            schema = ParsedIntent
            prompt = user
        else:
            system = "Return one JSON object with route, confidence and note. Do not add markdown."
            schema = Probe
            prompt = json.dumps({"message": "Plan a 3-day Kyoto trip for two people in April 2027, budget 4000 USD."})
        parsed = await llm.json(
            purpose="understand",
            system=system,
            user=prompt,
            schema=schema,
            temperature=0.0,
            max_tokens=2048,
        )
        return {
            "model": model,
            "ok": True,
            "intent": getattr(parsed, "intent", None) or getattr(parsed, "route", ""),
            "confidence": parsed.confidence,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "tokens": llm.trace.total_tokens if llm.trace else None,
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "model": model,
            "ok": False,
            "intent": "",
            "confidence": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "tokens": llm.trace.total_tokens if llm.trace else None,
            "error": str(exc)[:300],
        }
    finally:
        await llm.aclose()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="", help="comma-separated model ids; overrides AUTOTEST_MODELS")
    parser.add_argument("--all", action="store_true", help="fetch the endpoint catalog")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mode", choices=["raw", "probe", "understand"], default="probe")
    parser.add_argument("--jev", action="store_true", help="also probe the Jev endpoint using JEV_API_KEY")
    args = parser.parse_args()

    env = read_env(PROJECT / ".env.autotest")
    base_url = env.get("AUTOTEST_BASE_URL", "https://api.chatanywhere.org/v1")
    key = env.get("AUTOTEST_API_KEY") or os.environ.get("AUTOTEST_API_KEY", "")
    if not key:
        raise SystemExit("No AUTOTEST_API_KEY in .env.autotest or environment")

    if args.models:
        models = [item.strip() for item in args.models.split(",") if item.strip()]
    elif args.all:
        models = [m for m in await list_models(base_url, key) if not any(x in m.lower() for x in EXCLUDE)]
    else:
        models = [item.strip() for item in env.get("AUTOTEST_MODELS", ",".join(DEFAULT_MODELS)).split(",") if item.strip()]
    if args.limit:
        models = models[: args.limit]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "provider": "ChatAnywhere (test-only)",
        "model_count": len(models),
        "mode": args.mode,
        "models": [],
        "jev": {
            "env_key_present": bool(os.environ.get("JEV_API_KEY") or env.get("JEV_API_KEY")),
            "note": "Jev is optional; set JEV_API_KEY and pass --jev to call it.",
        },
    }
    print(f"probing {len(models)} model(s) through {base_url}", flush=True)
    for model in models:
        print(f"  {model} ...", flush=True)
        row = await raw_probe(model, base_url, key) if args.mode == "raw" else await probe(model, base_url, key, args.mode)
        payload["models"].append(row)
        print(f"    {'OK' if row['ok'] else 'FAIL'} {row['intent']} {row['latency_ms']} ms {row['error'][:100]}", flush=True)

    if args.jev:
        jev_key = os.environ.get("JEV_API_KEY") or env.get("JEV_API_KEY", "")
        if not jev_key:
            payload["jev"].update({"ok": False, "error": "JEV_API_KEY is not set"})
            print("Jev: skipped (JEV_API_KEY not set)", flush=True)
        else:
            jev_settings = get_settings().model_copy(
                update={
                    "decision_api_key": jev_key,
                    "decision_api_url": env.get("DECISION_API_URL", "https://api.typesafe.ai/v1/systemone"),
                    "decision_model": env.get("DECISION_MODEL", "jev-latest"),
                }
            )
            try:
                decision = await jev_route(
                    settings=jev_settings,
                    context=TripContext(session_id="autotest-jev", destination="Kyoto", origin="Hong Kong", days=3, party_size=2),
                    plan=None,
                    message="Plan a 3-day Kyoto trip for two people in April 2027.",
                    history=[ChatMessage(id="jev-1", role="user", content="Plan a 3-day Kyoto trip for two people in April 2027.")],
                )
                payload["jev"].update(
                    {"ok": decision is not None, "intent": decision.intent if decision else "", "confidence": decision.confidence if decision else None, "error": "" if decision else "no decision"}
                )
                print(f"Jev: {'OK' if decision else 'FAIL'}", flush=True)
            except Exception as exc:  # noqa: BLE001
                payload["jev"].update({"ok": False, "error": str(exc)[:300]})
                print(f"Jev: FAIL {exc}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = OUT / f"autotest-{stamp}.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "autotest-latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for row in payload["models"] if row["ok"])
    lines = [
        "# ChatAnywhere auto-test",
        "",
        f"Generated: {payload['generated_at']}",
        f"Base URL: `{base_url}`",
        f"Models: {ok}/{len(models)} schema-valid",
        "",
        "| Model | OK | Intent | p50-ish ms | Tokens | Error |",
        "|---|---:|---|---:|---:|---|",
    ]
    for row in payload["models"]:
        lines.append(f"| {row['model']} | {'yes' if row['ok'] else 'no'} | {row['intent']} | {row['latency_ms']} | {row['tokens'] or ''} | {row['error'][:120]} |")
    (OUT / "autotest-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {target}; {ok}/{len(models)} valid")


if __name__ == "__main__":
    asyncio.run(main())
