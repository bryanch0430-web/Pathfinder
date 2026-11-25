"""Run Pathfinder evaluations with every live source blocked: only model calls leave the machine.

Sandbox mode already serves places, hotels, weather, routes and flights from the curated corpus, but
web search, video notes and named-place lookups would still call SerpAPI, Wikipedia, DeepSeek Search
or YouTube. Here the providers' HTTP client refuses every request, so those paths return nothing and
the plan records a gap. Model calls use a separate client and are not affected.

    python offline.py eval hongkong japan          # the repository's evaluation runner
    python offline.py script scripts\\v5_verifier_current.py
"""

from __future__ import annotations

import asyncio
import atexit
import collections
import json
import os
import runpy
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Dies Irae\Desktop\HKU\LLM\Pathfinder v5")
BACKEND = REPO / "backend"

os.environ["PATHFINDER_SANDBOX"] = "1"
os.environ["DECISION_ENABLED"] = "false"
for name in ("SERPAPI_KEY", "SERPAPI_KEY_POOL", "GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY", "YOUTUBE_API_KEY",
             "DEEPSEEK_SEARCH_API_KEY"):
    os.environ[name] = ""
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import httpx  # noqa: E402

from app.evidence import providers as prov  # noqa: E402

BLOCKED: collections.Counter[str] = collections.Counter()


def _refuse(request: httpx.Request) -> httpx.Response:
    BLOCKED[request.url.host] += 1
    return httpx.Response(503, json={}, request=request)


def _offline_client(self) -> httpx.AsyncClient:
    if self._client is None:
        self._client = httpx.AsyncClient(transport=httpx.MockTransport(_refuse))
    return self._client


def install() -> None:
    prov.Providers.client = property(_offline_client)


def report() -> None:
    print(f"live source requests blocked: {dict(BLOCKED) or 'none'}", flush=True)


async def run_eval(dataset_ids: list[str]) -> None:
    from app.api.deps import Container
    from app.config import get_settings
    from app.core.store import Store
    from app.eval import runner

    settings = get_settings()
    store = Store(settings.database_file)
    store.init()
    c = Container(settings=settings, store=store)
    print(f"writer {settings.writer[2]} | parts {settings.purpose_models or 'writer'} | verifier "
          f"{settings.verifier_model} | max_repairs {settings.max_repairs} | db {settings.database_file}", flush=True)
    for dataset_id in dataset_ids:
        print(f"running {dataset_id}", flush=True)
        run = await runner.run_dataset(c, dataset_id, os.environ.get("EVAL_TAG", dataset_id), "all")
        # Zero tokens means the case never reached a model; such a run fails the validity screen.
        no_call = sum(1 for row in run["cases"] if int((row.get("scores") or {}).get("tokens", 0)) == 0)
        print(f"{dataset_id}: {run['passed']}/{run['case_count']} passed in {run['latency_ms'] / 1000:.0f}s; "
              f"cases with no model call: {no_call}", flush=True)
        for metric in run["metrics"]:
            print(f"  {metric['label']}: {metric['value']}{metric['unit']}", flush=True)
        for row in [r for r in run["cases"] if not r["passed"]][:12]:
            print(f"  FAIL {row['case_id']} [{row['failure_class']}] {row['output_summary'][:160]}", flush=True)
    await store.close()
    Path(str(settings.database_file) + ".blocked.json").write_text(json.dumps(dict(BLOCKED)), encoding="utf-8")


if __name__ == "__main__":
    install()
    atexit.register(report)
    mode, rest = sys.argv[1], sys.argv[2:]
    os.chdir(BACKEND)
    if mode == "eval":
        asyncio.run(run_eval(rest))
    elif mode == "script":
        script = Path(rest[0])
        script = script if script.is_absolute() else BACKEND / script
        # Running a file directly puts its folder first on the path; the study scripts import siblings.
        sys.path.insert(0, str(script.parent))
        sys.argv = [str(script), *rest[1:]]
        runpy.run_path(str(script), run_name="__main__")
    else:
        raise SystemExit(f"unknown mode {mode}")
