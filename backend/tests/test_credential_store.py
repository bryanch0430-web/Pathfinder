"""Encrypted per-owner LLM credential storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.domain.credentials import SERVER_OWNER, StoredCredential
from app.infrastructure.secret_box import SecretBox
from app.infrastructure.store import MIGRATIONS, SQLiteStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "creds.db")


def _add(store: SQLiteStore, owner: str = "guest-a", provider: str = "openai", model: str = "m1",
         search: bool = True, tail: str = "sk-…a1b2") -> StoredCredential:
    return store.add_credential(owner, provider, model, b"cipher-" + provider.encode(), tail, search, True, "")


def test_migration_creates_table(store: SQLiteStore) -> None:
    assert store.schema_version == len(MIGRATIONS) == 4
    with sqlite3.connect(store.path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert "llm_credentials" in names
    assert "idx_llm_credentials_owner" in names


def test_add_makes_only_one_active(store: SQLiteStore) -> None:
    first = _add(store)
    second = _add(store, provider="xai")
    rows = {row.id: row for row in store.list_credentials("guest-a")}
    assert rows[second.id].active and not rows[first.id].active
    assert [row.id for row in store.list_credentials("guest-a")][0] == second.id
    assert store.active_credential("guest-a").id == second.id
    assert second.last_test_ok is True and second.last_tested_at


def test_owner_isolation(store: SQLiteStore) -> None:
    mine = _add(store, owner="guest-a")
    _add(store, owner="guest-b")
    assert store.get_credential("guest-b", mine.id) is None
    assert store.update_credential("guest-b", mine.id, model="hacked") is None
    assert store.choose_credential("guest-b", mine.id) is False
    assert store.delete_credential("guest-b", mine.id) is False
    after = store.get_credential("guest-a", mine.id)
    assert after is not None and after.model == "m1" and after.active


def test_delete_active_key_reverts_to_default(store: SQLiteStore) -> None:
    row = _add(store)
    assert store.delete_credential("guest-a", row.id) is True
    assert store.active_credential("guest-a") is None


def test_choose_none_clears_active(store: SQLiteStore) -> None:
    first = _add(store)
    _add(store, provider="xai")
    assert store.choose_credential("guest-a", None) is True
    assert store.active_credential("guest-a") is None
    assert store.choose_credential("guest-a", first.id) is True
    assert store.active_credential("guest-a").id == first.id


def test_update_sets_fields_and_test_time(store: SQLiteStore) -> None:
    row = store.add_credential("guest-a", "xai", "g", b"c", "t", True, None, "")
    assert row.last_test_ok is None and row.last_tested_at is None
    changed = store.update_credential("guest-a", row.id, model="g2", use_for_search=False, test_ok=False, error="bad")
    assert changed.model == "g2" and changed.use_for_search is False
    assert changed.last_test_ok is False and changed.last_error == "bad" and changed.last_tested_at
    assert changed.updated_at >= row.updated_at


def test_search_credential_prefers_newest_xai_with_flag(store: SQLiteStore) -> None:
    old = _add(store, provider="xai")
    _add(store, provider="xai", search=False)
    _add(store, provider="openai")
    assert store.search_credential("guest-a").id == old.id
    newer = _add(store, provider="xai")
    assert store.search_credential("guest-a").id == newer.id
    assert store.search_credential("nobody") is None


def test_adopt_moves_and_keeps_account_choice(store: SQLiteStore) -> None:
    account = _add(store, owner="user:7", provider="anthropic")
    guest = _add(store, owner="guest-a", provider="xai")
    assert store.adopt_credentials("guest-a", "user:7") == 1
    assert store.list_credentials("guest-a") == []
    assert store.active_credential("user:7").id == account.id
    assert not store.get_credential("user:7", guest.id).active


def test_adopt_keeps_active_when_account_has_none(store: SQLiteStore) -> None:
    guest = _add(store, owner="guest-a")
    assert store.adopt_credentials("guest-a", "user:8") == 1
    assert store.active_credential("user:8").id == guest.id


def test_server_owner_constant() -> None:
    assert SERVER_OWNER == "server"


def test_ciphertext_only_column_with_secret(store: SQLiteStore) -> None:
    box = SecretBox(Settings())
    store.add_credential("guest-a", "openai", "m", box.encrypt("sk-LEAKCHECK999"), "sk-…K999", True, True, "")
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute("SELECT * FROM llm_credentials").fetchall()
    blob = " ".join(str(v) for row in rows for v in row)
    assert "sk-LEAKCHECK999" not in blob
    stored = store.list_credentials("guest-a")[0]
    assert box.decrypt(stored.ciphertext) == "sk-LEAKCHECK999"
