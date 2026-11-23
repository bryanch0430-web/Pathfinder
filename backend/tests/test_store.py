from __future__ import annotations

import asyncio
import sqlite3

from app.core.store import Store


def run(coro):
    return asyncio.run(coro)


def test_session_context_and_messages(tmp_path):
    store = Store(tmp_path / "test.db")
    store.init()

    async def scenario():
        session = await store.create_session(client_token="tok")
        sid = session["id"]
        await store.save_context(sid, {"session_id": sid, "destination": "Kyoto"})
        await store.add_message(sid, {"id": "m1", "role": "user", "content": "hi", "created_at": "2026-01-01T00:00:00Z"})
        await store.add_message(sid, {"id": "m2", "role": "assistant", "content": "hello", "created_at": "2026-01-01T00:00:01Z"})
        context = await store.load_context(sid)
        messages = await store.list_messages(sid)
        latest = await store.recent_messages(sid, 1)
        return session, context, messages, latest

    session, context, messages, latest = run(scenario())
    assert session["id"]
    assert context["destination"] == "Kyoto"
    assert [message["id"] for message in messages] == ["m1", "m2"]
    assert latest[0]["content"] == "hello"


def test_plan_versions_are_append_only(tmp_path):
    store = Store(tmp_path / "test.db")
    store.init()

    async def scenario():
        session = await store.create_session()
        sid = session["id"]
        for version in (1, 2, 3):
            await store.save_plan(
                {"session_id": sid, "trip_id": "t", "version": version, "days": [], "destination": {"name": "Kyoto"}}
            )
        latest = await store.load_plan(sid)
        old = await store.load_plan(sid, 1)
        versions = await store.list_versions(sid)
        return latest, old, versions

    latest, old, versions = run(scenario())
    assert latest["version"] == 3
    assert old["version"] == 1
    assert [row["version"] for row in versions] == [3, 2, 1]


def test_delete_session_cascades(tmp_path):
    store = Store(tmp_path / "test.db")
    store.init()

    async def scenario():
        session = await store.create_session()
        sid = session["id"]
        await store.save_context(sid, {"session_id": sid})
        await store.save_plan({"session_id": sid, "trip_id": "t", "version": 1, "days": []})
        await store.add_message(sid, {"id": "m1", "role": "user", "content": "hi", "created_at": "2026-01-01T00:00:00Z"})
        counts = await store.delete_session(sid)
        return counts, await store.get_session(sid), await store.load_context(sid), await store.load_plan(sid)

    counts, session, context, plan = run(scenario())
    assert session is None and context is None and plan is None
    assert counts["messages"] == 1 and counts["plans"] == 1


def test_seat_is_exclusive(tmp_path):
    store = Store(tmp_path / "test.db")
    store.init()

    async def scenario():
        session = await store.create_session()
        sid = session["id"]
        first = await store.claim_seat(sid, "turn-1")
        second = await store.claim_seat(sid, "turn-2")
        holder = await store.seat_holder(sid)
        await store.release_seat(sid, "turn-1")
        third = await store.claim_seat(sid, "turn-3")
        return first, second, holder, third

    first, second, holder, third = run(scenario())
    assert first is True
    assert second is False
    assert holder == "turn-1"
    assert third is True


def test_store_init_keeps_one_saved_trip_per_session(tmp_path):
    path = tmp_path / "test.db"
    store = Store(path)
    store.init()
    db = sqlite3.connect(path)
    for index in range(3):
        db.execute(
            "INSERT INTO wishlist (id, session_id, identity, destination, summary, stars, body, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"w{index}", "same-session", "guest:same", "Kyoto", "A trip", 5, "{}", f"2026-01-0{index + 1}T00:00:00Z"),
        )
    db.commit()
    db.close()

    # A restart runs the dedupe migration; only the newest row remains.
    store.init()
    db = sqlite3.connect(path)
    rows = db.execute("SELECT id FROM wishlist WHERE session_id='same-session' AND identity='guest:same'").fetchall()
    db.close()
    assert len(rows) == 1
    assert rows[0][0] == "w2"
