"""A turn belongs to its trip, not to the browser connection that started it."""

from __future__ import annotations

import asyncio
from collections import namedtuple

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import turn as turn_module
from app.api.app import create_app
from app.config import Settings
from app.domain.models import ChatRequest

Event = namedtuple("Event", "event data")


def _request(app, path: str = "/api/turn/stream") -> Request:
    scope = {
        "type": "http",
        "app": app,
        "method": "POST",
        "path": path,
        "headers": [(b"x-client-token", b"seat-owner")],
        "client": ("203.0.113.9", 5000),
        "query_string": b"",
    }
    return Request(scope)


def _app(tmp_path):
    return create_app(
        Settings(
            database_path=str(tmp_path / "seat.db"),
            sandbox=True,
            local_admin=False,
            admin_token="test-admin",
            secret_key="test-secret",
        )
    )


def _slow_turn(finished: list):
    async def run_turn(deps, payload, session_id, user_message):
        yield Event("progress", {"phase": "understand"})
        await asyncio.sleep(0.6)
        finished.append(session_id)
        yield Event("done", {})

    return run_turn


async def _open(app, store, finished):
    session = await store.create_session(client_token="seat-owner", user_id="")
    payload = ChatRequest(session_id=session["id"], message="hello", client_token="seat-owner")
    response = await turn_module.chat_stream(payload, _request(app))
    return session["id"], response


def test_a_reload_does_not_stop_the_turn_or_leak_its_seat(tmp_path, monkeypatch):
    finished: list = []
    monkeypatch.setattr(turn_module, "run_turn", _slow_turn(finished))
    app = _app(tmp_path)
    with TestClient(app):
        store = app.state.container.store

        async def scenario():
            sid, response = await _open(app, store, finished)

            async def listen():
                async for _ in response.body_iterator:
                    pass

            listener = asyncio.ensure_future(listen())
            await asyncio.sleep(0.2)  # the turn is mid-way
            listener.cancel()  # the page was reloaded
            try:
                await listener
            except asyncio.CancelledError:
                pass
            assert await store.seat_holder(sid) is not None  # still working
            await asyncio.sleep(1.2)
            return sid, await store.seat_holder(sid)

        sid, seat = asyncio.run(scenario())
    assert finished == [sid]  # the turn ran to its end although nobody listened
    assert seat is None  # and gave its seat back


def test_stop_ends_the_running_turn(tmp_path, monkeypatch):
    finished: list = []
    monkeypatch.setattr(turn_module, "run_turn", _slow_turn(finished))
    app = _app(tmp_path)
    with TestClient(app):
        store = app.state.container.store

        async def scenario():
            sid, response = await _open(app, store, finished)
            listener = asyncio.ensure_future(_drain(response))
            await asyncio.sleep(0.2)
            result = await turn_module.cancel_turn(sid, _request(app, f"/api/turn/{sid}/cancel"))
            await listener
            await asyncio.sleep(0.2)
            return result, await store.seat_holder(sid)

        result, seat = asyncio.run(scenario())
    assert result == {"cancelled": True}
    assert finished == []
    assert seat is None


async def _drain(response):
    async for _ in response.body_iterator:
        pass
