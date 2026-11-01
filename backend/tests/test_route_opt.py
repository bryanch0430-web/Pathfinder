from app.application.planner import _route_length, _two_opt


def _row(lat: float, lng: float) -> dict:
    return {"geo": {"lat": lat, "lng": lng}}


def test_two_opt_improves_open_path_and_keeps_first_stop() -> None:
    rows = [_row(0, 0), _row(10, 10), _row(1, 1), _row(11, 9), _row(2, 2)]
    before = _route_length(rows)
    after = _two_opt(rows)
    assert after[0] is rows[0]
    assert len(after) == len(rows)
    assert _route_length(after) <= before


def test_two_opt_is_a_noop_for_small_days() -> None:
    rows = [_row(0, 0), _row(1, 1), _row(2, 2)]
    assert _two_opt(rows) == rows
