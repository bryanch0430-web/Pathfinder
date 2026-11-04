"""SQLite persistence. One file, no server, WAL mode.

The store implements the repository ports structurally. Long-term trip recall is
computed in Python over stored embeddings; that is a deliberate single-node
trade-off documented in the architecture note.
"""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

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
    USER_PREFIX,
    User,
)

logger = logging.getLogger(__name__)

#: Days a share link stays public unless the store is given another lifetime.
DEFAULT_SHARE_TTL_DAYS = 30

#: The trip a session shows (one placeholder, the session id): the newest, since planning again starts a new
#: trip at version 1. A rewrite keeps a row's creation time, so rewriting an older trip never brings it back.
_NEWEST_TRIP = "(SELECT trip_id FROM itineraries WHERE session_id=? ORDER BY created_at DESC, version DESC LIMIT 1)"

#: The schema as it stood when versioned migrations began (migration 1).
#: Frozen: a later schema change is a new step in MIGRATIONS, never an edit
#: here, so a fresh file and an upgraded file walk the same path.
BASELINE_SCHEMA = """
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
  created_at TEXT NOT NULL,
  owner TEXT
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


# -- schema migrations ---------------------------------------------------------


@dataclass(frozen=True)
class MigrationContext:
    """What a migration step may need besides the connection."""

    #: when this migration run started (UTC); use it for backfilled timestamps
    now: datetime
    #: lifetime given to share links that existed before links could expire
    share_ttl_days: int = DEFAULT_SHARE_TTL_DAYS


MigrationStep = Callable[[sqlite3.Connection, MigrationContext], None]


def _statements(script: str) -> list[str]:
    """Split a DDL script into statements (the schema has no `;` in literals)."""
    return [part.strip() for part in script.split(";") if part.strip()]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _share_expiry(start: datetime, ttl_days: int) -> str:
    """ISO 8601 UTC instant a share link made at `start` stops working."""
    return (start.astimezone(timezone.utc) + timedelta(days=ttl_days)).isoformat(timespec="seconds")


def _share_is_live(expires_at: str | None) -> bool:
    """True until the expiry instant; a missing or unreadable expiry counts as expired."""
    try:
        expiry = datetime.fromisoformat(expires_at) if expires_at else None
    except ValueError:
        expiry = None
    if expiry is None:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < expiry


def _migration_1_baseline(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """Bring any pre-versioning database (user_version 0) to the baseline schema.

    Every statement is idempotent, so a new file, a file from the first release
    and a file from the release just before versioning all end up identical
    with their rows intact.
    """
    for statement in _statements(BASELINE_SCHEMA):
        conn.execute(statement)
    # Files from before accounts existed have no journeys.user_id.
    if "user_id" not in _columns(conn, "journeys"):
        conn.execute("ALTER TABLE journeys ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
    # Saved trips belong to one identity (`user:<id>` or a guest token).
    # Legacy rows keep a NULL owner and are never listed per user.
    if "owner" not in _columns(conn, "kept_journeys"):
        conn.execute("ALTER TABLE kept_journeys ADD COLUMN owner TEXT")
    # One row per owner and trip: saving the same trip again replaces it.
    # NULL owners never collide, so legacy rows migrate untouched.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_kept_owner_trip ON kept_journeys(owner, trip_id)")


def _migration_2_share_expiry(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """Share links expire. Links made before this step get a full lifetime from now."""
    if "expires_at" not in _columns(conn, "share_links"):
        conn.execute("ALTER TABLE share_links ADD COLUMN expires_at TEXT")
    conn.execute(
        "UPDATE share_links SET expires_at=? WHERE expires_at IS NULL",
        (_share_expiry(ctx.now, ctx.share_ttl_days),),
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_share_session ON share_links(session_id)")


# -- injection log (Proposal DB-06: totals only, no personal data) -------------

#: Where a suspected injection was seen.
THREAT_CHANNELS = ("user_message", "tool_result", "model_output")
_THREAT_SEVERITIES = ("low", "medium", "high")
_THREAT_CATEGORY = re.compile(r"[a-z][a-z_]{0,39}")
#: The only columns the injection log has: one counter per category, severity,
#: channel and UTC hour. No text, excerpt, token, user, session, email or IP.
THREAT_COLUMNS = ("category", "severity", "channel", "hour", "count")
_THREAT_TABLE = """
CREATE TABLE {name} (
  category TEXT NOT NULL,
  severity TEXT NOT NULL,
  channel TEXT NOT NULL,
  hour TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (category, severity, channel, hour)
)
"""


def _threat_hour(moment: datetime | None = None) -> str:
    """The UTC hour `moment` falls in (now by default), e.g. 2026-11-03T14:00:00+00:00."""
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.replace(minute=0, second=0, microsecond=0).isoformat()


def _threat_key(category: Any, severity: Any, channel: Any) -> tuple[str, str, str]:
    """Labels a counter may carry. Anything else (free text, ids) becomes "other"."""
    category = str(category or "").strip().lower()
    severity = str(severity or "").strip().lower()
    channel = str(channel or "").strip().lower()
    return (
        category if _THREAT_CATEGORY.fullmatch(category) else "other",
        severity if severity in _THREAT_SEVERITIES else "other",
        channel if channel in THREAT_CHANNELS else "other",
    )


def _legacy_threat_channel(category: str) -> str:
    if category == "tool_result_injection":
        return "tool_result"
    if category == "canary_leak":
        return "model_output"
    return "user_message"


def _migration_3_threat_totals(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """The injection log keeps hourly totals per category, severity and channel only.

    Old rows were one counter per category with an exact last-seen instant. Each
    becomes a counter in the hour of that instant (the run's hour if unreadable),
    with the channel its category implies. Any other column an old file had,
    such as message text or an identifier, is not copied.
    """
    columns = _columns(conn, "threat_events")
    if set(columns) == set(THREAT_COLUMNS):
        return
    totals: dict[tuple[str, str, str, str], int] = {}
    if columns:
        wanted = [name for name in ("category", "severity", "count", "updated_at") if name in columns]
        for row in conn.execute(f"SELECT {', '.join(wanted)} FROM threat_events"):
            values = dict(zip(wanted, row))
            category, severity, _ = _threat_key(values.get("category"), values.get("severity"), "")
            try:
                seen = datetime.fromisoformat(str(values.get("updated_at") or ""))
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=timezone.utc)
            except ValueError:
                seen = ctx.now
            try:
                count = int(values.get("count") or 0)
            except (TypeError, ValueError):
                count = 1
            if count <= 0:
                continue
            key = (category, severity, _legacy_threat_channel(category), _threat_hour(seen))
            totals[key] = totals.get(key, 0) + count
    conn.execute("DROP TABLE IF EXISTS threat_events_v3")
    conn.execute(_THREAT_TABLE.format(name="threat_events_v3"))
    conn.executemany(
        "INSERT INTO threat_events_v3 (category, severity, channel, hour, count) VALUES (?,?,?,?,?)",
        [(*key, count) for key, count in totals.items()],
    )
    conn.execute("DROP TABLE IF EXISTS threat_events")
    conn.execute("ALTER TABLE threat_events_v3 RENAME TO threat_events")


#: Ordered schema steps: step N moves a database from user_version N-1 to N.
#: Append new steps; never edit, reorder or remove a released one.
MIGRATIONS: list[MigrationStep] = [
    _migration_1_baseline,
    _migration_2_share_expiry,
    _migration_3_threat_totals,
]


def migrate(conn: sqlite3.Connection, ctx: MigrationContext) -> int:
    """Apply every step above the file's `PRAGMA user_version`, in order.

    Each step commits together with its version bump, so a failing step leaves
    the file at the previous version. `conn` must be in autocommit mode
    (``isolation_level=None``) so these explicit transactions are the only ones.
    Returns the version the file ends at.
    """
    steps = list(MIGRATIONS)
    latest = len(steps)
    current = _user_version(conn)
    if current > latest:
        logger.warning(
            "database schema version %s is newer than this code (%s); not migrating", current, latest
        )
        return current
    for version in range(current + 1, latest + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have applied this step while we waited for the lock.
            if _user_version(conn) < version:
                steps[version - 1](conn, ctx)
                conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return _user_version(conn)


class PlanConflict(RuntimeError):
    """A plan version is already stored: another turn on the same trip saved it first."""


class SQLiteStore:
    def __init__(self, path: Path, share_ttl_days: int = DEFAULT_SHARE_TTL_DAYS):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.share_ttl_days = share_ttl_days
        self._lock = threading.RLock()
        context = MigrationContext(now=datetime.now(timezone.utc), share_ttl_days=share_ttl_days)
        with self._lock, closing(self._connect(autocommit=True)) as conn:
            self.schema_version = migrate(conn, context)

    def _connect(self, autocommit: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=20,
            check_same_thread=False,
            # Autocommit leaves transaction control to explicit BEGIN/COMMIT.
            isolation_level=None if autocommit else "",
        )
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

    def adopt_sessions(self, client_token: str, user_id: str) -> int:
        """Give the account every guest session made with ``client_token``; returns how many moved.

        Only sessions nobody owns yet are moved, so a second account signing in from the same
        browser cannot take what the first one already holds.
        """
        if not client_token or not user_id:
            return 0
        with self._lock, self._connect() as conn:
            moved = conn.execute(
                "UPDATE journeys SET user_id=? WHERE client_token=? AND user_id=''", (user_id, client_token)
            )
            return moved.rowcount

    def merge_preferences(self, from_token: str, to_token: str) -> PreferenceProfile:
        """Move one profile into another (guest -> account on sign-in): the target's preferences first,
        then the source's. The source's profile is gone afterwards, so clearing the target's memory
        leaves nothing behind on the old token."""
        source = self.get_preferences(from_token)
        target = self.get_preferences(to_token)
        merged: list[str] = []
        for value in (target.preferences if target else []) + (source.preferences if source else []):
            if value and value not in merged:
                merged.append(value)
        profile = PreferenceProfile(client_token=to_token, preferences=merged)
        self.upsert_preferences(profile)
        if from_token != to_token:
            with self._lock, self._connect() as conn:
                conn.execute("DELETE FROM taste_profiles WHERE client_token=?", (from_token,))
        return profile

    def touch_session(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE journeys SET updated_at=? WHERE id=?", (_now(), session_id))

    def save_context(self, context: TripContext) -> None:
        """Store a trip's form, cut to the form's limits first (in place, so the caller holds what is stored):
        every writer ends here, and the understanding step appends to the lists without a check of its own."""
        context.fit_to_limits()
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
            # A deleted trip must not stay readable through a link shared earlier.
            shares = conn.execute("DELETE FROM share_links WHERE session_id=?", (session_id,)).rowcount or 0
            sessions = conn.execute("DELETE FROM journeys WHERE id=?", (session_id,)).rowcount or 0
        return {
            "messages": int(messages),
            "plans": int(plans),
            "contexts": int(contexts),
            "shares": int(shares),
            "sessions": int(sessions),
        }

    def delete_messages(self, session_id: str) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM turns WHERE session_id=?", (session_id,))
            return int(cursor.rowcount or 0)

    def clear_memory(self, owner: str) -> dict[str, int]:
        """Delete one identity's saved trips and preference profile, nobody else's."""
        if not owner:
            return {"saved_trips": 0, "preferences": 0}
        with self._lock, self._connect() as conn:
            saved = conn.execute("DELETE FROM kept_journeys WHERE owner=?", (owner,)).rowcount or 0
            prefs = conn.execute("DELETE FROM taste_profiles WHERE client_token=?", (owner,)).rowcount or 0
        return {"saved_trips": int(saved), "preferences": int(prefs)}

    def client_profile(self, client_token: str) -> dict[str, Any]:
        # An account holds its trips by `user_id`, and an adopted trip keeps the guest token it was made
        # with; a guest holds the trips made with its token that no account has taken.
        if client_token.startswith(USER_PREFIX):
            held, held_args = "s.user_id=?", (client_token[len(USER_PREFIX):],)
        else:
            held, held_args = "s.client_token=? AND s.user_id=''", (client_token,)
        with self._connect() as conn:
            sessions = conn.execute(f"SELECT COUNT(*) AS n FROM journeys s WHERE {held}", held_args).fetchone()["n"]
            rows = conn.execute(
                "SELECT c.data FROM journey_state c JOIN journeys s ON s.id=c.session_id "
                f"WHERE {held} ORDER BY s.updated_at DESC LIMIT 20",
                held_args,
            ).fetchall()
            saved = conn.execute(
                "SELECT COUNT(*) AS n FROM kept_journeys WHERE owner=?", (client_token,)
            ).fetchone()["n"]
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

    def save_plan(self, plan: TripPlan, change_summary: str | None, *, replace: bool = False) -> None:
        """Store one plan version. A version that is already stored is refused (`PlanConflict`),
        not overwritten: another turn saved it first. `replace=True` rewrites the same version in
        place, for an edit that changed nothing but its own notes. The version keeps its row and
        its creation time, so rewriting an older version never makes it the latest. No summary
        (`None`) leaves a rewritten version's summary as it is, and is the plan's own on a new row."""
        try:
            with self._lock, self._connect() as conn:
                if replace:
                    rewritten = conn.execute(
                        "UPDATE itineraries SET data=?, change_summary=COALESCE(?, change_summary) "
                        "WHERE session_id=? AND trip_id=? AND version=?",
                        (plan.model_dump_json(), change_summary, plan.session_id, plan.trip_id, plan.version),
                    )
                    if rewritten.rowcount:
                        return
                conn.execute(
                    "INSERT INTO itineraries (trip_id, session_id, version, data, change_summary, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        plan.trip_id,
                        plan.session_id,
                        plan.version,
                        plan.model_dump_json(),
                        plan.change_summary if change_summary is None else change_summary,
                        plan.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise PlanConflict(f"version {plan.version} of this trip is already saved") from exc

    def get_plan(self, session_id: str, version: int | None = None) -> TripPlan | None:
        """The plan of the session's newest trip: its highest version, or the one numbered ``version``.
        A version is a number inside one trip, so an older trip of the session is not reached by it."""
        with self._connect() as conn:
            if version is None:
                # The highest version of the newest trip: the number decides, never a timestamp.
                row = conn.execute(
                    f"SELECT data FROM itineraries WHERE session_id=? AND trip_id={_NEWEST_TRIP} "
                    "ORDER BY version DESC LIMIT 1",
                    (session_id, session_id),
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT data FROM itineraries WHERE session_id=? AND version=? AND trip_id={_NEWEST_TRIP}",
                    (session_id, version, session_id),
                ).fetchone()
        return TripPlan.model_validate_json(row["data"]) if row else None

    def list_versions(self, session_id: str) -> list[dict[str, Any]]:
        """The versions of the session's newest trip, newest first: the ones ``get_plan`` finds by number."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT trip_id, version, change_summary, created_at, data FROM itineraries "
                f"WHERE session_id=? AND trip_id={_NEWEST_TRIP} ORDER BY version DESC",
                (session_id, session_id),
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

    def save_saved_trip(self, trip: SavedTrip, owner: str | None = None) -> None:
        """Store a useful trip for `owner`; the same owner and trip keep one row."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kept_journeys (id, trip_id, destination, rating, stars, summary, embedding, data, created_at, owner) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                    owner or None,
                ),
            )

    def reassign_saved_trips(self, from_owner: str, to_owner: str) -> int:
        """Move one identity's saved trips to another (guest -> account on sign-in).

        A trip the target already saved is replaced by the moved copy, so the
        owner+trip key stays unique.
        """
        if not from_owner or not to_owner or from_owner == to_owner:
            return 0
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE OR REPLACE kept_journeys SET owner=? WHERE owner=?", (to_owner, from_owner)
            )
            return int(cursor.rowcount or 0)

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
        """Every owner's saved trips, for aggregate trending and seeding only.

        Never return this to a caller directly; use `list_saved_trips_for_owner`.
        """
        # Sort in Python: a large SQL LIMIT can push SQLite into a temp-file
        # sort, which fails on hosts where the temp directory is restricted.
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM kept_journeys").fetchall()
        trips = [SavedTrip.model_validate_json(row["data"]) for row in rows]
        trips.sort(key=lambda trip: trip.created_at, reverse=True)
        return trips[:limit]

    def list_saved_trips_for_owner(self, owner: str, limit: int = 20) -> list[SavedTrip]:
        """One identity's saved trips, newest first. Ownerless rows never match."""
        if not owner:
            return []
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM kept_journeys WHERE owner=?", (owner,)).fetchall()
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
        owner: str,
        limit: int = 3,
    ) -> list[tuple[float, SavedTrip]]:
        """Similar saved trips of one owner only; no owner recalls nothing."""
        if not owner:
            return []
        with self._connect() as conn:
            rows = conn.execute("SELECT data FROM kept_journeys WHERE owner=?", (owner,)).fetchall()
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

    def create_share(self, session_id: str, plan: TripPlan) -> tuple[str, str]:
        """Publish a read-only copy of `plan`; returns (slug, expires_at in ISO 8601 UTC)."""
        slug = "trip_" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
        expires_at = _share_expiry(datetime.now(timezone.utc), self.share_ttl_days)
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
                "INSERT INTO share_links (slug, session_id, trip_id, version, data, created_at, expires_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (slug, session_id, plan.trip_id, plan.version, _dump(public), _now(), expires_at),
            )
        return slug, expires_at

    def get_share(self, slug: str) -> dict[str, Any] | None:
        """The shared trip, or None when the link is unknown, revoked or expired."""
        with self._connect() as conn:
            row = conn.execute("SELECT data, expires_at FROM share_links WHERE slug=?", (slug,)).fetchone()
        if not row or not _share_is_live(row["expires_at"]):
            return None
        return json.loads(row["data"])

    def revoke_shares(self, session_id: str) -> int:
        """Delete every share link of a session; returns how many were removed."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM share_links WHERE session_id=?", (session_id,))
            return int(cursor.rowcount or 0)

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

    def tool_calls(self, request_id: str | None = None) -> list[dict[str, Any]]:
        """The tool log, oldest first; one request's spans when `request_id` is given."""
        query = "SELECT request_id, tool, status, latency_ms, error_class, source FROM tool_spans"
        params: tuple[Any, ...] = ()
        if request_id is not None:
            query += " WHERE request_id=?"
            params = (request_id,)
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY id", params).fetchall()
        return [dict(row) for row in rows]

    def log_injection(self, category: str, severity: str, channel: str = "user_message") -> None:
        """Count one suspected injection in its category, severity, channel and UTC hour.

        Proposal DB-06: the log holds totals only. Nothing about the message,
        the traveller or the session is passed in, and labels outside the known
        shapes are counted as "other" so free text cannot land in a label.
        """
        key = _threat_key(category, severity, channel)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO threat_events (category, severity, channel, hour, count) VALUES (?,?,?,?,1) "
                "ON CONFLICT(category, severity, channel, hour) DO UPDATE SET count=count+1",
                (*key, _threat_hour()),
            )

    def injection_summary(self) -> list[dict[str, Any]]:
        """Totals per category, severity and channel; `updated_at` is the latest hour counted."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category, severity, channel, SUM(count) AS count, MAX(hour) AS updated_at "
                "FROM threat_events GROUP BY category, severity, channel "
                "ORDER BY count DESC, category, severity, channel"
            ).fetchall()
        return [dict(row) for row in rows]

    def injection_hourly(self, limit: int = 168) -> list[dict[str, Any]]:
        """The hourly counters, newest hour first (a week of hours by default)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT category, severity, channel, hour, count FROM threat_events "
                "ORDER BY hour DESC, category, severity, channel LIMIT ?",
                (max(int(limit), 1),),
            ).fetchall()
        return [dict(row) for row in rows]

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
