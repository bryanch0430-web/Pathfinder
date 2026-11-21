"""Live planner step for the contact desk.

Stored beside the database so a long turn, which keeps its own write open,
does not hide the step from the desk.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "planner_runs.json"
_STALE = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read() -> dict:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data), encoding="utf-8")


def set_planner_run(trip_id: str, stage: str | None) -> None:
    """Remember the step a trip is on. None means the turn has finished."""
    data = _read()
    key = str(trip_id)
    if not stage:
        data.pop(key, None)
    else:
        current = data.get(key) if isinstance(data.get(key), dict) else {}
        data[key] = {
            "stage": stage,
            "started_at": current.get("started_at") or _now().isoformat(),
            "updated_at": _now().isoformat(),
        }
    _write(data)


def read_planner_run(trip_id: str) -> dict | None:
    row = _read().get(str(trip_id))
    if not isinstance(row, dict) or not row.get("stage") or not row.get("started_at"):
        return None
    updated = row.get("updated_at") or row["started_at"]
    try:
        stamp = datetime.fromisoformat(str(updated))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    if _now() - stamp > _STALE:
        return None
    return {"stage": str(row["stage"]), "started_at": str(row["started_at"])}
