"""The plan one real offline planning turn gives for a single city, with what changes from run to run removed.

``tests/golden/single_city_plan.json`` is that plan as the code gave it before multi-city planning existed; the test
that compares against it is what shows a one-city trip is planned as it always was.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, timedelta
from typing import Any

from app.config import Settings
from app.domain.models import EvalCase
from app.eval_efficiency import build_rig, case_context
from app.eval_metering import RunLedger

START = date(2027, 3, 1)
_VOLATILE_KEYS = {
    "created_at",
    "updated_at",
    "fetched_at",
    "generated_at",
    "checked_at",
    "trip_id",
    "session_id",
    "published_at",
    "result_id",
    # Fields later steps added (links, ranking, pictures): the golden is the plan as it was before them.
    "links",
    "rank",
    "score",
    "reasons",
    "image",
    # Step 7: the profile a plan was made for (None for the default) and a place source's step-free statement.
    "profile",
    "step_free",
}
_RANDOM_ID = re.compile(r"^(check|reservation|disruption|msg|trip|sess)_[0-9a-f]{6,}$")


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str) and _RANDOM_ID.match(value):
        return "<id>"
    return value


def single_city_plan(settings: Settings, destination: str = "Kyoto") -> dict[str, Any]:
    settings = settings.model_copy(
        update={
            "preplan_budget_seconds": 15,
            "agent_timeout_seconds": 8,
            "enable_flights": True,
        }
    )

    async def main() -> dict[str, Any]:
        ledger = RunLedger()
        try:
            rig = build_rig(settings, destination, ledger)
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-golden")
            case = EvalCase(
                id="g",
                dataset="d",
                input="",
                destination=destination,
                days=4,
                party_size=2,
                budget=90000,
                currency="HKD",
            )
            context = case_context(case, session.id)
            context.start_date, context.end_date = START, START + timedelta(days=3)
            ledger.store.save_context(context)
            _ = [
                e
                async for e in rig.turns.handle_turn(
                    session_id=session.id, message="Plan this trip"
                )
            ]
            plan = ledger.store.get_plan(session.id)
            cleaned = _clean(json.loads(plan.model_dump_json()))
            # The agents run side by side, so the order they finish in is not part of the plan.
            quality = cleaned["data_quality"]
            quality["notes"].sort()
            quality["gaps"].sort(key=json.dumps)
            return cleaned
        finally:
            ledger.close()

    return asyncio.run(main())
