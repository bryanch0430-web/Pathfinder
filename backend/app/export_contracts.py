"""Export the public contracts: TripPlan JSON Schema and the OpenAPI document.

Run from the repository root:
    PYTHONPATH=backend python -m app.export_contracts [directory]

The files go to `contracts/` unless a directory is given.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Describing the API serves nothing, so it must not need the deployment's secrets. An unset
# ENVIRONMENT means production, and importing the API builds the app, which production refuses
# to do on the repository's default secrets.
os.environ["ENVIRONMENT"] = "development"

from app.config import BASE_DIR  # noqa: E402 - after the environment is set
from app.domain.models import TripPlan  # noqa: E402
from app.interface.api import create_app  # noqa: E402


def main(target: Path | None = None) -> None:
    contracts = target or BASE_DIR / "contracts"
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
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
