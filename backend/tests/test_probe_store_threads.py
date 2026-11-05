"""The scratch store of the injection probes does not share one connection between threads.

A probe turn writes the session's preferences from a worker thread (`asyncio.to_thread`) while the turn goes on.
The scratch store kept one connection for every thread, so the two transactions committed each other's: the
evaluation printed `background preference write failed: cannot commit - no transaction is active` now and then
(and, on the audited branch, `Invalid JSON: EOF` from a row read back empty). One connection per thread, kept open.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from app.domain.models import PreferenceProfile
from app.injection_probes import ProbeStore


def _connection_seen_by_a_thread(store: ProbeStore, autocommit: bool = False) -> Any:
    seen: list[Any] = []
    worker = threading.Thread(target=lambda: seen.append(store._connect(autocommit)))
    worker.start()
    worker.join()
    return seen[0]


def test_each_thread_has_its_own_kept_open_connection(tmp_path: Path) -> None:
    store = ProbeStore(tmp_path / "probes.db")
    try:
        mine = store._connect()

        assert store._connect() is mine, "the same thread keeps its connection open"
        assert _connection_seen_by_a_thread(store) is not mine, "another thread does not share it"
        assert store._connect(True) is not mine, "autocommit is still a mode of its own"
    finally:
        store.close()


def test_closing_the_store_closes_the_connections_of_every_thread(tmp_path: Path) -> None:
    store = ProbeStore(tmp_path / "probes.db")
    mine = store._connect()
    other = _connection_seen_by_a_thread(store)

    store.close()

    for kept in (mine, other):
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            kept.execute("SELECT 1")


def test_reads_and_writes_from_two_threads_do_not_break_each_other(tmp_path: Path) -> None:
    store = ProbeStore(tmp_path / "probes.db")
    failures: list[BaseException] = []

    def background_writes() -> None:
        try:
            for index in range(150):
                store.upsert_preferences(PreferenceProfile(client_token="owner", preferences=[f"quiet {index}"]))
        except BaseException as error:  # noqa: BLE001 - the test reports whatever the thread hit
            failures.append(error)

    worker = threading.Thread(target=background_writes)
    worker.start()
    try:
        for _ in range(150):
            store.get_preferences("owner")
    except BaseException as error:  # noqa: BLE001
        failures.append(error)
    worker.join()
    store.close()

    assert failures == []
