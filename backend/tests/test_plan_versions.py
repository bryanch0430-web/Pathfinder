"""The latest plan of a trip is its highest version, however and whenever the versions were saved.

CONC-G1: a no-change edit saves its plan with ``replace=True``. When it had read version 1 and
version 2 was saved meanwhile, that rewrite gave version 1 the newest timestamp, ``get_plan`` (which
sorted by timestamp first) returned the stale version 1 from then on, and every later edit failed with
``plan_conflict`` while nothing was stale.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.domain.models import EvalCase, TripPlan
from app.eval_efficiency import accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger
from app.infrastructure.store import SQLiteStore
from scripts.seed_platform_demo import DESTINATIONS, _plan

SESSION = "sess_versions"
NO_MATCH = "Remove the Eiffel Tower from the plan."
SWAP_HOTEL = "Swap the hotel for the other option."


def _kyoto_plan(version: int = 1, trip_id: str | None = None) -> TripPlan:
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    plan = _plan(random.Random(1), place, 1).model_copy(update={"session_id": SESSION, "version": version})
    return plan.model_copy(update={"trip_id": trip_id}) if trip_id else plan


def _versions(store: SQLiteStore) -> list[int]:
    return [row["version"] for row in store.list_versions(SESSION)]


def _two_versions(tmp_path: Path) -> tuple[SQLiteStore, TripPlan, TripPlan]:
    store = SQLiteStore(tmp_path / "plans.db")
    first = _kyoto_plan()
    second = first.model_copy(deep=True, update={"version": 2, "updated_at": first.updated_at + timedelta(seconds=5)})
    store.save_plan(first, "first")
    store.save_plan(second, "second")
    return store, first, second


def test_rewriting_an_older_version_in_place_does_not_make_it_the_latest(tmp_path: Path) -> None:
    store, first, second = _two_versions(tmp_path)

    store.save_plan(
        first.model_copy(update={"updated_at": first.updated_at + timedelta(seconds=30)}),
        "first, noted",
        replace=True,
    )

    assert store.get_plan(SESSION).version == 2
    assert _versions(store) == [2, 1]
    # The next ordinary edit builds on version 2 and is saved, not refused.
    store.save_plan(second.model_copy(update={"version": 3}), "third")
    assert store.get_plan(SESSION).version == 3


def test_a_rewrite_changes_the_notes_of_the_version_and_keeps_its_place_in_the_order(tmp_path: Path) -> None:
    store, first, second = _two_versions(tmp_path)
    created = {row["version"]: row["created_at"] for row in store.list_versions(SESSION)}

    store.save_plan(
        second.model_copy(update={"updated_at": second.updated_at + timedelta(seconds=30)}),
        "second, noted",
        replace=True,
    )

    rows = {row["version"]: row for row in store.list_versions(SESSION)}
    assert rows[2]["change_summary"] == "second, noted"
    assert {version: row["created_at"] for version, row in rows.items()} == created
    assert _versions(store) == [2, 1]


def test_a_version_that_was_rewritten_before_is_still_found_by_its_number(tmp_path: Path) -> None:
    store, first, _ = _two_versions(tmp_path)
    store.save_plan(
        first.model_copy(update={"updated_at": first.updated_at + timedelta(seconds=30)}), "first, noted", replace=True
    )

    assert store.get_plan(SESSION, 1).version == 1
    assert store.get_plan(SESSION, 2).version == 2


def test_replace_still_stores_a_version_that_is_not_there_yet(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "plans.db")

    store.save_plan(_kyoto_plan(), "first", replace=True)

    assert _versions(store) == [1]


def test_a_file_that_already_has_an_older_version_on_top_still_answers_with_the_highest(tmp_path: Path) -> None:
    """Databases written before the fix can hold the shadowed pair; the read decides by version."""
    store, first, second = _two_versions(tmp_path)
    later = (first.updated_at + timedelta(seconds=30)).isoformat()
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE itineraries SET created_at=? WHERE version=1", (later,))

    assert store.get_plan(SESSION).version == 2
    assert _versions(store) == [2, 1]


def test_a_new_trip_in_the_same_session_is_the_latest_even_with_a_lower_version(tmp_path: Path) -> None:
    """Planning again creates a new trip that starts at version 1; it is the one the session shows."""
    store, _, second = _two_versions(tmp_path)
    again = _kyoto_plan(trip_id="trip_planned_again").model_copy(
        update={"updated_at": second.updated_at + timedelta(minutes=5)}
    )
    store.save_plan(again, "planned again")

    latest = store.get_plan(SESSION)

    assert (latest.trip_id, latest.version) == ("trip_planned_again", 1)


@pytest.fixture()
def quick_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})


def test_a_no_change_edit_that_raced_a_newer_version_leaves_the_newer_version_latest(
    quick_settings: Settings,
) -> None:
    """The edit read version 1, version 2 was saved meanwhile, and the edit changed nothing."""
    ledger = RunLedger()
    try:
        rig = build_rig(quick_settings, "Tokyo", ledger)
        store = ledger.store
        session = store.create_session("guest-versions")
        context = case_context(EvalCase(id="v", dataset="design", input="", destination="Tokyo"), session.id)
        store.save_context(context)
        first = asyncio.run(accepted_plan(quick_settings, context))
        store.save_plan(first, "Accepted plan")
        rig.llm.route = {"intent": "modify", "confidence": 0.95, "affected_parts": ["hotel"]}
        original = rig.modify_path.run

        async def save_the_next_version_first(**kwargs):
            # Saved just after version 1, long before this edit finishes and stamps its own rewrite.
            second = first.model_copy(
                deep=True, update={"version": 2, "updated_at": first.updated_at + timedelta(microseconds=1)}
            )
            store.save_plan(second, "Saved by someone else")
            return await original(**kwargs)

        rig.modify_path.run = save_the_next_version_first

        async def two_turns() -> tuple[list, list]:
            raced = [event async for event in rig.turns.handle_turn(session_id=session.id, message=NO_MATCH)]
            rig.modify_path.run = original
            follow_up = [event async for event in rig.turns.handle_turn(session_id=session.id, message=SWAP_HOTEL)]
            return raced, follow_up

        raced, follow_up = asyncio.run(two_turns())
        latest = store.get_plan(session.id)
    finally:
        ledger.close()

    assert not [event.data for event in raced if event.event == "error"]
    assert not [event.data for event in follow_up if event.event == "error"]
    assert latest.version == 3
