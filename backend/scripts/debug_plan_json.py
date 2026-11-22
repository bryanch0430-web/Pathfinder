"""Debug one structured planner call and show exactly what the model returned."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.prompts import context_brief, skill  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.domain.models import TripContext  # noqa: E402
from app.evidence.providers import Providers  # noqa: E402
from app.plan.agents import candidate_pool, gather  # noqa: E402


async def main() -> None:
    settings = get_settings()
    context = TripContext(
        session_id="debug",
        destination="Kyoto",
        origin="Hong Kong",
        start_date="2027-04-10",
        end_date="2027-04-13",
        days=4,
        party_size=2,
        budget=400000,
        currency="JPY",
        hard_constraints=["must include Fushimi Inari", "stay within budget"],
    )
    trace = TurnTrace(request_id="debug")
    providers = Providers(settings, trace)

    async def emit(event, data):
        pass

    gathered = await gather(providers=providers, settings=settings, context=context, trace=trace, emit=emit)
    payload = {
        "trip": context_brief(context),
        "dates": ["2027-04-10", "2027-04-11", "2027-04-12", "2027-04-13"],
        "weather": [{"date": day.date, "summary": day.summary} for day in gathered.bundle.weather],
        "candidates": candidate_pool(gathered.bundle),
        "disruptions": gathered.notes,
        "previous_problems": [],
        "destination": None,
    }
    llm = LLMClient(settings, trace=trace)
    body = {
        "model": llm.model,
        "messages": [
            {"role": "system", "content": skill("plan")},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)},
        ],
        "temperature": 0.2,
        "max_tokens": int(os.environ.get("DBG_MAX_TOKENS", "6000")),
        "response_format": {"type": "json_object"},
    }
    response = await llm.client.post(
        f"{llm.base_url}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {llm.api_key}", "Content-Type": "application/json"},
    )
    print("status", response.status_code)
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    print("finish_reason", choice.get("finish_reason"))
    print("usage", data.get("usage"))
    print("chars", len(content))
    print("HEAD:\n", content[:1200])
    print("\nTAIL:\n", content[-800:])
    await llm.aclose()
    await providers.aclose()


if __name__ == "__main__":
    asyncio.run(main())
