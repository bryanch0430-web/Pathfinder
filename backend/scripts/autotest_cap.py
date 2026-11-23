"""Run cap-stress through ChatAnywhere using the test-only environment."""

from __future__ import annotations

import os
import runpy
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
ENV = PROJECT / ".env.autotest"
values = {}
for line in ENV.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")

base_url = values.get("AUTOTEST_BASE_URL", "https://api.chatanywhere.org/v1")
key = values["AUTOTEST_API_KEY"]
model = values.get("AUTOTEST_CAP_MODEL", "gemini-3.5-flash-lite")
verifier = values.get("AUTOTEST_VERIFIER_MODEL", "deepseek-v4.1-flash")
os.environ.update(
    {
        "PATHFINDER_SANDBOX": "1",
        "LLM_PROVIDER": "openai",
        "OPENAI_BASE_URL": base_url,
        "OPENAI_API_KEY": key,
        "OPENAI_MODEL": model,
        "LLM_MODEL": model,
        "LLM_BASE_URL": base_url,
        "LLM_API_KEY": key,
        "VERIFIER_BASE_URL": base_url,
        "VERIFIER_API_KEY": key,
        "VERIFIER_MODEL": verifier,
    }
)
runpy.run_path(str(PROJECT / "backend" / "scripts" / "v5_cap_stress.py"), run_name="__main__")
