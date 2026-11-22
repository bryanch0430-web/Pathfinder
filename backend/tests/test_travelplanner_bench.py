"""The mocked TravelPlanner calls accept only a complete request."""

from datetime import date

import pytest

from scripts.travelplanner_bench import MockTravel, load_rows


def test_the_official_splits_are_the_full_benchmark() -> None:
    rows = load_rows()
    assert len(rows) == 1225
    assert sum(row["split"] == "validation" for row in rows) == 180
    assert sum(row["split"] == "test" for row in rows) == 1000


def test_a_mock_call_needs_every_field() -> None:
    mock = MockTravel()
    with pytest.raises(ValueError):
        mock.flight({"origin": "Washington", "destination": "Myrtle Beach", "depart_date": None, "party_size": 1})
    answered = mock.flight(
        {
            "origin": "Washington",
            "destination": "Myrtle Beach",
            "depart_date": date(2022, 3, 13),
            "party_size": 1,
        }
    )
    assert answered["origin"] == "Washington"
    with pytest.raises(ValueError):
        mock.hotel(
            {
                "destination": "Myrtle Beach",
                "check_in": date(2022, 3, 15),
                "check_out": date(2022, 3, 13),
                "party_size": 1,
            }
        )
