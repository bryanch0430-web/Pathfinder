"""Versioned SQLite migrations: `PRAGMA user_version` plus an ordered step list."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.domain.models import SavedTrip
from app.infrastructure import store as store_module
from app.infrastructure.store import BASELINE_SCHEMA, MIGRATIONS, SQLiteStore

#: Tables the store reads and writes; every migrated file must have all of them.
TABLES = {
    "accounts", "journeys", "journey_state", "turns", "itineraries", "ratings", "kept_journeys",
    "share_links", "taste_profiles", "tool_spans", "threat_events", "eval_suites", "eval_trials",
}

#: A file from before accounts and per-owner saved trips: no journeys.user_id,
#: no kept_journeys.owner, no owner index, user_version 0.
OLD_SCHEMA = """
CREATE TABLE journeys (
  id TEXT PRIMARY KEY,
  client_token TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE turns (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  intent TEXT,
  confidence REAL,
  meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE kept_journeys (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  destination TEXT NOT NULL,
  rating REAL NOT NULL DEFAULT 0,
  stars INTEGER,
  summary TEXT NOT NULL DEFAULT '',
  embedding TEXT NOT NULL,
  data TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

NOW = "2026-01-01T00:00:00+00:00"


def _user_version(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    return int(version)


def _schema(path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    conn.close()
    return [tuple(row) for row in rows]


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    return columns


def _indexes(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    return names


def _legacy_trip() -> SavedTrip:
    return SavedTrip(trip_id="trip_old", destination="Kyoto", summary="old row", embedding=[0.0])


def _insert_rows(conn: sqlite3.Connection) -> SavedTrip:
    trip = _legacy_trip()
    conn.execute(
        "INSERT INTO journeys (id, client_token, created_at, updated_at) VALUES (?,?,?,?)",
        ("ses_old", "guest-old", NOW, NOW),
    )
    conn.execute(
        "INSERT INTO turns (id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
        ("msg_old", "ses_old", "user", "Plan Kyoto", NOW),
    )
    conn.execute(
        "INSERT INTO kept_journeys (id, trip_id, destination, rating, stars, summary, embedding, data, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (trip.id, trip.trip_id, "Kyoto", 1.0, None, "old row", "[0.0]", trip.model_dump_json(), trip.created_at.isoformat()),
    )
    return trip


def test_fresh_database_reaches_the_latest_version(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    store = SQLiteStore(path)

    assert store.schema_version == len(MIGRATIONS)
    assert _user_version(path) == len(MIGRATIONS)
    with sqlite3.connect(path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert TABLES <= tables
    assert "user_id" in _columns(path, "journeys")
    assert "owner" in _columns(path, "kept_journeys")
    assert "idx_kept_owner_trip" in _indexes(path)


def test_old_schema_database_upgrades_and_keeps_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)
        trip = _insert_rows(conn)
    conn.close()
    assert _user_version(path) == 0

    store = SQLiteStore(path)

    assert _user_version(path) == len(MIGRATIONS)
    assert TABLES <= {name for kind, name, _ in _schema(path) if kind == "table"}
    assert "user_id" in _columns(path, "journeys")
    assert "owner" in _columns(path, "kept_journeys")
    assert "idx_kept_owner_trip" in _indexes(path)
    # The old rows are still there and readable through the store.
    session = store.get_session("ses_old")
    assert session is not None and session.client_token == "guest-old" and session.user_id == ""
    assert [message.content for message in store.list_messages("ses_old")] == ["Plan Kyoto"]
    assert [saved.trip_id for saved in store.list_saved_trips()] == [trip.trip_id]
    with sqlite3.connect(path) as conn:
        owners = [row[0] for row in conn.execute("SELECT owner FROM kept_journeys")]
    conn.close()
    assert owners == [None]
    # And the upgraded file takes new writes in the current shape.
    store.save_saved_trip(SavedTrip(trip_id="trip_new", destination="Kyoto", embedding=[0.0]), owner="guest-new")
    assert [saved.trip_id for saved in store.list_saved_trips_for_owner("guest-new")] == ["trip_new"]


def test_pre_versioning_file_with_the_current_schema_upgrades_in_place(tmp_path: Path) -> None:
    """A file from the release before versioning: owner column and index, user_version 0."""
    path = tmp_path / "v130.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(BASELINE_SCHEMA)
        conn.execute("CREATE UNIQUE INDEX idx_kept_owner_trip ON kept_journeys(owner, trip_id)")
        conn.execute(
            "INSERT INTO journeys (id, client_token, user_id, created_at, updated_at) VALUES (?,?,?,?,?)",
            ("ses_v130", "guest-v130", "usr_1", NOW, NOW),
        )
    conn.close()
    assert _user_version(path) == 0

    store = SQLiteStore(path)

    assert _user_version(path) == len(MIGRATIONS)
    session = store.get_session("ses_v130")
    assert session is not None and session.user_id == "usr_1"


def test_opening_a_migrated_database_again_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "twice.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)
        _insert_rows(conn)
    conn.close()
    SQLiteStore(path)
    schema_before = _schema(path)

    def must_not_run(conn: sqlite3.Connection, ctx: store_module.MigrationContext) -> None:
        raise AssertionError("an applied migration ran again")

    monkeypatch.setattr(store_module, "MIGRATIONS", [must_not_run] * len(MIGRATIONS))
    store = SQLiteStore(path)

    assert store.schema_version == len(MIGRATIONS)
    assert _schema(path) == schema_before
    assert store.get_session("ses_old") is not None


def test_a_failing_step_rolls_back_and_keeps_the_previous_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "failing.db"
    SQLiteStore(path)
    latest = len(MIGRATIONS)

    def half_done(conn: sqlite3.Connection, ctx: store_module.MigrationContext) -> None:
        conn.execute("ALTER TABLE journeys ADD COLUMN half_done TEXT")
        raise RuntimeError("step failed")

    monkeypatch.setattr(store_module, "MIGRATIONS", [*MIGRATIONS, half_done])
    with pytest.raises(RuntimeError, match="step failed"):
        SQLiteStore(path)

    assert _user_version(path) == latest
    assert "half_done" not in _columns(path, "journeys")
