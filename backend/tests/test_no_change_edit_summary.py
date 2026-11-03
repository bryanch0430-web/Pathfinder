"""An edit that changed nothing leaves the summary of the version it rewrote alone.

The edit saves its notes into the version in place (`replace=True`). It used to write the reason it changed
nothing into that version's summary as well, so the list of revisions showed "No unlocked stop matched that
removal" for the plan's first version, and a later swap of the hotel no longer said what it was.
"""

from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path

from app.config import Settings
from app.domain.models import TripPlan
from app.infrastructure.store import SQLiteStore
from scripts.seed_platform_demo import DESTINATIONS, _plan
from tests.test_modify_dates import reply_of, run_trip

SESSION = "sess_summary"
NO_MATCH = "Remove the Eiffel Tower from the plan."
SWAP_HOTEL = "Swap the hotel for the other option."


def _summaries(store, session_id: str) -> list[tuple[int, str]]:
    return [(row["version"], row["change_summary"]) for row in store.list_versions(session_id)]


def test_a_no_change_edit_leaves_the_summary_of_the_first_version(settings: Settings) -> None:
    async def scenario(trip) -> None:
        await trip.turn(NO_MATCH)

        assert _summaries(trip.store, trip.session_id) == [(1, "Accepted plan")]
        assert trip.store.get_plan(trip.session_id).version == 1

    run_trip(settings, scenario)


def test_a_no_change_edit_after_a_swap_leaves_what_the_swap_was(settings: Settings) -> None:
    async def scenario(trip) -> None:
        await trip.turn(SWAP_HOTEL)
        swapped = _summaries(trip.store, trip.session_id)
        assert [version for version, _ in swapped] == [2, 1]

        await trip.turn(NO_MATCH)
        await trip.turn(NO_MATCH)

        assert _summaries(trip.store, trip.session_id) == swapped

    run_trip(settings, scenario)


def test_the_reason_an_edit_changed_nothing_is_still_the_reply(settings: Settings) -> None:
    async def scenario(trip) -> None:
        events = await trip.turn(NO_MATCH)

        assert reply_of(events) == "No unlocked stop matched that removal, so the itinerary stayed as it was."

    run_trip(settings, scenario)


# -- the store -------------------------------------------------------------------------------------------


def _plan_of(version: int = 1) -> TripPlan:
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    return _plan(random.Random(1), place, 1).model_copy(update={"session_id": SESSION, "version": version})


def test_a_rewrite_with_no_summary_changes_the_data_and_keeps_the_summary(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "plans.db")
    first = _plan_of()
    store.save_plan(first, "Initial plan")
    noted = first.model_copy(deep=True, update={"change_summary": "No unlocked stop matched that removal."})
    noted.data_quality.notes = ["a note the rewrite adds"]

    store.save_plan(noted, None, replace=True)

    assert _summaries(store, SESSION) == [(1, "Initial plan")]
    assert store.get_plan(SESSION).data_quality.notes == ["a note the rewrite adds"]


def test_a_rewrite_that_names_a_summary_still_stores_it(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "plans.db")
    first = _plan_of()
    store.save_plan(first, "first")

    store.save_plan(first.model_copy(update={"updated_at": first.updated_at + timedelta(seconds=30)}), "first, noted", replace=True)

    assert _summaries(store, SESSION) == [(1, "first, noted")]


def test_a_version_saved_with_no_summary_takes_the_one_of_the_plan(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "plans.db")

    store.save_plan(_plan_of().model_copy(update={"change_summary": "Moved the trip"}), None)
    store.save_plan(_plan_of(2).model_copy(update={"change_summary": "Swapped the hotel"}), None, replace=True)

    assert _summaries(store, SESSION) == [(2, "Swapped the hotel"), (1, "Moved the trip")]
