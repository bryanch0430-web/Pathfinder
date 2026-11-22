"""SQLite persistence for Pathfinder v5.

One file holds everything a trip needs: the session, its context, the chat, every
version of the plan, share links, traveller memory, evaluation runs and the small
settings the operator can change from the UI.

Writes are serialised through a single lock and run in a worker thread, so the
event loop never waits on disk.  WAL lets the evaluation runner read while a turn
is writing.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    client_token TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    held INTEGER NOT NULL DEFAULT 0,
    agent_name TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contexts (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    intent TEXT,
    confidence REAL,
    created_at TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS plans (
    session_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    change_summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    destination TEXT NOT NULL DEFAULT '',
    days INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, version)
);

CREATE TABLE IF NOT EXISTS shares (
    slug TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    identity TEXT PRIMARY KEY,
    preferences TEXT NOT NULL DEFAULT '[]',
    hard_constraints TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wishlist (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    identity TEXT NOT NULL DEFAULT '',
    destination TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    stars INTEGER,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL DEFAULT '1',
    experiment_tag TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    useful INTEGER NOT NULL,
    stars INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seats (
    session_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    started_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._ready = False

    # -- plumbing -----------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA)
        self._ready = True

    async def _run(self, fn, *args, **kwargs):
        if not self._ready:
            self.init()
        return await asyncio.to_thread(self._call, fn, *args, **kwargs)

    def _call(self, fn, *args, **kwargs):
        with self._lock:
            conn = self._connect()
            try:
                return fn(conn, *args, **kwargs)
            finally:
                conn.close()

    async def close(self) -> None:
        """Nothing is held open between calls; kept for the app lifecycle."""
        return None

    # -- sessions -----------------------------------------------------

    async def create_session(self, *, client_token: str = "", user_id: str = "", session_id: str | None = None) -> dict[str, Any]:
        sid = session_id or uuid.uuid4().hex
        now = _now()

        def op(conn):
            conn.execute(
                "INSERT INTO sessions (id, client_token, user_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                (sid, client_token, user_id, now, now),
            )
            return {"id": sid, "created_at": now, "client_token": client_token, "user_id": user_id}

        return await self._run(op)

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        def op(conn):
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def touch_session(self, session_id: str, *, planner_note: str | None = None) -> None:
        def op(conn):
            if planner_note is None:
                conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (_now(), session_id))
            else:
                conn.execute(
                    "UPDATE sessions SET updated_at=?, note=? WHERE id=?",
                    (_now(), planner_note, session_id),
                )

        await self._run(op)

    async def set_held(self, session_id: str, held: bool, agent_name: str = "") -> None:
        def op(conn):
            conn.execute(
                "UPDATE sessions SET held=?, agent_name=? WHERE id=?",
                (1 if held else 0, agent_name, session_id),
            )

        await self._run(op)

    async def delete_session(self, session_id: str) -> dict[str, int]:
        tables = ("messages", "plans", "shares", "contexts", "wishlist", "seats")

        def op(conn):
            counts = {}
            for table in tables:
                cur = conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
                counts[table] = cur.rowcount
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            counts["session"] = 1
            return counts

        return await self._run(op)

    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        def op(conn):
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    # -- context ------------------------------------------------------

    async def save_context(self, session_id: str, body: dict[str, Any]) -> None:
        def op(conn):
            conn.execute(
                "INSERT INTO contexts (session_id, body) VALUES (?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET body=excluded.body",
                (session_id, json.dumps(body, ensure_ascii=False)),
            )

        await self._run(op)

    async def load_context(self, session_id: str) -> dict[str, Any] | None:
        def op(conn):
            row = conn.execute("SELECT body FROM contexts WHERE session_id=?", (session_id,)).fetchone()
            return json.loads(row["body"]) if row else None

        return await self._run(op)

    # -- messages -----------------------------------------------------

    async def add_message(self, session_id: str, message: dict[str, Any]) -> dict[str, Any]:
        def op(conn):
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, intent, confidence, created_at, meta) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    message["id"],
                    session_id,
                    message.get("role", "assistant"),
                    message.get("content", ""),
                    message.get("intent"),
                    message.get("confidence"),
                    message.get("created_at") or _now(),
                    json.dumps(message.get("meta") or {}, ensure_ascii=False),
                ),
            )
            return message

        return await self._run(op)

    async def update_message(self, message_id: str, *, content: str | None = None, meta: dict[str, Any] | None = None) -> None:
        def op(conn):
            if content is not None:
                conn.execute("UPDATE messages SET content=? WHERE id=?", (content, message_id))
            if meta is not None:
                conn.execute("UPDATE messages SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), message_id))

        await self._run(op)

    async def list_messages(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        def op(conn):
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY seq ASC LIMIT ?", (session_id, limit)
            ).fetchall()
            return [_message(row) for row in rows]

        return await self._run(op)

    async def recent_messages(self, session_id: str, limit: int = 8) -> list[dict[str, Any]]:
        def op(conn):
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY seq DESC LIMIT ?", (session_id, limit)
            ).fetchall()
            return [_message(row) for row in reversed(rows)]

        return await self._run(op)

    async def get_message(self, message_id: str) -> dict[str, Any] | None:
        def op(conn):
            row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            return _message(row) if row else None

        return await self._run(op)

    async def clear_messages(self, session_id: str) -> int:
        def op(conn):
            return conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,)).rowcount

        return await self._run(op)

    # -- plans --------------------------------------------------------

    async def save_plan(self, plan: dict[str, Any]) -> None:
        def op(conn):
            conn.execute(
                "INSERT INTO plans (session_id, version, body, change_summary, created_at, item_count, destination, days) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(session_id, version) DO UPDATE SET "
                "body=excluded.body, change_summary=excluded.change_summary",
                (
                    plan["session_id"],
                    plan["version"],
                    json.dumps(plan, ensure_ascii=False),
                    plan.get("change_summary", ""),
                    plan.get("created_at") or _now(),
                    sum(len(day.get("items", [])) for day in plan.get("days", [])),
                    (plan.get("destination") or {}).get("name", ""),
                    len(plan.get("days", [])),
                ),
            )

        await self._run(op)

    async def load_plan(self, session_id: str, version: int | None = None) -> dict[str, Any] | None:
        def op(conn):
            if version is None:
                row = conn.execute(
                    "SELECT body FROM plans WHERE session_id=? ORDER BY version DESC LIMIT 1", (session_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT body FROM plans WHERE session_id=? AND version=?", (session_id, version)
                ).fetchone()
            return json.loads(row["body"]) if row else None

        return await self._run(op)

    async def list_versions(self, session_id: str) -> list[dict[str, Any]]:
        def op(conn):
            rows = conn.execute(
                "SELECT version, change_summary, created_at, item_count, destination, days "
                "FROM plans WHERE session_id=? ORDER BY version DESC",
                (session_id,),
            ).fetchall()
            trip_id = ""
            if rows:
                latest = conn.execute(
                    "SELECT body FROM plans WHERE session_id=? ORDER BY version DESC LIMIT 1", (session_id,)
                ).fetchone()
                if latest:
                    trip_id = json.loads(latest["body"]).get("trip_id", "")
            return [
                {
                    "trip_id": trip_id,
                    "version": row["version"],
                    "change_summary": row["change_summary"],
                    "created_at": row["created_at"],
                    "item_count": row["item_count"],
                    "destination": row["destination"],
                    "days": row["days"],
                }
                for row in rows
            ]

        return await self._run(op)

    # -- shares -------------------------------------------------------

    async def save_share(self, slug: str, session_id: str, expires_at: str) -> None:
        def op(conn):
            conn.execute(
                "INSERT OR REPLACE INTO shares (slug, session_id, expires_at, created_at) VALUES (?,?,?,?)",
                (slug, session_id, expires_at, _now()),
            )

        await self._run(op)

    async def get_share(self, slug: str) -> dict[str, Any] | None:
        def op(conn):
            row = conn.execute("SELECT * FROM shares WHERE slug=?", (slug,)).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def revoke_shares(self, session_id: str) -> int:
        def op(conn):
            return conn.execute("DELETE FROM shares WHERE session_id=?", (session_id,)).rowcount

        return await self._run(op)

    # -- profiles -----------------------------------------------------

    async def get_profile(self, identity: str) -> dict[str, Any] | None:
        def op(conn):
            row = conn.execute("SELECT * FROM profiles WHERE identity=?", (identity,)).fetchone()
            if not row:
                return None
            return {
                "preferences": json.loads(row["preferences"]),
                "hard_constraints": json.loads(row["hard_constraints"]),
                "updated_at": row["updated_at"],
            }

        return await self._run(op)

    async def save_profile(self, identity: str, preferences: Iterable[str], hard_constraints: Iterable[str]) -> dict[str, Any]:
        def op(conn):
            now = _now()
            conn.execute(
                "INSERT INTO profiles (identity, preferences, hard_constraints, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(identity) DO UPDATE SET preferences=excluded.preferences, "
                "hard_constraints=excluded.hard_constraints, updated_at=excluded.updated_at",
                (identity, json.dumps(list(preferences)), json.dumps(list(hard_constraints)), now),
            )
            return {"preferences": list(preferences), "hard_constraints": list(hard_constraints), "updated_at": now}

        return await self._run(op)

    async def clear_profile(self, identity: str) -> None:
        def op(conn):
            conn.execute("DELETE FROM profiles WHERE identity=?", (identity,))

        await self._run(op)

    # -- wishlist -----------------------------------------------------

    async def save_wishlist(self, entry: dict[str, Any]) -> None:
        def op(conn):
            conn.execute(
                "INSERT OR REPLACE INTO wishlist (id, session_id, identity, destination, summary, stars, body, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    entry["id"],
                    entry["session_id"],
                    entry.get("identity", ""),
                    entry.get("destination", ""),
                    entry.get("summary", ""),
                    entry.get("stars"),
                    json.dumps(entry["body"], ensure_ascii=False),
                    entry.get("created_at") or _now(),
                ),
            )

        await self._run(op)

    async def list_wishlist(self, identity: str) -> list[dict[str, Any]]:
        def op(conn):
            rows = conn.execute(
                "SELECT * FROM wishlist WHERE identity=? ORDER BY created_at DESC", (identity,)
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(op)

    async def get_wishlist(self, entry_id: str) -> dict[str, Any] | None:
        def op(conn):
            row = conn.execute("SELECT * FROM wishlist WHERE id=?", (entry_id,)).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    # -- evaluation ---------------------------------------------------

    async def save_eval_run(self, run: dict[str, Any]) -> None:
        def op(conn):
            conn.execute(
                "INSERT OR REPLACE INTO eval_runs (id, dataset_id, dataset_version, experiment_tag, status, body, created_at, finished_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    run["id"],
                    run["dataset_id"],
                    run.get("dataset_version", "1"),
                    run.get("experiment_tag", ""),
                    run.get("status", "running"),
                    json.dumps(run, ensure_ascii=False),
                    run.get("created_at") or _now(),
                    run.get("finished_at"),
                ),
            )

        await self._run(op)

    async def list_eval_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        def op(conn):
            rows = conn.execute(
                "SELECT body FROM eval_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [json.loads(row["body"]) for row in rows]

        return await self._run(op)

    async def get_eval_run(self, run_id: str) -> dict[str, Any] | None:
        def op(conn):
            row = conn.execute("SELECT body FROM eval_runs WHERE id=?", (run_id,)).fetchone()
            return json.loads(row["body"]) if row else None

        return await self._run(op)

    # -- users --------------------------------------------------------

    async def create_user(self, email: str, password_hash: str, display_name: str) -> dict[str, Any]:
        user = {"id": uuid.uuid4().hex, "email": email.lower(), "display_name": display_name, "created_at": _now()}

        def op(conn):
            conn.execute(
                "INSERT INTO users (id, email, password_hash, display_name, created_at) VALUES (?,?,?,?,?)",
                (user["id"], user["email"], password_hash, display_name, user["created_at"]),
            )
            return user

        try:
            return await self._run(op)
        except sqlite3.IntegrityError as exc:
            raise ValueError("email_taken") from exc

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        def op(conn):
            row = conn.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        def op(conn):
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def claim_session(self, session_id: str, user_id: str) -> bool:
        def op(conn):
            cur = conn.execute("UPDATE sessions SET user_id=? WHERE id=?", (user_id, session_id))
            return cur.rowcount > 0

        return await self._run(op)

    # -- feedback -----------------------------------------------------

    async def save_feedback(self, session_id: str, useful: bool, stars: int | None) -> str:
        fid = uuid.uuid4().hex

        def op(conn):
            conn.execute(
                "INSERT INTO feedback (id, session_id, useful, stars, created_at) VALUES (?,?,?,?,?)",
                (fid, session_id, 1 if useful else 0, stars, _now()),
            )
            return fid

        return await self._run(op)

    # -- kv -----------------------------------------------------------

    async def get_kv(self, key: str) -> Any | None:
        def op(conn):
            row = conn.execute("SELECT body FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row["body"]) if row else None

        return await self._run(op)

    async def set_kv(self, key: str, value: Any) -> None:
        def op(conn):
            conn.execute(
                "INSERT INTO kv (key, body) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
                (key, json.dumps(value, ensure_ascii=False)),
            )

        await self._run(op)

    async def delete_kv(self, key: str) -> None:
        def op(conn):
            conn.execute("DELETE FROM kv WHERE key=?", (key,))

        await self._run(op)

    # -- turn seat (one planning turn per trip) ----------------------

    async def claim_seat(self, session_id: str, turn_id: str) -> bool:
        def op(conn):
            try:
                conn.execute(
                    "INSERT INTO seats (session_id, turn_id, started_at) VALUES (?,?,?)",
                    (session_id, turn_id, _now()),
                )
                return True
            except sqlite3.IntegrityError:
                return False

        return await self._run(op)

    async def release_seat(self, session_id: str, turn_id: str) -> None:
        def op(conn):
            conn.execute("DELETE FROM seats WHERE session_id=? AND turn_id=?", (session_id, turn_id))

        await self._run(op)

    async def seat_holder(self, session_id: str) -> str | None:
        def op(conn):
            row = conn.execute("SELECT turn_id FROM seats WHERE session_id=?", (session_id,)).fetchone()
            return row["turn_id"] if row else None

        return await self._run(op)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _message(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "intent": row["intent"],
        "confidence": row["confidence"],
        "created_at": row["created_at"],
        "meta": json.loads(row["meta"] or "{}"),
    }
