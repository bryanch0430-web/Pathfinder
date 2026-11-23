from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from app.domain.models import (
    DateRange,
    DayPlan,
    Destination,
    HotelStay,
    PlanAlternative,
    PlanItem,
    TicketOption,
    TripPlan,
    TripSegment,
)


def _client(tmp_path) -> TestClient:
    settings = Settings(
        database_path=str(tmp_path / "jr.db"),
        sandbox=True,
        local_admin=False,
        admin_token="test-admin",
        secret_key="test-secret",
    )
    return TestClient(create_app(settings))


HEADERS = {"X-Client-Token": "jr-tester"}


def _new(client) -> str:
    return client.post("/api/journeys", json={}, headers=HEADERS).json()["session"]["id"]


def _seed(tmp_path, plan: TripPlan) -> None:
    db = sqlite3.connect(tmp_path / "jr.db")
    db.execute(
        "INSERT OR REPLACE INTO plans (session_id, version, body, change_summary, created_at, item_count, destination, days) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (plan.session_id, plan.version, plan.model_dump_json(), "", "2026-11-23T00:00:00+00:00", 0, "x", 1),
    )
    db.commit()
    db.close()


def _sql(tmp_path, query: str, args: tuple = ()):
    db = sqlite3.connect(tmp_path / "jr.db")
    try:
        rows = db.execute(query, args).fetchall()
        db.commit()
        return rows
    finally:
        db.close()


def _plan(session_id: str, **extra) -> TripPlan:
    base = dict(
        trip_id="trip-x",
        session_id=session_id,
        destination=Destination(name="Kyoto"),
        dates=DateRange(start="2027-03-05", end="2027-03-06"),
        days=[
            DayPlan(
                date="2027-03-05",
                title="Day one",
                items=[PlanItem(id="i1", name="Temple", start="09:00", end="10:00")],
            )
        ],
    )
    base.update(extra)
    return TripPlan(**base)


def _proposal(tmp_path, session_id: str, message_id: str, plan: TripPlan, status: str = "pending") -> None:
    import json

    meta = {
        "proposal": {
            "changes": [{"id": "edit-1", "label": "Change", "status": status, "plan": plan.model_dump(mode="json")}]
        }
    }
    _sql(
        tmp_path,
        "INSERT INTO messages (id, session_id, role, content, created_at, meta) VALUES (?,?,?,?,?,?)",
        (message_id, session_id, "assistant", "ok", "2026-11-23T00:00:00+00:00", json.dumps(meta)),
    )


# -- 1. proposals ------------------------------------------------------------


def test_proposal_of_another_trip_is_not_found(tmp_path):
    with _client(tmp_path) as client:
        mine, other = _new(client), _new(client)
        _seed(tmp_path, _plan(mine))
        _seed(tmp_path, _plan(other))
        _proposal(tmp_path, other, "msg-other", _plan(other, version=2))
        response = client.post(
            f"/api/journeys/{mine}/proposals/msg-other", json={"change_id": "edit-1", "accept": True}, headers=HEADERS
        )
        assert response.status_code == 404
        status = _sql(tmp_path, "SELECT meta FROM messages WHERE id='msg-other'")[0][0]
        assert '"pending"' in status


def test_a_proposal_is_decided_only_once(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        _seed(tmp_path, _plan(sid))
        _proposal(tmp_path, sid, "m1", _plan(sid, version=2, summary="new"))
        url = f"/api/journeys/{sid}/proposals/m1"
        assert client.post(url, json={"change_id": "edit-1", "accept": True}, headers=HEADERS).status_code == 200
        assert client.post(url, json={"change_id": "edit-1", "accept": True}, headers=HEADERS).status_code == 409
        assert client.post(url, json={"change_id": "edit-1", "accept": False}, headers=HEADERS).status_code == 409
        assert len(client.get(f"/api/journeys/{sid}/revisions", headers=HEADERS).json()["versions"]) == 2

        _proposal(tmp_path, sid, "m2", _plan(sid, version=3), status="pending")
        url2 = f"/api/journeys/{sid}/proposals/m2"
        assert client.post(url2, json={"change_id": "edit-1", "accept": False}, headers=HEADERS).status_code == 200
        assert client.post(url2, json={"change_id": "edit-1", "accept": True}, headers=HEADERS).status_code == 409


def test_a_stale_proposal_cannot_replace_a_newer_plan(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        _seed(tmp_path, _plan(sid))
        # A suggestion cut from version 1 ...
        _proposal(tmp_path, sid, "old", _plan(sid, version=2, summary="old idea"))
        # ... then the plan moved on to version 2 and 3 by other means.
        _seed(tmp_path, _plan(sid, version=2, summary="newer"))
        _seed(tmp_path, _plan(sid, version=3, summary="newest"))
        response = client.post(
            f"/api/journeys/{sid}/proposals/old", json={"change_id": "edit-1", "accept": True}, headers=HEADERS
        )
        assert response.status_code == 409
        assert client.get(f"/api/journeys/{sid}/itinerary", headers=HEADERS).json()["plan"]["summary"] == "newest"


# -- 2. choose -----------------------------------------------------------------


def test_choosing_a_flight_on_a_multi_city_plan_keeps_the_other_cities(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        tickets = [
            TicketOption(id="o1", name="Osaka A", destination="Osaka", selected=True),
            TicketOption(id="o2", name="Osaka B", destination="Osaka"),
            TicketOption(id="k1", name="Kyoto A", destination="Kyoto", selected=True),
            TicketOption(id="n1", name="Nara A", destination="Nara", selected=True),
        ]
        segments = [TripSegment(id=f"seg-{n}") for n in (1, 2, 3)]
        _seed(tmp_path, _plan(sid, tickets=tickets, segments=segments))
        response = client.post(
            f"/api/journeys/{sid}/plan/choose",
            json={"kind": "flight", "option_id": "o2"},
            headers=HEADERS,
        )
        assert response.status_code == 200
        picked = {row["id"] for row in response.json()["plan"]["tickets"] if row["selected"]}
        assert picked == {"o2", "k1", "n1"}


def test_choosing_a_hotel_with_an_unknown_target_is_404_and_changes_only_that_stay(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        stays = [HotelStay(id=f"h{n}", name=f"Stay {n}", segment_id=f"seg-{n}") for n in (1, 2, 3)]
        options = [HotelStay(id="opt", name="Option", segment_id="seg-2")]
        _seed(tmp_path, _plan(sid, hotels=stays, hotel_options=options))
        url = f"/api/journeys/{sid}/plan/choose"
        bogus = client.post(url, json={"kind": "hotel", "target_id": "nope", "option_id": "opt"}, headers=HEADERS)
        assert bogus.status_code == 404
        good = client.post(url, json={"kind": "hotel", "target_id": "h2", "option_id": "opt"}, headers=HEADERS)
        assert good.status_code == 200
        assert [row["name"] for row in good.json()["plan"]["hotels"]] == ["Stay 1", "Option", "Stay 3"]


def test_the_replaced_stop_stays_as_an_alternative_and_can_be_switched_back(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        item = PlanItem(id="i1", name="Temple", alternatives=[PlanAlternative(id="alt1", name="Garden")])
        _seed(
            tmp_path,
            _plan(sid, days=[DayPlan(date="2027-03-05", items=[item])]),
        )
        url = f"/api/journeys/{sid}/plan/choose"
        first = client.post(url, json={"kind": "item", "target_id": "i1", "option_id": "alt1"}, headers=HEADERS).json()[
            "plan"
        ]
        stop = first["days"][0]["items"][0]
        assert stop["name"] == "Garden"
        back = [row for row in stop["alternatives"] if row["name"] == "Temple"]
        assert len(back) == 1
        second = client.post(
            url, json={"kind": "item", "target_id": "i1", "option_id": back[0]["id"]}, headers=HEADERS
        ).json()["plan"]
        assert second["days"][0]["items"][0]["name"] == "Temple"
        assert [row["name"] for row in second["days"][0]["items"][0]["alternatives"]] == ["Garden"]


# -- 3. share page -------------------------------------------------------------


def test_share_page_escapes_everything_and_hides_the_session_id(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        plan = _plan(
            sid,
            summary="<script>alert(1)</script>",
            destination=Destination(name="<img src=x onerror=alert(2)>"),
            days=[DayPlan(date="2027-03-05", title="<b>t</b>", items=[PlanItem(id="i1", name="<script>x()</script>")])],
        )
        _seed(tmp_path, plan)
        slug = client.post(f"/api/journeys/{sid}/share", headers=HEADERS).json()["slug"]
        page = client.get(f"/s/{slug}").text
        assert "<script>" not in page and "<img" not in page and "<b>t</b>" not in page
        assert "&lt;script&gt;x()&lt;/script&gt;" in page
        assert sid not in page
        shared = client.get(f"/api/public/{slug}")
        assert shared.status_code == 200
        assert sid not in shared.text


def test_share_page_refuses_an_expired_link(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        _seed(tmp_path, _plan(sid))
        slug = client.post(f"/api/journeys/{sid}/share", headers=HEADERS).json()["slug"]
        assert client.get(f"/s/{slug}").status_code == 200
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _sql(tmp_path, "UPDATE shares SET expires_at=? WHERE slug=?", (past, slug))
        assert client.get(f"/s/{slug}").status_code == 410
        assert client.get(f"/api/public/{slug}").status_code == 410


# -- 4. calendar ---------------------------------------------------------------


def test_calendar_escapes_line_breaks_and_folds_long_lines(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        evil = "Lunch\r\nBEGIN:VEVENT\rSUMMARY:injected"
        long_name = "京都" * 60
        plan = _plan(
            sid,
            days=[DayPlan(date="2027-03-05", items=[PlanItem(id="i1", name=evil), PlanItem(id="i2", name=long_name)])],
        )
        _seed(tmp_path, plan)
        raw = client.get(f"/api/journeys/{sid}/calendar.ics", headers=HEADERS).content
        text = raw.decode("utf-8")  # a split UTF-8 character would raise here
        assert text.split("\r\n").count("BEGIN:VEVENT") == 2
        assert "SUMMARY:injected" not in text.split("\r\n")
        for line in raw.split(b"\r\n"):
            assert len(line) <= 75
        unfolded = text.replace("\r\n ", "")
        assert "SUMMARY:" + long_name in unfolded


# -- 5. finalised trips are read-only -------------------------------------------


def test_a_finalised_trip_refuses_edits(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        _seed(tmp_path, _plan(sid, hotels=[HotelStay(id="h1", name="Stay")]))
        assert client.post(f"/api/journeys/{sid}/finalise", headers=HEADERS).status_code == 200
        base = f"/api/journeys/{sid}"
        calls = [
            client.post(f"{base}/layout", json={"items": []}, headers=HEADERS),
            client.post(f"{base}/lock", json={"kind": "hotel", "target_id": "h1"}, headers=HEADERS),
            client.post(
                f"{base}/plan/choose", json={"kind": "hotel", "target_id": "h1", "option_id": "h1"}, headers=HEADERS
            ),
            client.post(f"{base}/itinerary/restore", json={"version": 1}, headers=HEADERS),
        ]
        assert [call.status_code for call in calls] == [409, 409, 409, 409]
        assert "finalised" in calls[0].json()["detail"]
        state = client.get(base, headers=HEADERS).json()
        assert state["context"]["finalized"] is True
        assert state["plan"]["status"] == "ready"


# -- 6. deleting the chat keeps the saved trip ------------------------------------


def test_deleting_a_chat_keeps_its_saved_trip(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        _seed(tmp_path, _plan(sid))
        client.post(f"/api/journeys/{sid}/finalise", headers=HEADERS)
        assert len(client.get("/api/wishlist", headers=HEADERS).json()["trips"]) == 1
        assert client.delete(f"/api/journeys/{sid}", headers=HEADERS).status_code == 200
        listed = client.get("/api/wishlist", headers=HEADERS).json()["trips"]
        assert len(listed) == 1
        saved_id = listed[0]["id"]
        assert client.get(f"/api/wishlist/{saved_id}", headers=HEADERS).status_code == 200
        opened = client.post(f"/api/wishlist/{saved_id}/open", headers=HEADERS)
        assert opened.status_code == 200
        assert opened.json()["plan"]["destination"]["name"] == "Kyoto"
        assert client.delete(f"/api/wishlist/{saved_id}", headers=HEADERS).json()["deleted"] == 1


# -- 7. validation -------------------------------------------------------------


def test_state_patch_rejects_bad_values_with_422(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        url = f"/api/journeys/{sid}/state"
        bad = [
            {"days": -3},
            {"days": "three"},
            {"party_size": 0},
            {"party_size": 21},
            {"party_size": True},
            {"start_date": "nope"},
            {"budget": -5},
            {"budget": "lots"},
            {"preferences": "food"},
            {"preferences": [1, 2]},
            {"destination": 5},
            {"finalized": "yes"},
            {"stops": [{"nights": 2}]},
        ]
        for body in bad:
            assert client.patch(url, json=body, headers=HEADERS).status_code == 422, body
        ok = client.patch(
            url,
            json={"days": 4, "party_size": 2, "budget": 1000, "start_date": "2027-03-05", "preferences": ["food"]},
            headers=HEADERS,
        )
        assert ok.status_code == 200
        assert ok.json()["context"]["days"] == 4


def test_import_checks_before_creating_a_session_and_gets_a_fresh_trip_id(tmp_path):
    with _client(tmp_path) as client:
        before = _sql(tmp_path, "SELECT COUNT(*) FROM sessions")[0][0]
        broken = client.post("/api/journeys/import", json={"plan": {"nope": 1}}, headers=HEADERS)
        assert broken.status_code == 400
        assert _sql(tmp_path, "SELECT COUNT(*) FROM sessions")[0][0] == before

        huge = _plan("s", summary="x" * 6_000_000).model_dump(mode="json")
        assert client.post("/api/journeys/import", json={"plan": huge}, headers=HEADERS).status_code in (413, 422)
        assert _sql(tmp_path, "SELECT COUNT(*) FROM sessions")[0][0] == before

        source = _plan("s").model_dump(mode="json")
        made = client.post("/api/journeys/import", json={"plan": source}, headers=HEADERS)
        assert made.status_code == 200
        assert made.json()["plan"]["trip_id"] != "trip-x"


def test_finalise_needs_a_plan_and_rating_needs_real_booleans(tmp_path):
    with _client(tmp_path) as client:
        sid = _new(client)
        assert client.post(f"/api/journeys/{sid}/finalise", headers=HEADERS).status_code == 409
        assert client.get(f"/api/journeys/{sid}", headers=HEADERS).json()["context"]["finalized"] is False
        rating = f"/api/journeys/{sid}/rating"
        assert client.post(rating, json={"useful": "false"}, headers=HEADERS).status_code == 422
        assert client.post(rating, json={"useful": 1}, headers=HEADERS).status_code == 422
        assert client.post(rating, json={"useful": False}, headers=HEADERS).status_code == 200
