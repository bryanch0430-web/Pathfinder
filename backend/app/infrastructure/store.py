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

from pydantic import ValidationError

from app.domain.credentials import StoredCredential
from app.domain.schedule import separate_overlaps
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


def _suggestion_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "skill": row["skill"],
        "summary": row["summary"],
        "rationale": row["rationale"],
        "body": row["body"],
        "case_ids": json.loads(row["case_ids"] or "[]"),
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
        "kind": row["kind"] if "kind" in row.keys() else "skill",
        "setting_value": row["setting_value"] if "setting_value" in row.keys() else "",
    }

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


_RUNTIME_BASELINE: dict[int, dict[str, float]] = {}
_GATE_BASELINE: dict[int, str] = {}
_TEXT_BASELINE: dict[int, dict[str, str]] = {}
_GATE_CHOICES = frozenset({"jev", "plain", "reason", "fast"})
# Keep these next to the provider catalogue in llm_providers.PROVIDERS.
_GATE_PROVIDERS = frozenset({"anthropic", "openai", "openai-compatible", "poe", "xai", "gemini", "deepseek"})
_REASONING = frozenset({"none", "low", "medium", "high"})
_HTTP_URL = re.compile(r"https?://\S+$")


def _runtime_baseline(settings: Any) -> dict[str, float]:
    return {
        "router_threshold": float(settings.router_threshold),
        "memory_similarity": float(settings.memory_similarity_cutoff),
        "preplan_budget_seconds": float(settings.preplan_budget_seconds),
        "max_repairs": float(settings.max_repairs),
        "refine_max_rounds": float(getattr(settings, "refine_max_rounds", 3)),
        "reviewer_temperature": float(getattr(settings, "reviewer_temperature", 0.0)),
        "reviewer_max_tokens": float(getattr(settings, "reviewer_max_tokens", 4096)),
        "writer_temperature": float(getattr(settings, "llm_temperature", 0.2)),
        "writer_max_tokens": float(getattr(settings, "llm_max_tokens", 4096)),
        "conversation_turns": float(getattr(settings, "conversation_turns", 10)),
        "reviewer_timeout_seconds": float(getattr(settings, "reviewer_timeout_seconds", 20)),
        "turn_deadline_seconds": float(getattr(settings, "turn_deadline_seconds", 180)),
        "llm_timeout_seconds": float(getattr(settings, "llm_timeout_seconds", 90)),
        "weather_fresh_seconds": float(settings.weather_fresh_seconds),
        "ticket_fresh_seconds": float(settings.ticket_fresh_seconds),
        "hotel_fresh_seconds": float(settings.hotel_fresh_seconds),
        "attraction_fresh_seconds": float(settings.attraction_fresh_seconds),
        "gate_plain_temperature": float(getattr(settings, "gate_plain_temperature", 0.0)),
        "gate_plain_max_tokens": float(getattr(settings, "gate_plain_max_tokens", 1024)),
        "gate_reason_temperature": float(getattr(settings, "gate_reason_temperature", 0.2)),
        "gate_reason_max_tokens": float(getattr(settings, "gate_reason_max_tokens", 1024)),
    }


def _text_baseline(settings: Any) -> dict[str, str]:
    return {
        "decision_model": str(getattr(settings, "decision_model", "") or ""),
        "decision_api_url": str(getattr(settings, "decision_api_url", "") or ""),
        "decision_api_key": str(getattr(settings, "decision_api_key", "") or ""),
        "gate_fast_model": str(getattr(settings, "gate_fast_model", "") or ""),
        "gate_fast_base_url": str(getattr(settings, "gate_fast_base_url", "") or ""),
        "gate_fast_api_key": str(getattr(settings, "gate_fast_api_key", "") or ""),
        "gate_plain_model": str(getattr(settings, "gate_plain_model", "") or ""),
        "gate_plain_provider": str(getattr(settings, "gate_plain_provider", "") or "poe"),
        "gate_plain_base_url": str(getattr(settings, "gate_plain_base_url", "") or ""),
        "gate_plain_reasoning": str(getattr(settings, "gate_plain_reasoning", "") or "none"),
        "gate_plain_api_key": str(getattr(settings, "gate_plain_api_key", "") or ""),
        "gate_reason_model": str(getattr(settings, "gate_reason_model", "") or ""),
        "gate_reason_provider": str(getattr(settings, "gate_reason_provider", "") or "poe"),
        "gate_reason_base_url": str(getattr(settings, "gate_reason_base_url", "") or ""),
        "gate_reason_reasoning": str(getattr(settings, "gate_reason_reasoning", "") or "low"),
        "gate_reason_api_key": str(getattr(settings, "gate_reason_api_key", "") or ""),
        "poe_api_key": str(getattr(settings, "poe_api_key", "") or ""),
        "reviewer_model": str(getattr(settings, "reviewer_model", "") or ""),
        "reviewer_provider": str(getattr(settings, "reviewer_provider", "") or "poe"),
        "reviewer_base_url": str(getattr(settings, "reviewer_base_url", "") or ""),
        "reviewer_reasoning": str(getattr(settings, "reviewer_reasoning", "") or "none"),
        "reviewer_api_key": str(getattr(settings, "reviewer_api_key", "") or ""),
        "handoff_api_key": str(getattr(settings, "handoff_api_key", "") or ""),
        "writer_reasoning": str(getattr(settings, "writer_reasoning", "") or "none"),
    }


def _runtime_token(raw: dict[str, str], key: str, default: str, limit: int) -> str:
    if key not in raw or str(raw[key]).strip() == "":
        return default
    value = str(raw[key]).strip()
    if len(value) > limit or any(character.isspace() for character in value):
        raise ValueError(f"{key} must be {limit} characters or fewer, with no spaces")
    return value


def _or_default(raw: dict[str, str], key: str, default: str, limit: int, *, url: bool) -> str:
    try:
        return _runtime_url(raw, key, default) if url else _runtime_token(raw, key, default, limit)
    except ValueError:
        return default


def _runtime_url(raw: dict[str, str], key: str, default: str) -> str:
    if key not in raw or str(raw[key]).strip() == "":
        return default
    value = str(raw[key]).strip().rstrip("/")
    if len(value) > 300 or _HTTP_URL.fullmatch(value) is None:
        raise ValueError(f"{key} must be an http(s) address")
    return value


def _or_choice(raw: dict[str, str], key: str, default: str, allowed: frozenset[str]) -> str:
    try:
        return _runtime_choice(raw, key, default, allowed)
    except ValueError:
        return default if default in allowed else next(iter(allowed))


def _runtime_choice(raw: dict[str, str], key: str, default: str, allowed: frozenset[str]) -> str:
    if key not in raw or str(raw[key]).strip() == "":
        return default
    value = str(raw[key]).strip().lower()
    if value not in allowed:
        names = " or ".join(sorted(allowed))
        raise ValueError(f"{key} must be {names}")
    return value


def _runtime_float(raw: dict[str, str], key: str, default: float, low: float, high: float) -> float:
    if key not in raw or raw[key].strip() == "":
        return default
    try:
        value = float(raw[key])
    except ValueError as exc:
        raise ValueError(f"{key} is not a number") from exc
    if value < low or value > high:
        raise ValueError(f"{key} must be between {low:g} and {high:g}")
    return value


def _runtime_flag(raw: dict[str, str], key: str) -> bool:
    """A stored 0, false, no, or off turns the pattern off. A missing key stays on."""
    if key not in raw:
        return True
    return str(raw[key]).strip().lower() not in {"0", "false", "no", "off"}


def _runtime_text(raw: dict[str, str], key: str, limit: int = 300) -> str:
    if key not in raw:
        return ""
    return str(raw[key])[:limit]


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


def _migration_4_llm_credentials(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """Users' own LLM keys: one row per saved key, the key itself only as ciphertext."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_credentials ("
        "id TEXT PRIMARY KEY, owner TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', "
        "ciphertext BLOB NOT NULL, key_tail TEXT NOT NULL DEFAULT '', "
        "active INTEGER NOT NULL DEFAULT 0, use_for_search INTEGER NOT NULL DEFAULT 1, "
        "last_test_ok INTEGER, last_tested_at TEXT, last_error TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_credentials_owner ON llm_credentials(owner)")


def _migration_8_desk_agent(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """The account name of the person holding a chat, shown to the traveller."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(desk_holds)")}
    if "agent_name" not in columns:
        conn.execute("ALTER TABLE desk_holds ADD COLUMN agent_name TEXT NOT NULL DEFAULT ''")


def _migration_7_desk_notes(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """A short note an operator keeps on a session. It stays when the chat is handed back."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS desk_notes ("
        "session_id TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)"
    )


def _migration_6_exemplars_and_desk(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """Which chats a person is holding. The route_examples table is unused; the step stays because it already shipped."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS route_examples ("
        "id TEXT PRIMARY KEY, utterance TEXT NOT NULL, intent TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS desk_holds ("
        "session_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
    )


def _migration_10_runtime_policy(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """Admin overrides for the policy thresholds and freshness windows. Absent keys keep the environment."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runtime_policy (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )


def _migration_9_escalation_and_prompts(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """When to hand a chat to a person, and any instruction an admin has replaced."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS escalation_policy ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), enabled INTEGER NOT NULL, message TEXT NOT NULL, "
        "frustration_at INTEGER, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS escalation_examples ("
        "id TEXT PRIMARY KEY, utterance TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS prompt_overrides ("
        "name TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )


def _migration_11_gate_secrets(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """Encrypted keys for the orchestrator's own models. The model names stay in runtime_policy."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gate_secrets ("
        "name TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, updated_at TEXT NOT NULL)"
    )


def _migration_13_desk_presence(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """The last time the traveller's page asked about this session. A closed page stops updating it."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS desk_presence ("
        "session_id TEXT PRIMARY KEY, seen_at TEXT NOT NULL)"
    )


def _migration_14_prompt_suggestions(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """Draft skill changes an admin has not accepted. Accepting one writes a prompt override."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS prompt_suggestions ("
        "id TEXT PRIMARY KEY, status TEXT NOT NULL, skill TEXT NOT NULL, summary TEXT NOT NULL, "
        "rationale TEXT NOT NULL, body TEXT NOT NULL, case_ids TEXT NOT NULL, created_at TEXT NOT NULL, "
        "decided_at TEXT)"
    )


def _migration_15_operator_cases(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """Cases an operator adds by hand, and whether a suggestion replaces a skill or a number."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS operator_cases ("
        "id TEXT PRIMARY KEY, session_id TEXT NOT NULL DEFAULT '', session_state TEXT NOT NULL DEFAULT '', "
        "input TEXT NOT NULL, expected TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(prompt_suggestions)")}
    if "kind" not in columns:
        conn.execute("ALTER TABLE prompt_suggestions ADD COLUMN kind TEXT NOT NULL DEFAULT 'skill'")
    if "setting_value" not in columns:
        conn.execute("ALTER TABLE prompt_suggestions ADD COLUMN setting_value TEXT NOT NULL DEFAULT ''")


def _migration_16_case_checks(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """The latest re-check of a failed case: whether it passes with the skills and numbers in force now."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS case_checks ("
        "case_id TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL, "
        "output_summary TEXT NOT NULL DEFAULT '', judge_reason TEXT NOT NULL DEFAULT '', "
        "checked_at TEXT NOT NULL, PRIMARY KEY (case_id, source))"
    )


def _migration_12_serpapi_keys(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """SerpAPI keys kept for rotation. The key itself is encrypted; the row stores only its tail and status."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS serpapi_keys ("
        "id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, key_tail TEXT NOT NULL DEFAULT '', "
        "label TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, position INTEGER NOT NULL, updated_at TEXT NOT NULL)"
    )


def _migration_5_credential_base_url(conn: sqlite3.Connection, ctx: MigrationContext) -> None:
    """A key's own endpoint: set for the generic OpenAI-compatible provider, empty for a fixed vendor."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_credentials)")}
    if "base_url" not in columns:
        conn.execute("ALTER TABLE llm_credentials ADD COLUMN base_url TEXT NOT NULL DEFAULT ''")


#: Ordered schema steps: step N moves a database from user_version N-1 to N.
#: Append new steps; never edit, reorder or remove a released one.
MIGRATIONS: list[MigrationStep] = [
    _migration_1_baseline,
    _migration_2_share_expiry,
    _migration_3_threat_totals,
    _migration_4_llm_credentials,
    _migration_5_credential_base_url,
    _migration_6_exemplars_and_desk,
    _migration_7_desk_notes,
    _migration_8_desk_agent,
    _migration_9_escalation_and_prompts,
    _migration_10_runtime_policy,
    _migration_11_gate_secrets,
    _migration_12_serpapi_keys,
    _migration_13_desk_presence,
    _migration_14_prompt_suggestions,
    _migration_15_operator_cases,
    _migration_16_case_checks,
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


def _ensure_returns(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS desk_returns (session_id TEXT PRIMARY KEY, released_at TEXT NOT NULL)"
    )


def _ensure_escalation_columns(conn: sqlite3.Connection) -> None:
    present = {row[1] for row in conn.execute("PRAGMA table_info(escalation_policy)")}
    adds = {
        "match_mode": "ALTER TABLE escalation_policy ADD COLUMN match_mode TEXT NOT NULL DEFAULT 'examples'",
        "use_jev": "ALTER TABLE escalation_policy ADD COLUMN use_jev INTEGER NOT NULL DEFAULT 0",
        "stuck_on": "ALTER TABLE escalation_policy ADD COLUMN stuck_on INTEGER NOT NULL DEFAULT 0",
        "stuck_after": "ALTER TABLE escalation_policy ADD COLUMN stuck_after INTEGER NOT NULL DEFAULT 3",
        "words": "ALTER TABLE escalation_policy ADD COLUMN words TEXT NOT NULL DEFAULT ''",
        "parts": "ALTER TABLE escalation_policy ADD COLUMN parts TEXT NOT NULL DEFAULT ''",
    }
    for name, sql in adds.items():
        if name not in present:
            conn.execute(sql)


def _clean_rows(value: Any, limit: int, length: int) -> list[dict[str, Any]]:
    """One text and its own score. A bare string keeps a score of 60."""
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value or []:
        if isinstance(raw, dict):
            text = raw.get("text") or raw.get("utterance") or raw.get("word") or ""
            score = raw.get("score", 60)
        else:
            text = raw
            score = 60
        line = str(text or "").strip()[:length]
        key = line.casefold()
        if not line or key in seen:
            continue
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            number = 60
        else:
            number = max(0, min(100, int(score)))
        seen.add(key)
        kept.append({"text": line, "score": number})
        if len(kept) >= limit:
            break
    return kept


def _clean_part(raw: Any, *, engine_default: str) -> dict[str, Any]:
    body = raw if isinstance(raw, dict) else {}
    engine = str(body.get("engine") or engine_default)
    if engine in {"examples", "phrases"}:
        engine = "keywords"
    if engine not in {"keywords", "jev"}:
        engine = "keywords" if engine_default not in {"keywords", "jev"} else engine_default
    score = body.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = None
    else:
        score = max(0, min(100, int(score)))
    try:
        after = int(body.get("after") or 3)
    except (TypeError, ValueError):
        after = 3
    return {
        "enabled": bool(body.get("enabled", True)),
        "engine": engine,
        "words": _clean_rows(body.get("words"), 80, 80),
        "examples": _clean_rows(body.get("examples"), 40, 600),
        "score": score,
        "message": str(body.get("message") or "").strip()[:2000],
        "after": min(8, max(2, after)),
    }


def _default_parts(
    *,
    enabled: bool,
    message: str,
    frustration_at: int | None,
    match_mode: str,
    use_jev: bool,
    stuck_on: bool,
    stuck_after: int,
    words: list[str],
    examples: list[str],
) -> dict[str, Any]:
    from app.application.escalation import FAIL_PHRASES, LIVE_AGENT_PHRASES
    from app.application.sentiment import strong_words

    return {
        "hand": _clean_part(
            {
                "enabled": enabled,
                "engine": "keywords",
                "words": [{"text": phrase, "score": 60} for phrase in LIVE_AGENT_PHRASES],
                "examples": examples,
                "score": frustration_at,
                "message": message,
            },
            engine_default="examples",
        ),
        "sentiment": _clean_part(
            {
                "enabled": True,
                "engine": "jev" if use_jev else "keywords",
                "words": [{"text": word, "score": 80} for word in sorted(strong_words())],
                "examples": [],
                "score": 60,
            },
            engine_default="keywords",
        ),
        "stuck": _clean_part(
            {
                "enabled": stuck_on,
                "engine": "keywords",
                "words": [{"text": phrase, "score": 80} for phrase in FAIL_PHRASES],
                "examples": [],
                "after": stuck_after,
            },
            engine_default="phrases",
        ),
    }


def _separate_hand_phrases(parts: dict[str, Any]) -> dict[str, Any]:
    """The escalate list used to be a copy of the anger words. Put the live-agent phrases there instead."""
    from app.application.escalation import LIVE_AGENT_PHRASES
    from app.application.sentiment import negative_words

    lexicon = {word.casefold() for word in negative_words()}
    texts = [str(row.get("text") or "").casefold() for row in parts["hand"].get("words") or [] if str(row.get("text") or "").strip()]
    if len(texts) >= 8 and sum(text in lexicon for text in texts) / len(texts) > 0.8:
        parts["hand"]["words"] = [{"text": phrase, "score": 60} for phrase in LIVE_AGENT_PHRASES]
    return parts


def _parts_from_row(
    row: sqlite3.Row | None,
    *,
    enabled: bool,
    message: str,
    frustration_at: int | None,
    match_mode: str,
    use_jev: bool,
    stuck_on: bool,
    stuck_after: int,
    words: list[str],
    examples: list[str],
) -> dict[str, Any]:
    raw = ""
    if row is not None and "parts" in row.keys():
        raw = str(row["parts"] or "")
    if raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            parts = {
                "hand": _clean_part(parsed.get("hand"), engine_default="examples"),
                "sentiment": _clean_part(parsed.get("sentiment"), engine_default="keywords"),
                "stuck": _clean_part(parsed.get("stuck"), engine_default="phrases"),
            }
            return _separate_hand_phrases(parts)
    return _default_parts(
        enabled=enabled,
        message=message,
        frustration_at=frustration_at,
        match_mode=match_mode,
        use_jev=use_jev,
        stuck_on=stuck_on,
        stuck_after=stuck_after,
        words=words,
        examples=examples,
    )


def _policy_row(row: sqlite3.Row | None, examples: list[sqlite3.Row]) -> dict[str, Any]:
    base = {
        "enabled": True,
        "message": "I'll pass this chat to a person. They will reply here.",
        "frustration_at": None,
        "match_mode": "examples",
        "use_jev": False,
        "stuck_on": False,
        "stuck_after": 3,
        "words": [],
        "examples": [{"id": item["id"], "utterance": item["utterance"]} for item in examples],
    }
    if row is not None:
        keys = row.keys()
        base.update(
            {
                "enabled": bool(row["enabled"]),
                "message": row["message"],
                "frustration_at": row["frustration_at"],
                "match_mode": row["match_mode"] if "match_mode" in keys and row["match_mode"] in {"examples", "keywords"} else "examples",
                "use_jev": bool(row["use_jev"]) if "use_jev" in keys else False,
                "stuck_on": bool(row["stuck_on"]) if "stuck_on" in keys else False,
                "stuck_after": int(row["stuck_after"]) if "stuck_after" in keys and row["stuck_after"] else 3,
            }
        )
        stored = [line.strip() for line in str(row["words"] if "words" in keys else "").splitlines() if line.strip()]
        if stored:
            base["words"] = stored[:80]
        else:
            from app.application.sentiment import negative_words

            base["words"] = sorted(negative_words())
    else:
        from app.application.sentiment import negative_words

        base["words"] = sorted(negative_words())
    parts = _parts_from_row(
        row,
        enabled=bool(base["enabled"]),
        message=str(base["message"]),
        frustration_at=base["frustration_at"],
        match_mode=str(base["match_mode"]),
        use_jev=bool(base["use_jev"]),
        stuck_on=bool(base["stuck_on"]),
        stuck_after=int(base["stuck_after"]),
        words=list(base["words"]),
        examples=[str(item["utterance"]) for item in base["examples"]],
    )
    hand = parts["hand"]
    base.update(
        {
            "parts": parts,
            "enabled": hand["enabled"],
            "message": hand["message"] or base["message"],
            "frustration_at": hand["score"],
            "match_mode": "keywords" if hand["engine"] == "keywords" else "examples",
            "use_jev": hand["engine"] == "jev" or parts["sentiment"]["engine"] == "jev" or parts["stuck"]["engine"] == "jev",
            "stuck_on": parts["stuck"]["enabled"],
            "stuck_after": parts["stuck"]["after"],
            "words": [row["text"] for row in hand["words"]],
            "examples": [{"id": "", "utterance": row["text"]} for row in hand["examples"]],
        }
    )
    return base


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

    def mark_seen(self, session_id: str) -> None:
        """The traveller's page is open. This does not count as a change to the trip."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO desk_presence (session_id, seen_at) VALUES (?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET seen_at=excluded.seen_at",
                (session_id, _now()),
            )

    def seen_map(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT session_id, seen_at FROM desk_presence").fetchall()
        return {str(row["session_id"]): str(row["seen_at"]) for row in rows}

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

    def attach_decisions_since(self, session_id: str, after: datetime, decision: dict[str, Any]) -> None:
        """Keep this turn's model reasoning on the assistant rows of the turn that started at ``after``.

        Compared in Python: a text compare of the stored timestamps misses a row when one
        carries ``+00:00`` and another does not, and then a reload has no decision to show.
        """
        after_cmp = after if after.tzinfo else after.replace(tzinfo=timezone.utc)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, meta, created_at FROM turns WHERE session_id=? AND role='assistant' ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
            matched = []
            for row in rows:
                created = datetime.fromisoformat(row["created_at"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created + timedelta(seconds=2) >= after_cmp:
                    matched.append(row)
                    continue
                break
            if not matched and rows:
                created = datetime.fromisoformat(rows[0]["created_at"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if abs((created - after_cmp).total_seconds()) < 180:
                    matched = [rows[0]]
            for row in matched:
                meta = json.loads(row["meta"] or "{}")
                meta["decision"] = decision
                conn.execute("UPDATE turns SET meta=? WHERE id=?", (_dump(meta), row["id"]))

    def set_message_meta(self, message_id: str, meta: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE turns SET meta=? WHERE id=?", (_dump(meta), message_id))

    def is_held(self, session_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT session_id FROM desk_holds WHERE session_id=?", (session_id,)).fetchone()
        return row is not None

    def hold_chat(self, session_id: str, agent_name: str = "") -> None:
        with self._lock, self._connect() as conn:
            _ensure_returns(conn)
            conn.execute(
                "INSERT INTO desk_holds (session_id, agent_name, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET agent_name=excluded.agent_name, updated_at=excluded.updated_at",
                (session_id, agent_name.strip()[:80], _now()),
            )
            conn.execute("DELETE FROM desk_returns WHERE session_id=?", (session_id,))

    def returned_chat(self, session_id: str) -> bool:
        """True after an operator sent the chat back, until someone holds it again."""
        with self._connect() as conn:
            _ensure_returns(conn)
            row = conn.execute("SELECT session_id FROM desk_returns WHERE session_id=?", (session_id,)).fetchone()
        return row is not None

    def runtime_overrides(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM runtime_policy").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def clear_runtime_key(self, key: str) -> None:
        """Drop one stored number so the environment value is used again."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM runtime_policy WHERE key=?", (key,))

    def save_case_check(self, *, case_id: str, source: str, status: str, output_summary: str, judge_reason: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO case_checks (case_id, source, status, output_summary, judge_reason, checked_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(case_id, source) DO UPDATE SET status=excluded.status, "
                "output_summary=excluded.output_summary, judge_reason=excluded.judge_reason, checked_at=excluded.checked_at",
                (case_id, source, status, output_summary[:400], judge_reason[:400], _now()),
            )

    def case_checks(self) -> dict[tuple[str, str], dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM case_checks").fetchall()
        return {
            (row["case_id"], row["source"]): {
                "status": row["status"],
                "output_summary": row["output_summary"],
                "judge_reason": row["judge_reason"],
                "checked_at": row["checked_at"],
            }
            for row in rows
        }

    def add_operator_case(self, *, session_id: str, session_state: str, message: str, expected: str, note: str) -> dict[str, str]:
        from app.domain.models import new_id

        row = {
            "id": new_id("case"),
            "session_id": session_id,
            "session_state": session_state,
            "input": message.strip()[:500],
            "expected": expected.strip()[:400],
            "note": note.strip()[:400],
            "created_at": _now(),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO operator_cases (id, session_id, session_state, input, expected, note, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (row["id"], row["session_id"], row["session_state"], row["input"], row["expected"], row["note"], row["created_at"]),
            )
        return row

    def list_operator_cases(self, limit: int = 24) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM operator_cases ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_capture_turns(self, limit: int = 30) -> list[dict[str, str]]:
        """Recent traveller lines from live and finished chats, for an operator to mark as a failure."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.id, t.session_id, t.content, t.created_at, j.updated_at, "
                "(SELECT seen_at FROM desk_presence WHERE session_id=t.session_id) AS seen_at, "
                "(SELECT a.content FROM turns a WHERE a.session_id=t.session_id AND a.role='assistant' "
                "AND a.created_at>=t.created_at ORDER BY a.created_at LIMIT 1) AS reply "
                "FROM turns t JOIN journeys j ON j.id=t.session_id "
                "WHERE t.role='user' ORDER BY t.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_runtime(self, values: dict[str, str]) -> None:
        with self._lock, self._connect() as conn:
            for key, value in values.items():
                conn.execute(
                    "INSERT INTO runtime_policy (key, value, updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, value, _now()),
                )

    def clear_runtime(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM runtime_policy")
            conn.execute("DELETE FROM gate_secrets")

    def check_runtime(self, settings: Any, values: dict[str, str]) -> None:
        """Raise ValueError when a stored number is outside its range. Does not change ``settings``."""
        base = _RUNTIME_BASELINE.get(id(settings)) or _runtime_baseline(settings)
        _runtime_float(values, "router_confidence", base["router_threshold"], 0.0, 1.0)
        _runtime_float(values, "memory_similarity", base["memory_similarity"], 0.0, 1.0)
        _runtime_float(values, "preplan_budget_seconds", base["preplan_budget_seconds"], 1.0, 600.0)
        _runtime_float(values, "max_repairs", base["max_repairs"], 0.0, 6.0)
        _runtime_float(values, "rule_passes", base["refine_max_rounds"], 0.0, 10.0)
        _runtime_float(values, "conversation_turns", base["conversation_turns"], 1.0, 10.0)
        _runtime_float(values, "reviewer_timeout_seconds", base["reviewer_timeout_seconds"], 5.0, 120.0)
        _runtime_float(values, "turn_deadline_seconds", base["turn_deadline_seconds"], 30.0, 900.0)
        _runtime_float(values, "llm_timeout_seconds", base["llm_timeout_seconds"], 5.0, 180.0)
        for key, field in (
            ("weather", "weather_fresh_seconds"),
            ("tickets", "ticket_fresh_seconds"),
            ("hotels", "hotel_fresh_seconds"),
            ("attractions", "attraction_fresh_seconds"),
        ):
            _runtime_float(values, key, base[field] / 3600, 1.0, 24 * 365)
        if "router_gate" in values:
            _runtime_choice(values, "router_gate", "jev", _GATE_CHOICES)
        for key, limit in (
            ("writer_model", 120),
            ("decision_model", 120),
            ("gate_fast_model", 120),
            ("gate_plain_model", 120),
            ("gate_reason_model", 120),
            ("gate_plain_provider", 40),
            ("gate_reason_provider", 40),
        ):
            if key in values:
                _runtime_token(values, key, "", limit)
        for key in ("decision_api_url", "gate_fast_base_url", "gate_plain_base_url", "gate_reason_base_url"):
            if key in values:
                _runtime_url(values, key, "")
        for key, default in (("gate_plain_provider", "poe"), ("gate_reason_provider", "poe")):
            if key in values:
                _runtime_choice(values, key, default, _GATE_PROVIDERS)
        for key, default in (("gate_plain_reasoning", "none"), ("gate_reason_reasoning", "low")):
            if key in values:
                _runtime_choice(values, key, default, _REASONING)
        _runtime_float(values, "gate_plain_temperature", 0.0, 0.0, 2.0)
        _runtime_float(values, "gate_reason_temperature", 0.2, 0.0, 2.0)
        _runtime_float(values, "gate_plain_max_tokens", 1024, 16, 16384)
        _runtime_float(values, "gate_reason_max_tokens", 1024, 16, 16384)
        if "reviewer_model" in values:
            _runtime_token(values, "reviewer_model", "", 120)
        if "reviewer_base_url" in values:
            _runtime_url(values, "reviewer_base_url", "")
        if "reviewer_provider" in values:
            _runtime_choice(values, "reviewer_provider", "poe", _GATE_PROVIDERS)
        if "reviewer_reasoning" in values:
            _runtime_choice(values, "reviewer_reasoning", "none", _REASONING)
        _runtime_float(values, "reviewer_temperature", 0.0, 0.0, 2.0)
        _runtime_float(values, "reviewer_max_tokens", 4096, 16, 16384)
        _runtime_float(values, "writer_temperature", 0.2, 0.0, 2.0)
        _runtime_float(values, "writer_max_tokens", 4096, 16, 16384)
        if "writer_reasoning" in values:
            _runtime_choice(values, "writer_reasoning", "none", _REASONING)

    def apply_runtime(self, settings: Any) -> None:
        """Copy stored overrides onto ``settings``. The first call remembers the environment values."""
        text_base = getattr(settings, "__pf_text_base", None) or _text_baseline(settings)
        base = getattr(settings, "__pf_runtime_base", None) or _runtime_baseline(settings)
        object.__setattr__(settings, "__pf_text_base", text_base)
        object.__setattr__(settings, "__pf_runtime_base", base)
        raw = self.runtime_overrides()
        settings.router_threshold = _runtime_float(raw, "router_confidence", base["router_threshold"], 0.0, 1.0)
        settings.memory_similarity_cutoff = _runtime_float(raw, "memory_similarity", base["memory_similarity"], 0.0, 1.0)
        settings.preplan_budget_seconds = _runtime_float(raw, "preplan_budget_seconds", base["preplan_budget_seconds"], 1.0, 600.0)
        settings.max_repairs = int(_runtime_float(raw, "max_repairs", float(base["max_repairs"]), 0.0, 6.0))
        settings.refine_max_rounds = int(_runtime_float(raw, "rule_passes", float(base["refine_max_rounds"]), 0.0, 10.0))
        settings.conversation_turns = int(_runtime_float(raw, "conversation_turns", float(base["conversation_turns"]), 1.0, 10.0))
        settings.reviewer_timeout_seconds = _runtime_float(raw, "reviewer_timeout_seconds", base["reviewer_timeout_seconds"], 5.0, 120.0)
        settings.turn_deadline_seconds = _runtime_float(raw, "turn_deadline_seconds", base["turn_deadline_seconds"], 30.0, 900.0)
        settings.llm_timeout_seconds = _runtime_float(raw, "llm_timeout_seconds", base["llm_timeout_seconds"], 5.0, 180.0)
        for key, field in (
            ("weather", "weather_fresh_seconds"),
            ("tickets", "ticket_fresh_seconds"),
            ("hotels", "hotel_fresh_seconds"),
            ("attractions", "attraction_fresh_seconds"),
        ):
            hours = _runtime_float(raw, key, base[field] / 3600, 1.0, 24 * 365)
            setattr(settings, field, int(hours) * 3600)
        current = str(getattr(settings, "router_gate", "jev") or "jev").strip().lower()
        gate_base = getattr(settings, "__pf_gate_base", None) or (current if current in _GATE_CHOICES else "jev")
        object.__setattr__(settings, "__pf_gate_base", gate_base)
        try:
            settings.router_gate = _runtime_choice(raw, "router_gate", gate_base, _GATE_CHOICES)
        except ValueError:
            settings.router_gate = gate_base
        settings.writer_model_override = _or_default(raw, "writer_model", "", 120, url=False)
        settings.decision_model = _or_default(raw, "decision_model", text_base["decision_model"], 120, url=False)
        settings.decision_api_url = _or_default(raw, "decision_api_url", text_base["decision_api_url"], 300, url=True)
        settings.gate_fast_model = _or_default(raw, "gate_fast_model", text_base["gate_fast_model"], 120, url=False)
        settings.gate_fast_base_url = _or_default(raw, "gate_fast_base_url", text_base["gate_fast_base_url"], 300, url=True)
        settings.gate_plain_model = _or_default(raw, "gate_plain_model", text_base["gate_plain_model"], 120, url=False)
        settings.gate_reason_model = _or_default(raw, "gate_reason_model", text_base["gate_reason_model"], 120, url=False)
        settings.gate_plain_base_url = _or_default(raw, "gate_plain_base_url", text_base["gate_plain_base_url"], 300, url=True)
        settings.gate_reason_base_url = _or_default(raw, "gate_reason_base_url", text_base["gate_reason_base_url"], 300, url=True)
        settings.gate_plain_provider = _or_choice(raw, "gate_plain_provider", text_base["gate_plain_provider"] or "poe", _GATE_PROVIDERS)
        settings.gate_reason_provider = _or_choice(raw, "gate_reason_provider", text_base["gate_reason_provider"] or "poe", _GATE_PROVIDERS)
        settings.gate_plain_reasoning = _or_choice(raw, "gate_plain_reasoning", text_base["gate_plain_reasoning"] or "none", _REASONING)
        settings.gate_reason_reasoning = _or_choice(raw, "gate_reason_reasoning", text_base["gate_reason_reasoning"] or "low", _REASONING)
        settings.gate_plain_temperature = _runtime_float(raw, "gate_plain_temperature", base["gate_plain_temperature"], 0.0, 2.0)
        settings.gate_reason_temperature = _runtime_float(raw, "gate_reason_temperature", base["gate_reason_temperature"], 0.0, 2.0)
        settings.gate_plain_max_tokens = int(_runtime_float(raw, "gate_plain_max_tokens", base["gate_plain_max_tokens"], 16, 16384))
        settings.gate_reason_max_tokens = int(_runtime_float(raw, "gate_reason_max_tokens", base["gate_reason_max_tokens"], 16, 16384))
        settings.reviewer_model = _or_default(raw, "reviewer_model", text_base["reviewer_model"], 120, url=False)
        settings.reviewer_provider = _or_choice(raw, "reviewer_provider", text_base["reviewer_provider"] or "poe", _GATE_PROVIDERS)
        settings.reviewer_base_url = _or_default(raw, "reviewer_base_url", text_base["reviewer_base_url"], 300, url=True)
        settings.reviewer_reasoning = _or_choice(raw, "reviewer_reasoning", text_base["reviewer_reasoning"] or "none", _REASONING)
        settings.reviewer_temperature = _runtime_float(raw, "reviewer_temperature", base["reviewer_temperature"], 0.0, 2.0)
        settings.reviewer_max_tokens = int(_runtime_float(raw, "reviewer_max_tokens", base["reviewer_max_tokens"], 16, 16384))
        settings.llm_temperature = _runtime_float(raw, "writer_temperature", base["writer_temperature"], 0.0, 2.0)
        settings.llm_max_tokens = int(_runtime_float(raw, "writer_max_tokens", base["writer_max_tokens"], 16, 16384))
        settings.writer_reasoning = _or_choice(raw, "writer_reasoning", text_base["writer_reasoning"] or "none", _REASONING)
        settings.redact_hkid = _runtime_flag(raw, "redact_hkid")
        settings.redact_hk_phone = _runtime_flag(raw, "redact_hk_phone")
        settings.redact_email = _runtime_flag(raw, "redact_email")
        settings.redact_card = _runtime_flag(raw, "redact_card")
        settings.redact_hkid_pattern = _runtime_text(raw, "redact_hkid_pattern")
        settings.redact_hk_phone_pattern = _runtime_text(raw, "redact_hk_phone_pattern")
        settings.redact_email_pattern = _runtime_text(raw, "redact_email_pattern")
        settings.redact_card_pattern = _runtime_text(raw, "redact_card_pattern")


    def delete_runtime_keys(self, keys: list[str]) -> None:
        if not keys:
            return
        with self._lock, self._connect() as conn:
            conn.executemany("DELETE FROM runtime_policy WHERE key=?", [(key,) for key in keys])

    def gate_secret(self, name: str) -> bytes | None:
        with self._connect() as conn:
            row = conn.execute("SELECT ciphertext FROM gate_secrets WHERE name=?", (name,)).fetchone()
        if row is None or row["ciphertext"] is None:
            return None
        return bytes(row["ciphertext"])

    def gate_secret_saved(self, name: str) -> bool:
        return self.gate_secret(name) is not None

    def save_gate_secret(self, name: str, ciphertext: bytes) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO gate_secrets (name, ciphertext, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET ciphertext=excluded.ciphertext, updated_at=excluded.updated_at",
                (name, ciphertext, _now()),
            )

    def clear_gate_secret(self, name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM gate_secrets WHERE name=?", (name,))

    def apply_gate_secrets(self, settings: Any, box: Any) -> None:
        """Put saved orchestrator keys onto ``settings``. A missing row keeps the environment key."""
        from app.infrastructure.secret_box import UnreadableSecret

        base = _TEXT_BASELINE.setdefault(id(settings), _text_baseline(settings))
        for name, field in (
            ("jev", "decision_api_key"),
            ("fast", "gate_fast_api_key"),
            ("poe", "poe_api_key"),
            ("plain", "gate_plain_api_key"),
            ("reason", "gate_reason_api_key"),
            ("reviewer", "reviewer_api_key"),
            ("handoff", "handoff_api_key"),
        ):
            blob = self.gate_secret(name)
            if not blob:
                setattr(settings, field, base[field])
                continue
            try:
                setattr(settings, field, box.decrypt(blob))
            except UnreadableSecret:
                setattr(settings, field, base[field])

    def serpapi_keys(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, key_tail, label, status, position FROM serpapi_keys ORDER BY position, rowid"
            ).fetchall()
        return [dict(row) for row in rows]

    def serpapi_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM serpapi_keys").fetchone()
        return int(row["n"]) if row else 0

    def serpapi_add(self, *, key_id: str, ciphertext: bytes, key_tail: str, label: str, status: str) -> None:
        with self._lock, self._connect() as conn:
            position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 AS n FROM serpapi_keys").fetchone()["n"]
            if status == "active":
                conn.execute("UPDATE serpapi_keys SET status='ready', updated_at=? WHERE status='active'", (_now(),))
            conn.execute(
                "INSERT INTO serpapi_keys (id, ciphertext, key_tail, label, status, position, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (key_id, ciphertext, key_tail, label[:80], status, int(position), _now()),
            )

    def serpapi_set_status(self, key_id: str, status: str) -> bool:
        """Move one key. Making it active returns the previous active key to the queue."""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT id, status FROM serpapi_keys WHERE id=?", (key_id,)).fetchone()
            if row is None:
                return False
            if status == "active":
                conn.execute(
                    "UPDATE serpapi_keys SET status='ready', updated_at=? WHERE status='active' AND id<>?",
                    (_now(), key_id),
                )
            conn.execute("UPDATE serpapi_keys SET status=?, updated_at=? WHERE id=?", (status, _now(), key_id))
            if row["status"] == "active" and status != "active":
                nxt = conn.execute(
                    "SELECT id FROM serpapi_keys WHERE status='ready' ORDER BY position, rowid LIMIT 1"
                ).fetchone()
                if nxt is not None:
                    conn.execute(
                        "UPDATE serpapi_keys SET status='active', updated_at=? WHERE id=?", (_now(), nxt["id"])
                    )
        return True

    def serpapi_plain(self, box: Any, key_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT ciphertext FROM serpapi_keys WHERE id=?", (key_id,)).fetchone()
        if row is None:
            return ""
        try:
            return box.decrypt(bytes(row["ciphertext"]))
        except Exception:
            return ""

    def serpapi_active_plain(self, box: Any) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ciphertext FROM serpapi_keys WHERE status='active' ORDER BY position, rowid LIMIT 1"
            ).fetchone()
        if row is None:
            return ""
        try:
            return box.decrypt(bytes(row["ciphertext"]))
        except Exception:
            return ""

    def serpapi_retire(self, box: Any, used: str) -> str:
        """Mark the active key exhausted when it is `used`, then return the next ready key."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, ciphertext FROM serpapi_keys WHERE status='active' ORDER BY position, rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return ""
            try:
                plain = box.decrypt(bytes(row["ciphertext"]))
            except Exception:
                plain = ""
            if plain != used:
                return plain
            conn.execute(
                "UPDATE serpapi_keys SET status='exhausted', updated_at=? WHERE id=?", (_now(), row["id"])
            )
            nxt = conn.execute(
                "SELECT id, ciphertext FROM serpapi_keys WHERE status='ready' ORDER BY position, rowid LIMIT 1"
            ).fetchone()
            if nxt is None:
                return ""
            conn.execute("UPDATE serpapi_keys SET status='active', updated_at=? WHERE id=?", (_now(), nxt["id"]))
            try:
                return box.decrypt(bytes(nxt["ciphertext"]))
            except Exception:
                return ""

    def serpapi_known(self, box: Any, plain: str) -> bool:
        with self._connect() as conn:
            rows = conn.execute("SELECT ciphertext FROM serpapi_keys").fetchall()
        for row in rows:
            try:
                if box.decrypt(bytes(row["ciphertext"])) == plain:
                    return True
            except Exception:
                continue
        return False

    def prompt_override(self, name: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT body FROM prompt_overrides WHERE name=?", (name,)).fetchone()
        if row is None or not str(row["body"]).strip():
            return None
        return str(row["body"])

    def set_prompt_override(self, name: str, body: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO prompt_overrides (name, body, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
                (name, body, _now()),
            )

    def clear_prompt_override(self, name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM prompt_overrides WHERE name=?", (name,))

    def add_prompt_suggestion(
        self,
        *,
        skill: str,
        summary: str,
        rationale: str,
        body: str,
        case_ids: list[str],
        kind: str = "skill",
        setting_value: str = "",
    ) -> dict[str, Any]:
        from app.domain.models import new_id

        row = {
            "id": new_id("suggest"),
            "status": "draft",
            "skill": skill,
            "summary": summary,
            "rationale": rationale,
            "body": body,
            "case_ids": case_ids,
            "created_at": _now(),
            "decided_at": None,
            "kind": kind,
            "setting_value": setting_value,
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO prompt_suggestions "
                "(id, status, skill, summary, rationale, body, case_ids, created_at, decided_at, kind, setting_value) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"],
                    row["status"],
                    row["skill"],
                    row["summary"],
                    row["rationale"],
                    row["body"],
                    json.dumps(case_ids),
                    row["created_at"],
                    None,
                    kind,
                    setting_value,
                ),
            )
        return row

    def list_prompt_suggestions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM prompt_suggestions WHERE status!='dismissed' ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        return [_suggestion_row(row) for row in rows]

    def get_prompt_suggestion(self, suggestion_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM prompt_suggestions WHERE id=?", (suggestion_id,)).fetchone()
        return _suggestion_row(row) if row else None

    def decide_prompt_suggestion(self, suggestion_id: str, status: str, body: str | None = None) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM prompt_suggestions WHERE id=?", (suggestion_id,)).fetchone()
            if row is None:
                return None
            kept = body if body is not None else str(row["body"])
            conn.execute(
                "UPDATE prompt_suggestions SET status=?, body=?, decided_at=? WHERE id=?",
                (status, kept, _now(), suggestion_id),
            )
            saved = conn.execute("SELECT * FROM prompt_suggestions WHERE id=?", (suggestion_id,)).fetchone()
        return _suggestion_row(saved)

    def session_review_rows(self, limit: int = 40) -> dict[str, list[dict[str, Any]]]:
        """Recent unchanged edits, and trips a traveller marked not useful, from live and finished chats."""
        with self._connect() as conn:
            edits = conn.execute(
                "SELECT t.id, t.session_id, t.content, t.meta, t.created_at, j.updated_at, "
                "(SELECT seen_at FROM desk_presence WHERE session_id=t.session_id) AS seen_at, "
                "(SELECT u.content FROM turns u WHERE u.session_id=t.session_id AND u.role='user' "
                "AND u.created_at<=t.created_at ORDER BY u.created_at DESC LIMIT 1) AS request "
                "FROM turns t JOIN journeys j ON j.id=t.session_id "
                "WHERE t.role='assistant' AND t.intent='modify' "
                "ORDER BY t.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            ratings = conn.execute(
                "SELECT r.id, r.reason_code, r.stars, r.created_at, s.session_id, j.updated_at, "
                "(SELECT seen_at FROM desk_presence WHERE session_id=s.session_id) AS seen_at, "
                "(SELECT u.content FROM turns u WHERE u.session_id=s.session_id AND u.role='user' "
                "ORDER BY u.created_at DESC LIMIT 1) AS request "
                "FROM ratings r "
                "JOIN (SELECT trip_id, MAX(created_at) AS created_at FROM itineraries GROUP BY trip_id) latest "
                "ON latest.trip_id=r.trip_id "
                "JOIN itineraries s ON s.trip_id=latest.trip_id AND s.created_at=latest.created_at "
                "JOIN journeys j ON j.id=s.session_id "
                "WHERE r.useful=0 ORDER BY r.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {
            "edits": [dict(row) for row in edits],
            "ratings": [dict(row) for row in ratings],
        }

    def escalation_policy(self) -> dict[str, Any]:
        with self._connect() as conn:
            _ensure_escalation_columns(conn)
            row = conn.execute("SELECT * FROM escalation_policy WHERE id=1").fetchone()
            examples = conn.execute(
                "SELECT id, utterance FROM escalation_examples ORDER BY created_at"
            ).fetchall()
        return _policy_row(row, examples)

    def save_escalation_policy(
        self,
        *,
        enabled: bool,
        message: str,
        frustration_at: int | None,
        examples: list[str],
        match_mode: str = "examples",
        use_jev: bool = False,
        stuck_on: bool = False,
        stuck_after: int = 3,
        words: list[str] | None = None,
        parts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from app.domain.models import new_id

        text = message.strip()[:2000] or "I'll pass this chat to a person. They will reply here."
        lines = []
        seen: set[str] = set()
        for raw in examples:
            utterance = raw.strip()[:600]
            key = utterance.lower()
            if utterance and key not in seen:
                seen.add(key)
                lines.append(utterance)
            if len(lines) >= 40:
                break
        mode = match_mode if match_mode in {"examples", "keywords"} else "examples"
        tries = min(8, max(2, int(stuck_after or 3)))
        kept_words: list[str] = []
        seen_words: set[str] = set()
        for raw_word in words or []:
            word = raw_word.strip()[:40]
            key = word.casefold()
            if word and key not in seen_words:
                seen_words.add(key)
                kept_words.append(word)
            if len(kept_words) >= 80:
                break
        if parts is None:
            stored_parts = _default_parts(
                enabled=enabled,
                message=text,
                frustration_at=frustration_at,
                match_mode=mode,
                use_jev=use_jev,
                stuck_on=stuck_on,
                stuck_after=tries,
                words=kept_words,
                examples=lines,
            )
        else:
            stored_parts = {
                "hand": _clean_part(parts.get("hand"), engine_default="examples"),
                "sentiment": _clean_part(parts.get("sentiment"), engine_default="keywords"),
                "stuck": _clean_part(parts.get("stuck"), engine_default="phrases"),
            }
            hand = stored_parts["hand"]
            enabled = hand["enabled"]
            text = hand["message"] or text
            frustration_at = hand["score"]
            mode = "keywords" if hand["engine"] == "keywords" else "examples"
            use_jev = hand["engine"] == "jev" or stored_parts["sentiment"]["engine"] == "jev" or stored_parts["stuck"]["engine"] == "jev"
            stuck_on = stored_parts["stuck"]["enabled"]
            tries = stored_parts["stuck"]["after"]
            kept_words = [row["text"] for row in hand["words"]]
            lines = [row["text"] for row in hand["examples"]]
        with self._lock, self._connect() as conn:
            _ensure_escalation_columns(conn)
            conn.execute(
                "INSERT INTO escalation_policy (id, enabled, message, frustration_at, match_mode, use_jev, stuck_on, stuck_after, words, parts, updated_at) "
                "VALUES (1,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled, message=excluded.message, "
                "frustration_at=excluded.frustration_at, match_mode=excluded.match_mode, use_jev=excluded.use_jev, "
                "stuck_on=excluded.stuck_on, stuck_after=excluded.stuck_after, words=excluded.words, parts=excluded.parts, "
                "updated_at=excluded.updated_at",
                (
                    1 if enabled else 0, text, frustration_at, mode, 1 if use_jev else 0, 1 if stuck_on else 0, tries,
                    "\n".join(kept_words), json.dumps(stored_parts), _now(),
                ),
            )
            conn.execute("DELETE FROM escalation_examples")
            for utterance in lines:
                conn.execute(
                    "INSERT INTO escalation_examples (id, utterance, created_at) VALUES (?,?,?)",
                    (new_id("esc"), utterance, _now()),
                )
        return self.escalation_policy()

    def held_agent(self, session_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT agent_name FROM desk_holds WHERE session_id=?", (session_id,)).fetchone()
        return str(row["agent_name"]) if row and row["agent_name"] else ""

    def release_chat(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            _ensure_returns(conn)
            conn.execute("DELETE FROM desk_holds WHERE session_id=?", (session_id,))
            conn.execute(
                "INSERT INTO desk_returns (session_id, released_at) VALUES (?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET released_at=excluded.released_at",
                (session_id, _now()),
            )

    def desk_note(self, session_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT note FROM desk_notes WHERE session_id=?", (session_id,)).fetchone()
        return str(row["note"]) if row else ""

    def set_desk_note(self, session_id: str, note: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO desk_notes (session_id, note, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET note=excluded.note, updated_at=excluded.updated_at",
                (session_id, note.strip()[:2000], _now()),
            )

    def recent_sessions(self, limit: int = 40) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, updated_at FROM journeys ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

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
            conn.execute(self._SEARCH_TABLE)
            conn.execute("DELETE FROM search_cache WHERE session_id=?", (session_id,))
            sessions = conn.execute("DELETE FROM journeys WHERE id=?", (session_id,)).rowcount or 0
        return {
            "messages": int(messages),
            "plans": int(plans),
            "contexts": int(contexts),
            "shares": int(shares),
            "sessions": int(sessions),
        }

    # -- paid search results kept for a session --------------------------------------------------------
    # The table is made on first use rather than by a migration step: it holds nothing that must outlive a
    # session, and a database that never plans a multi-city trip never gets it.

    _SEARCH_TABLE = (
        "CREATE TABLE IF NOT EXISTS search_cache ("
        "session_id TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, rows TEXT NOT NULL, "
        "stored_at TEXT NOT NULL, PRIMARY KEY (session_id, kind, key))"
    )

    def get_search_rows(self, session_id: str, kind: str, key: str) -> tuple[str, str] | None:
        """The rows (as JSON) and the time (ISO) a search for ``key`` was kept in this session, or None."""
        with self._lock, closing(self._connect()) as conn:
            conn.execute(self._SEARCH_TABLE)
            row = conn.execute(
                "SELECT rows, stored_at FROM search_cache WHERE session_id=? AND kind=? AND key=?",
                (session_id, kind, key),
            ).fetchone()
        return (row["rows"], row["stored_at"]) if row else None

    def put_search_rows(self, session_id: str, kind: str, key: str, rows: str, stored_at: str) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(self._SEARCH_TABLE)
            conn.execute(
                "INSERT OR REPLACE INTO search_cache (session_id, kind, key, rows, stored_at) VALUES (?,?,?,?,?)",
                (session_id, kind, key, rows, stored_at),
            )
            conn.commit()

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
        (`None`) leaves a rewritten version's summary as it is, and is the plan's own on a new row.

        A merge that left two stops on one time, or wrote the same place twice, is separated
        first, so the row can be read back.
        """
        payload = json.loads(plan.model_dump_json())
        if separate_overlaps(payload):
            plan = TripPlan.model_validate(payload)
        plan.refresh_derived()  # an edit may have changed what a link or a rank is built from
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
                    f"SELECT trip_id, version, data FROM itineraries WHERE session_id=? AND trip_id={_NEWEST_TRIP} "
                    "ORDER BY version DESC LIMIT 1",
                    (session_id, session_id),
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT trip_id, version, data FROM itineraries WHERE session_id=? AND version=? AND trip_id={_NEWEST_TRIP}",
                    (session_id, version, session_id),
                ).fetchone()
        if row is None:
            return None
        return self._readable_plan(row["data"], session_id, row["trip_id"], int(row["version"]))

    def _readable_plan(self, raw: str, session_id: str, trip_id: str | None, version: int | None) -> TripPlan:
        """Read a stored plan. An overlap or a duplicate row is separated and written back."""
        try:
            return TripPlan.model_validate_json(raw)
        except ValidationError as exc:
            if "overlap" not in str(exc):
                raise
        payload = json.loads(raw)
        notes = separate_overlaps(payload)
        plan = TripPlan.model_validate(payload)
        if notes and trip_id is not None and version is not None:
            logger.warning("repaired the stored plan for %s version %s", session_id, version)
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE itineraries SET data=? WHERE session_id=? AND trip_id=? AND version=?",
                    (plan.model_dump_json(), session_id, trip_id, version),
                )
        return plan

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
            plan = self._readable_plan(row["data"], session_id, row["trip_id"], int(row["version"]))
            out.append(
                {
                    "trip_id": row["trip_id"],
                    "version": row["version"],
                    "change_summary": row["change_summary"],
                    "created_at": row["created_at"],
                    "item_count": plan.item_count,
                    # The route for a trip of several cities ("Tokyo → Kyoto"), the one name otherwise.
                    "destination": " → ".join(segment.destination.name for segment in plan.segments)
                    if len(plan.segments) > 1
                    else plan.destination.name,
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

    # -- LLM credentials --------------------------------------------------

    @staticmethod
    def _credential(row: sqlite3.Row) -> StoredCredential:
        tested = row["last_test_ok"]
        return StoredCredential(
            id=row["id"],
            owner=row["owner"],
            provider=row["provider"],
            model=row["model"],
            base_url=row["base_url"] or "",
            ciphertext=bytes(row["ciphertext"]),
            key_tail=row["key_tail"],
            active=bool(row["active"]),
            use_for_search=bool(row["use_for_search"]),
            last_test_ok=None if tested is None else bool(tested),
            last_tested_at=row["last_tested_at"],
            last_error=row["last_error"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def add_credential(
        self,
        owner: str,
        provider: str,
        model: str,
        ciphertext: bytes,
        key_tail: str,
        use_for_search: bool,
        test_ok: bool | None,
        error: str,
        base_url: str = "",
    ) -> StoredCredential:
        """Save a key as the owner's active one; their other keys stay saved but inactive."""
        credential_id = secrets.token_hex(8)
        now = _now()
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE llm_credentials SET active=0 WHERE owner=?", (owner,))
            conn.execute(
                "INSERT INTO llm_credentials (id, owner, provider, model, base_url, ciphertext, key_tail, active, "
                "use_for_search, last_test_ok, last_tested_at, last_error, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?,?)",
                (
                    credential_id, owner, provider, model, base_url, ciphertext, key_tail, int(use_for_search),
                    None if test_ok is None else int(test_ok), None if test_ok is None else now,
                    error, now, now,
                ),
            )
            row = conn.execute("SELECT * FROM llm_credentials WHERE id=?", (credential_id,)).fetchone()
        return self._credential(row)

    def list_credentials(self, owner: str) -> list[StoredCredential]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM llm_credentials WHERE owner=? ORDER BY created_at DESC, rowid DESC", (owner,)
            ).fetchall()
        return [self._credential(row) for row in rows]

    def get_credential(self, owner: str, credential_id: str) -> StoredCredential | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM llm_credentials WHERE owner=? AND id=?", (owner, credential_id)
            ).fetchone()
        return self._credential(row) if row else None

    def update_credential(
        self,
        owner: str,
        credential_id: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        use_for_search: bool | None = None,
        test_ok: bool | None = None,
        error: str | None = None,
    ) -> StoredCredential | None:
        now = _now()
        sets = ["updated_at=?"]
        args: list[Any] = [now]
        if model is not None:
            sets.append("model=?")
            args.append(model)
        if base_url is not None:
            sets.append("base_url=?")
            args.append(base_url)
        if use_for_search is not None:
            sets.append("use_for_search=?")
            args.append(int(use_for_search))
        if test_ok is not None:
            sets.extend(["last_test_ok=?", "last_tested_at=?"])
            args.extend([int(test_ok), now])
        if error is not None:
            sets.append("last_error=?")
            args.append(error)
        with self._lock, self._connect() as conn:
            changed = conn.execute(
                f"UPDATE llm_credentials SET {', '.join(sets)} WHERE owner=? AND id=?",
                (*args, owner, credential_id),
            ).rowcount
            if not changed:
                return None
            row = conn.execute("SELECT * FROM llm_credentials WHERE id=?", (credential_id,)).fetchone()
        return self._credential(row)

    def choose_credential(self, owner: str, credential_id: str | None) -> bool:
        """Make one saved key the active one; ``None`` clears the choice (back to the default)."""
        with self._lock, self._connect() as conn:
            if credential_id is not None and not conn.execute(
                "SELECT 1 FROM llm_credentials WHERE owner=? AND id=?", (owner, credential_id)
            ).fetchone():
                return False
            conn.execute("UPDATE llm_credentials SET active=0 WHERE owner=?", (owner,))
            if credential_id is not None:
                conn.execute(
                    "UPDATE llm_credentials SET active=1 WHERE owner=? AND id=?", (owner, credential_id)
                )
        return True

    def delete_credential(self, owner: str, credential_id: str) -> bool:
        with self._lock, self._connect() as conn:
            return bool(
                conn.execute(
                    "DELETE FROM llm_credentials WHERE owner=? AND id=?", (owner, credential_id)
                ).rowcount
            )

    def active_credential(self, owner: str) -> StoredCredential | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM llm_credentials WHERE owner=? AND active=1 "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (owner,),
            ).fetchone()
        return self._credential(row) if row else None

    def search_credential(self, owner: str) -> StoredCredential | None:
        """The newest xAI key allowed for web search, whether or not it is the active key."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM llm_credentials WHERE owner=? AND provider='xai' AND use_for_search=1 "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (owner,),
            ).fetchone()
        return self._credential(row) if row else None

    def adopt_credentials(self, guest: str, account: str) -> int:
        """Move a guest's keys to an account on sign-in; the account's own active key stays active."""
        if not guest or not account or guest == account:
            return 0
        with self._lock, self._connect() as conn:
            has_active = conn.execute(
                "SELECT 1 FROM llm_credentials WHERE owner=? AND active=1", (account,)
            ).fetchone()
            if has_active:
                conn.execute("UPDATE llm_credentials SET active=0 WHERE owner=?", (guest,))
            moved = conn.execute("UPDATE llm_credentials SET owner=? WHERE owner=?", (account, guest))
            return int(moved.rowcount or 0)

    def list_latest_plans(self, limit: int = 300) -> list[TripPlan]:
        """Newest version of each saved trip, used only for aggregate trending."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM itineraries WHERE id IN ("
                "  SELECT MAX(id) FROM itineraries GROUP BY session_id, trip_id"
                ") ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        plans: list[TripPlan] = []
        for row in rows:
            try:
                plans.append(TripPlan.model_validate_json(row["data"]))
            except ValidationError as exc:
                if "overlap" not in str(exc):
                    continue
                payload = json.loads(row["data"])
                separate_overlaps(payload)
                try:
                    plans.append(TripPlan.model_validate(payload))
                except ValidationError:
                    continue
        return plans

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
