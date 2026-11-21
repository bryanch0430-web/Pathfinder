"""A duplicate place on one clock is merged. Two different stops may share a clock."""

import json

from app.domain.schedule import separate_overlaps


def _item(name: str, start: str, end: str, *, locked: bool = False) -> dict:
    return {
        "id": name.lower().replace(" ", "-"),
        "type": "attraction",
        "name": name,
        "start": start,
        "end": end,
        "locked": locked,
        "geo": {"lat": 22.3, "lng": 114.1},
        "source_refs": [{"title": name, "url": "https://example.com", "source": "google-places"}],
    }


def _day(items: list[dict]) -> dict:
    return {"date": "2027-03-03", "items": items}


def test_an_overlap_of_two_places_is_kept() -> None:
    day = _day([_item("Victoria Harbour", "10:00", "12:00"), _item("Blend & Grind Starstreet", "11:00", "12:30")])
    notes = separate_overlaps({"days": [day]})
    assert [(item["name"], item["start"], item["end"]) for item in day["items"]] == [
        ("Victoria Harbour", "10:00", "12:00"),
        ("Blend & Grind Starstreet", "11:00", "12:30"),
    ]
    assert notes == []


def test_a_duplicate_name_on_the_same_time_is_merged() -> None:
    day = _day([_item("Victoria Harbour", "10:00", "12:00"), _item("Victoria Harbour", "11:00", "12:30")])
    notes = separate_overlaps({"days": [day]})
    assert [item["name"] for item in day["items"]] == ["Victoria Harbour"]
    assert any(note.startswith("Merged a duplicate") for note in notes)


def test_a_locked_stop_and_another_place_both_keep_their_clocks() -> None:
    day = _day([
        _item("Blend & Grind Starstreet", "11:00", "12:30"),
        _item("Victoria Harbour", "10:00", "12:00", locked=True),
    ])
    separate_overlaps({"days": [day]})
    by_name = {item["name"]: item for item in day["items"]}
    assert (by_name["Victoria Harbour"]["start"], by_name["Victoria Harbour"]["end"]) == ("10:00", "12:00")
    assert (by_name["Blend & Grind Starstreet"]["start"], by_name["Blend & Grind Starstreet"]["end"]) == ("11:00", "12:30")


def test_an_overlap_is_recorded_and_the_line_stays_after_the_times_move(tmp_path) -> None:
    from app.domain.models import TripPlan
    from app.infrastructure.store import SQLiteStore

    store = SQLiteStore(tmp_path / "notes.db")
    session = store.create_session("guest-token")
    plan = TripPlan.model_validate(
        {
            "trip_id": "t",
            "session_id": session.id,
            "version": 1,
            "destination": {"name": "Hong Kong", "country_code": "HK", "timezone": "Asia/Hong_Kong"},
            "dates": {"start": "2027-03-03", "end": "2027-03-03"},
            "days": [_day([
                _item("Victoria Harbour", "10:00", "12:00"),
                _item("Blend & Grind Starstreet", "11:00", "12:30"),
            ])],
            "budget": {"amount": 9000, "currency": "HKD"},
        }
    )
    store.save_plan(plan, "Overlap")
    saved = store.get_plan(session.id)
    assert saved is not None
    assert saved.data_quality.notes == ["Victoria Harbour and Blend & Grind Starstreet overlap on 2027-03-03."]
    saved.days[0].items[1].start = "12:30"
    saved.days[0].items[1].end = "14:00"
    saved.version = 2
    store.save_plan(saved, "Moved apart")
    later = store.get_plan(session.id)
    assert later is not None
    assert later.data_quality.notes == saved.data_quality.notes


def test_a_stored_overlap_is_repaired_when_the_plan_is_opened(tmp_path) -> None:
    from app.domain.models import TripPlan
    from app.infrastructure.store import SQLiteStore

    store = SQLiteStore(tmp_path / "overlap.db")
    session = store.create_session("guest-token")
    plan = TripPlan.model_validate(
        {
            "trip_id": "t",
            "session_id": session.id,
            "version": 1,
            "destination": {"name": "Hong Kong", "country_code": "HK", "timezone": "Asia/Hong_Kong"},
            "dates": {"start": "2027-03-03", "end": "2027-03-03"},
            "days": [_day([_item("Victoria Harbour", "10:00", "12:00")])],
            "budget": {"amount": 9000, "currency": "HKD"},
        }
    )
    store.save_plan(plan, "Initial plan")
    raw = json.loads(plan.model_dump_json())
    raw["days"][0]["items"].append(_item("Blend & Grind Starstreet", "11:00", "12:30"))
    with store._lock, store._connect() as conn:
        conn.execute(
            "UPDATE itineraries SET data=? WHERE session_id=?",
            (json.dumps(raw), session.id),
        )
    opened = store.get_plan(session.id)
    assert opened is not None
    names = [(item.name, item.start, item.end) for item in opened.days[0].items]
    assert ("Victoria Harbour", "10:00", "12:00") in names
    assert ("Blend & Grind Starstreet", "11:00", "12:30") in names
    again = store.get_plan(session.id)
    assert again is not None
    assert [(item.name, item.start) for item in again.days[0].items] == [
        (item.name, item.start) for item in opened.days[0].items
    ]
