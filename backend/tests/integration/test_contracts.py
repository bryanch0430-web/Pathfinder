"""contracts/ is the frontend's only coupling to the backend: it must match the running app."""

from __future__ import annotations

import json
from pathlib import Path

from backend.api.export_contracts import CONTRACTS_DIR, export
from backend.api.main import create_app


def test_committed_contracts_match_the_app(tmp_path: Path) -> None:
    for fresh in export(tmp_path):
        committed = CONTRACTS_DIR / fresh.name
        assert json.loads(committed.read_text(encoding="utf-8")) == json.loads(fresh.read_text(encoding="utf-8")), (
            f"{committed.name} is stale: run `uv run python -m backend.api.export_contracts`"
        )


def test_plan_workspace_additions_are_in_the_contract() -> None:
    schema = create_app().openapi()
    assert {"patch", "delete"} <= set(schema["paths"]["/api/sessions/{session_id}/plan/items/{item_id}"])
    components = schema["components"]["schemas"]
    assert {"PlanFocus", "FocusKind", "PlanItemPatch"} <= set(components)
    assert set(components["ChatRequest"]["properties"]) == {"message", "focus"}
    assert components["ChatRequest"]["required"] == ["message"]  # focus stays optional
