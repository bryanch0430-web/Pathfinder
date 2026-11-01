from __future__ import annotations

import random
from pathlib import Path

import pytest

from app.application import trending_rag
from app.config import Settings
from app.infrastructure.store import SQLiteStore
from scripts.seed_platform_demo import DESTINATIONS, _plan, _saved_trip


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_path=tmp_path / "trending.db",
        trace_dir=tmp_path / "traces",
        eval_dir=tmp_path / "eval",
    )
    return SQLiteStore(settings.database_path)


def _seed(store: SQLiteStore, destination_name: str, count: int, offset: int = 0) -> None:
    destination = next(item for item in DESTINATIONS if item["name"] == destination_name)
    rng = random.Random(offset + len(destination_name))
    for index in range(count):
        store.save_saved_trip(_saved_trip(rng, _plan(rng, destination, offset + index)))


def test_hybrid_retrieval_ranks_the_matching_destination(store: SQLiteStore) -> None:
    _seed(store, "Kyoto", 10)
    _seed(store, "Tokyo", 10, offset=100)
    trending_rag.invalidate()

    report = trending_rag.trending_report(store, "kyoto temples", 6)

    assert report["trace"]["mode"] == "hybrid"
    assert report["items"][0]["destination"] == "Kyoto"


def test_preferences_only_echo_the_requesters_own_tags(store: SQLiteStore) -> None:
    _seed(store, "Kyoto", 10)
    trending_rag.invalidate()

    report = trending_rag.trending_report(store, "", 6, preferences=["food"])

    assert report["items"]
    matches = [tag for item in report["items"] for tag in item.get("match", [])]
    assert matches
    assert set(matches) <= {"food"}


def test_empty_store_returns_no_cards(store: SQLiteStore) -> None:
    trending_rag.invalidate()

    report = trending_rag.trending_report(store, "", 6)

    assert report["items"] == []
    assert "marked useful" in report["trace"]["note"].lower()
