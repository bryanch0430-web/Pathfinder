"""Export the API contract for the frontend (and any other client).

    uv run python -m backend.api.export_contracts

Writes contracts/openapi.json (HTTP API + TurnEvent) and contracts/trip_plan.schema.json (the
shared TripPlan JSON Schema). The frontend generates its TypeScript types from these files only.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.api.main import create_app
from backend.schemas.trip_plan import TripPlan

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"


def export(out_dir: Path = CONTRACTS_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    openapi_path = out_dir / "openapi.json"
    schema_path = out_dir / "trip_plan.schema.json"
    openapi_path.write_text(json.dumps(create_app().openapi(), indent=2) + "\n", encoding="utf-8")
    trip_plan = TripPlan.model_json_schema()
    trip_plan["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    trip_plan["$id"] = "https://pathfinder.local/schemas/trip_plan.schema.json"
    schema_path.write_text(json.dumps(trip_plan, indent=2) + "\n", encoding="utf-8")
    return [openapi_path, schema_path]


if __name__ == "__main__":
    for path in export():
        print(f"wrote {path}")
