"""Export the public contracts: TripPlan JSON Schema and the OpenAPI document.

Run from `Pathfinder_v1/`:
    PYTHONPATH=backend python -m app.export_contracts
"""

from __future__ import annotations

import json

from app.config import BASE_DIR
from app.domain.models import TripPlan
from app.interface.api import create_app


def main() -> None:
    contracts = BASE_DIR / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    schema = TripPlan.model_json_schema()
    (contracts / "trip_plan.schema.json").write_text(
        json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    openapi = create_app().openapi()
    (contracts / "openapi.json").write_text(
        json.dumps(openapi, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {contracts / 'trip_plan.schema.json'}")
    print(f"wrote {contracts / 'openapi.json'}")


if __name__ == "__main__":
    main()
