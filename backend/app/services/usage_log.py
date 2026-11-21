"""Latency and estimated cost for each model call, stored locally and in LangSmith when a key is set."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# Dollars per 1M tokens. These are estimates for comparison, not an invoice.
_RATES = {
    "deepseek": (0.14, 0.28),
    "grok-4.7": (2.0, 6.0),
    "claude": (3.0, 15.0),
    "default": (0.50, 1.50),
}
_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "llm_usage.jsonl"


def _rate(model: str) -> tuple[float, float]:
    name = (model or "").casefold()
    if "grok-4.7" in name:
        return _RATES["grok-4.7"]
    if "deepseek" in name:
        return _RATES["deepseek"]
    if "claude" in name or "sonnet" in name:
        return _RATES["claude"]
    return _RATES["default"]


def estimate_usd(model: str, prompt: str, output: str) -> tuple[int, int, float]:
    in_tokens = max(1, len(prompt or "") // 4) if prompt else 0
    out_tokens = max(1, len(output or "") // 4) if output else 0
    in_rate, out_rate = _rate(model)
    cost = (in_tokens * in_rate + out_tokens * out_rate) / 1_000_000
    return in_tokens, out_tokens, cost


def record(kind: str, model: str, prompt: str, output: str, started: float) -> dict:
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    in_tokens, out_tokens, cost = estimate_usd(model, prompt, output)
    row = {
        "kind": kind,
        "model": model or "unknown",
        "latency_ms": elapsed_ms,
        "input_tokens_est": in_tokens,
        "output_tokens_est": out_tokens,
        "est_usd": round(cost, 8),
    }
    row["langsmith"] = _send(row, prompt, output)
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        with _PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    except OSError as exc:
        logger.info("Could not write the usage log: %s", type(exc).__name__)
    logger.info(
        "llm call kind=%s model=%s latency_ms=%s est_usd=%s langsmith=%s",
        row["kind"], row["model"], row["latency_ms"], row["est_usd"], row["langsmith"],
    )
    return row


def _send(row: dict, prompt: str, output: str) -> str:
    if not (settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY.strip()):
        return "off"
    try:
        from langsmith import Client

        end = datetime.now(timezone.utc)
        start = end - timedelta(milliseconds=max(row["latency_ms"], 1))
        Client(api_key=settings.LANGSMITH_API_KEY).create_run(
            name=f"pathfinder-{row['kind']}",
            run_type="llm",
            inputs={"model": row["model"], "prompt_preview": (prompt or "")[:500]},
            outputs={
                "output_preview": (output or "")[:500],
                "est_usd": row["est_usd"],
                "input_tokens_est": row["input_tokens_est"],
                "output_tokens_est": row["output_tokens_est"],
                "latency_ms": row["latency_ms"],
            },
            start_time=start,
            end_time=end,
            project_name=settings.LANGSMITH_PROJECT or "Pathfinder",
        )
        return "sent"
    except Exception as exc:
        logger.info("LangSmith run was not stored: %s", type(exc).__name__)
        return "failed"
