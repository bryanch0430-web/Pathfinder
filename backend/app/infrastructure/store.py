"""SQLite persistence. One file, no server, WAL mode.

The store implements the repository ports structurally. Long-term trip recall is
computed in Python over stored embeddings; that is a deliberate single-node
trade-off documented in the architecture note.
"""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from app.domain.models import (
    DatasetMeta,
    EvalCaseResult,
    EvaluationRun,
    Feedback,
    Message,
    PreferenceProfile,
    SavedTrip,
    Session,
    TripContext,
    TripPlan,
    User,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journeys (
  id TEXT PRIMARY KEY,
  client_token TEXT NOT NULL DEFAULT '',
  user_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journey_state (
  session_id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  intent TEXT,
  confidence REAL,
  meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON turns(session_id, created_at);
CREATE TABLE IF NOT EXISTS itineraries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trip_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  data TEXT NOT NULL,
  change_summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(session_id, trip_id, version)
);
CREATE INDEX IF NOT EXISTS idx_plans_session ON itineraries(session_id, created_at DESC);
CREATE TABLE IF NOT EXISTS ratings (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  useful INTEGER NOT NULL,
  stars INTEGER,
  reason_code TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kept_journeys (
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
CREATE TABLE IF NOT EXISTS share_links (
  slug TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  trip_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  data TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS taste_profiles (
  client_token TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_spans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args TEXT NOT NULL,
  status TEXT NOT NULL,
  latency_ms INTEGER NOT NULL,
  error_class TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS threat_events (
  category TEXT PRIMARY KEY,
  severity TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_suites (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  split TEXT NOT NULL,
  frozen INTEGER NOT NULL DEFAULT 0,
  item_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_trials (
  id TEXT PRIMARY KEY,
  experiment_tag TEXT NOT NULL,
  dataset_id TEXT NOT NULL,
  dataset_version TEXT NOT NULL,
  status TEXT NOT NULL,
  metrics TEXT NOT NULL,
  cases TEXT NOT NULL,
  model_snapshot TEXT NOT NULL,
  token_usage INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class SQLiteStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(journeys)")}
            if "user_id" not in columns:
                conn.execute("ALTER TABLE journeys ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=20, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    # -- sessions ---------------------------------------------------------

    def create_session(self, client_token: str, user_id: str = "") -> Session:
        session = Session(client_token=client_token, user_id=user_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO journeys (id, client_token, user_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                (
                    session.id,
                    session.client_token,
                    session.user_id,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                ),
            )
        return session

    def get_session(self, session_id: str) -> Session | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM journeys WHERE id=?", (session_id,)).fetchone()
        if not row:
            return None
        return Session(
            id=row["id"],
            client_token=row["client_token"],
            user_id=row["user_id"] if "user_id" in row.keys() else "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def create_user(self, user: User, password_hash: str) -> User:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO accounts (id, email, password_hash, display_name, created_at) VALUES (?,?,?,?,?)",
                (user.id, user.email, password_hash, user.display_name, user.created_at.isoformat()),
            )
        return user

    def get_user_by_email(self, email: str) -> tuple[User, str] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE email=?", (email.lower(),)).fetchone()
        if not row:
            return None
        user = User(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        return user, row["password_hash"]

    def get_user(self, user_id: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id=?", (user_id,)).fetchone()
        if not row:
            return None
        return User(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def set_session_user(self, session_id: str, user_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE journeys SET user_id=? WHERE id=?", (user_id, session_id))

    def merge_preferences(self, from_token: str, to_token: str) -> PreferenceProfile:
        source = self.get_preferences(from_token)
        target = self.get_preferences(to_token)
        merged: list[str] = []
        for value in (target.preferences if target else []) + (source.preferences if source else []):
            if value and value not in merged:
                merged.append(value)
        profile = PreferenceProfile(client_token=to_token, preferences=merged[:20])
        self.upsert_preferences(profile)
        return profile

    def touch_session(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE journeys SET updated_at=? WHERE id=?", (_now(), session_id))

    def save_context(self, context: TripContext) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO journey_state (session_id, data, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (context.session_id, context.model_dump_json(), context.updated_at.isoformat()),
            )

    def get_context(self, session_id: str) -> TripContext | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM journey_state WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return None
        return TripContext.model_validate_json(row["data"])

    def add_message(self, message: Message) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO turns (id, session_id, role, content, intent, confidence, meta, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.intent.value if message.intent else None,
                    message.confidence,
                    _dump(message.meta),
                    message.created_at.isoformat(),
                ),
            )

    def delete_session(self, session_id: str) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            messages = conn.execute("DELETE FROM turns WHERE session_id=?", (session_id,)).rowcount or 0
            plans = conn.execute("DELETE FROM itineraries WHERE session_id=?", (session_id,)).rowcount or 0
            contexts = conn.execute("DELETE FROM journey_state WHERE session_id=?", (session_id,)).rowcount or 0
            sessions = conn.execute("DELETE FROM journeys WHERE id=?", (session_id,)).rowcount or 0
        return {
            "messages": int(messages),
            "plans": int(plans),
            "contexts": int(contexts),
            "sessions": int(sessions),
        }

    def delete_messages(self, session_id: str) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM turns WHERE session_id=?", (session_id,))
            return int(cursor.rowcount or 0)

    def clear_memory(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            saved = conn.execute("DELETE FROM kept_journeys").rowcount or 0
            prefs = conn.execute("DELETE FROM taste_profiles").rowcount or 0
        return {"saved_trips": int(saved), "preferences": int(prefs)}

    def client_profile(self, client_token: str) -> dict[str, Any]:
        with self._connect() as conn:
            sessions = conn.execute(
                "SELECT COUNT(*) AS n FROM journeys WHERE client_token=?", (client_token,)
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT c.data FROM journey_state c JOIN journeys s ON s.id=c.session_id "
                "WHERE s.client_token=? ORDER BY s.updated_at DESC LIMIT 20",
                (client_token,),
            ).fetchall()
            saved = conn.execute("SELECT COUNT(*) AS n FROM kept_journeys").fetchone()["n"]
        destinations: list[str] = []
        for row in rows:
            try:
                context = TripContext.model_validate_json(row["data"])
            except Exception:
                continue
            if context.destination and context.destination not in destinations:
                destinations.append(context.destination)
        profile = self.get_preferences(client_token)
        return {
            "client_token": client_token,
            "preferences": profile.preferences if profile else [],
            "hard_constraints": [],
            "sessions": int(sessions),
            "saved_trips": int(saved),
            "destinations": destinations[:8],
            "updated_at": profile.updated_at.isoformat() if profile else None,
        }

    def list_messages(self, session_id: str, limit: int = 50) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        rows = list(reversed(rows))
        return [
            Message(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                intent=row["intent"],
                confidence=row["confidence"],
                meta=json.loads(row["meta"] or "{}"),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    # -- plans ------------------------------------------------------------

    def save_plan(self, plan: TripPlan, change_summary: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO itineraries (trip_id, session_id, version, data, change_summary, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    plan.trip_id,
                    plan.session_id,
                    plan.version,
                    plan.model_dump_json(),
                    change_summary,
                    plan.updated_at.isoformat(),
                ),
            )

    def get_plan(self, session_id: str, version: int | None = None) -> TripPlan | None:
        with self._connect() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT data FROM itineraries WHERE session_id=? ORDER BY created_at DESC, version DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT data FROM itineraries WHERE session_id=? AND version=? ORDER BY created_at DESC LIMIT 1",
                    (session_id, version),
                ).fetchone()
        return TripPlan.model_validate_json(row["data"]) if row else None

    def list_versions(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT trip_id, version, change_summary, created_at, data FROM itineraries WHERE session_id=? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        out = []
        for row in rows:
            plan = TripPlan.model_validate_json(row["data"])
            out.append(
                {
                    "trip_id": row["trip_id"],
                    "version": row["version"],
                    "change_summary": row["change_summary"],
                    "created_at": row["created_at"],
                    "item_count": plan.item_count,
                    "destination": plan.destination.name,
                    "days": len(plan.days),
                }
            )
        return out

    # -- feedback / memory ------------------------------------------------

    def add_feedback(self, feedback: Feedback) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO ratings (id, trip_id, useful, stars, reason_code, created_at) VALUES (?,?,?,?,?,?)",
                (
                    feedback.id,
                    feedback.trip_id,
                    int(feedback.useful),
                    feedback.stars,
                    feedback.reason_code,
                    feedback.created_at.isoformat(),
                ),
            )

    def list_feedback(self, trip_id: str) -> list[Feedback]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM ratings WHERE trip_id=? ORDER BY created_at", (trip_id,)).fetchall()
        return [
            Feedback(
                id=row["id"],
                trip_id=row["trip_id"],
                useful=bool(row["useful"]),
                stars=row["stars"],
                reason_code=row["reason_code"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def save_saved_trip(self, trip: SavedTrip) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kept_journeys (id, trip_id, destination, rating, stars, summary, embedding, data, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    trip.id,
                    trip.trip_id,
                    trip.destination,
                    trip.rating,
                    trip.stars,
                    trip.summary,
                    _dump(trip.embedding),
                    trip.model_dump_json(),
                    trip.created_at.isoformat(),
                ),
            )

    def list_latest_plans(self, limit: int = 300) -> list[TripPlan]:
        """Newest version of each saved trip, used only for aggregate trending."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM itineraries WHERE id IN ("
                "  SELECT MAX(id) FROM itineraries GROUP BY session_id, trip_id"
                ") ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [TripPlan.model_validate_json(row["data"]) for row in rows]

    def list_saved_trips(self, limit: int = 20) -> list[SavedTrip]:
        # Sort in Python: a large SQL LIMIT can push SQLite into a temp-file
        # sort, which fails on hosts where the temp directory is restricted.
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM kept_journeys").fetchall()
        trips = [SavedTrip.model_validate_json(row["data"]) for row in rows]
        trips.sort(key=lambda trip: trip.created_at, reverse=True)
        return trips[:limit]

    def delete_saved_trips_prefix(self, prefix: str) -> int:
        """Remove synthetic journeys by trip_id prefix (demo seeding only)."""
        with self._connect() as conn:
            return conn.execute(
                "DELETE FROM kept_journeys WHERE trip_id LIKE ?", (prefix + "%",)
            ).rowcount or 0

    def delete_preferences_prefix(self, prefix: str) -> int:
        """Remove synthetic preference profiles by client_token prefix."""
        with self._connect() as conn:
            return conn.execute(
                "DELETE FROM taste_profiles WHERE client_token LIKE ?", (prefix + "%",)
            ).rowcount or 0

    def search_saved_trips(
        self,
        *,
        embedding: Sequence[float],
        destination: str,
        cutoff: float,
        limit: int = 3,
    ) -> list[tuple[float, SavedTrip]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM kept_journeys").fetchall()
        scored: list[tuple[float, SavedTrip]] = []
        for row in rows:
            trip = SavedTrip.model_validate_json(row["data"])
            similarity = cosine(embedding, trip.embedding)
            if destination and destination.lower() == trip.destination.lower():
                similarity = min(1.0, similarity + 0.08)
            if similarity >= cutoff:
                scored.append((similarity, trip))
        scored.sort(key=lambda pair: (pair[0], pair[1].rating), reverse=True)
        return scored[:limit]

    def create_share(self, session_id: str, plan: TripPlan) -> str:
        slug = "trip_" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
        public = {
            "destination": plan.destination.model_dump(mode="json"),
            "dates": plan.dates.model_dump(mode="json"),
            "days": [day.model_dump(mode="json") for day in plan.days],
            "hotels": [hotel.model_dump(mode="json") for hotel in plan.hotels],
            "tickets": [],
            "cost": {"currency": plan.cost.currency, "total": None, "budget": None, "remaining": None, "status": "unknown", "lines": []},
            "summary": plan.summary,
            "version": plan.version,
            "created_at": _now(),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO share_links (slug, session_id, trip_id, version, data, created_at) VALUES (?,?,?,?,?,?)",
                (slug, session_id, plan.trip_id, plan.version, _dump(public), _now()),
            )
        return slug

    def get_share(self, slug: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM share_links WHERE slug=?", (slug,)).fetchone()
        return json.loads(row["data"]) if row else None

    def get_preferences(self, client_token: str) -> PreferenceProfile | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM taste_profiles WHERE client_token=?", (client_token,)).fetchone()
        return PreferenceProfile.model_validate_json(row["data"]) if row else None

    def upsert_preferences(self, profile: PreferenceProfile) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO taste_profiles (client_token, data, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(client_token) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (profile.client_token, profile.model_dump_json(), profile.updated_at.isoformat()),
            )

    # -- telemetry --------------------------------------------------------

    def log_tool_call(
        self,
        *,
        request_id: str,
        tool: str,
        args: dict[str, Any],
        status: str,
        latency_ms: int,
        error_class: str = "",
        source: str = "",
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO tool_spans (request_id, tool, args, status, latency_ms, error_class, source, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (request_id, tool, _dump(args), status, latency_ms, error_class, source, _now()),
            )

    def log_injection(self, category: str, severity: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO threat_events (category, severity, count, updated_at) VALUES (?,?,1,?) "
                "ON CONFLICT(category) DO UPDATE SET count=count+1, updated_at=excluded.updated_at",
                (category, severity, _now()),
            )

    def injection_summary(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM threat_events ORDER BY count DESC").fetchall()
        return [
            {"category": row["category"], "severity": row["severity"], "count": row["count"], "updated_at": row["updated_at"]}
            for row in rows
        ]

    # -- evaluation -------------------------------------------------------

    def save_eval_dataset(self, meta: DatasetMeta) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO eval_suites (id, name, version, split, frozen, item_count, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (meta.id, meta.name, meta.version, meta.split, int(meta.frozen), meta.item_count, meta.created_at.isoformat()),
            )

    def list_eval_datasets(self) -> list[DatasetMeta]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM eval_suites ORDER BY created_at").fetchall()
        return [
            DatasetMeta(
                id=row["id"],
                name=row["name"],
                version=row["version"],
                split=row["split"],
                frozen=bool(row["frozen"]),
                item_count=row["item_count"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def get_eval_dataset(self, dataset_id: str) -> DatasetMeta | None:
        for dataset in self.list_eval_datasets():
            if dataset.id == dataset_id or dataset.name == dataset_id:
                return dataset
        return None

    def freeze_eval_dataset(self, dataset_id: str) -> DatasetMeta:
        meta = self.get_eval_dataset(dataset_id)
        if not meta:
            raise KeyError(dataset_id)
        meta.frozen = True
        self.save_eval_dataset(meta)
        return meta

    def save_eval_run(self, run: EvaluationRun) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO eval_trials "
                "(id, experiment_tag, dataset_id, dataset_version, status, metrics, cases, model_snapshot, token_usage, latency_ms, created_at, finished_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run.id,
                    run.experiment_tag,
                    run.dataset_id,
                    run.dataset_version,
                    run.status,
                    _dump([metric.model_dump() for metric in run.metrics]),
                    _dump([case.model_dump() for case in run.cases]),
                    _dump(run.model_snapshot),
                    run.token_usage,
                    run.latency_ms,
                    run.created_at.isoformat(),
                    run.finished_at.isoformat() if run.finished_at else None,
                ),
            )

    def list_eval_runs(self, limit: int = 50) -> list[EvaluationRun]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM eval_trials ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_run(row) for row in rows]

    def get_eval_run(self, run_id: str) -> EvaluationRun | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM eval_trials WHERE id=?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def save_eval_cases(self, run_id: str, cases: list[EvalCaseResult]) -> None:
        run = self.get_eval_run(run_id)
        if not run:
            return
        run.cases = cases
        self.save_eval_run(run)

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> EvaluationRun:
        data = {
            "id": row["id"],
            "experiment_tag": row["experiment_tag"],
            "dataset_id": row["dataset_id"],
            "dataset_version": row["dataset_version"],
            "status": row["status"],
            "metrics": json.loads(row["metrics"]),
            "cases": json.loads(row["cases"]),
            "model_snapshot": json.loads(row["model_snapshot"]),
            "token_usage": row["token_usage"],
            "latency_ms": row["latency_ms"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }
        return EvaluationRun.model_validate(data)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)
