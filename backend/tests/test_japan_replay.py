"""Proposal TC-02 (p4 l.140-143): Japan scenarios fetch, parse and merge fragmented provider data.

The default run is offline: ``app.eval_replay`` serves responses shaped like the real
Google, Open-Meteo, Nominatim, Wikivoyage and DeepSeek APIs through the app's own HTTP
helper, so the real parsers, the agents' merge step and the plan path run on them.
``test_tc02_live_*`` calls the real keyless providers and is skipped unless
PATHFINDER_JAPAN_LIVE=1 (never in CI or the default run).
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app.eval_replay as eval_replay
from app.application.agents import _merge_places
from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import DataStatus, EvalCase, GeoLocation, GeoPoint, PlaceCandidate
from app.eval_replay import LIVE_ENV, Replay, load_replay, replay_case
from app.evaluation import EvalService
from app.infrastructure.http import use_transport
from app.infrastructure.providers import geocoding
from app.infrastructure.providers.places import GooglePlacesSource, NominatimPlacesSource, WikivoyagePlacesSource
from app.infrastructure.providers.weather import OpenMeteoWeather
from app.infrastructure.providers.websearch import DeepSeekSearchSource
from app.infrastructure.store import SQLiteStore
from app.interface.api import create_app

LIVE = eval_replay.live_mode()


def _settings(root: Path) -> Settings:
    root.mkdir(parents=True, exist_ok=True)
    return Settings(
        _env_file=None,
        data_dir=root,
        database_path=root / "test.db",
        trace_dir=root / "traces",
        eval_dir=root / "eval",
        llm_api_key="",
        llm_base_url="",
        xai_api_key="",
        decision_enabled=False,
        langfuse_enabled=False,
        default_currency="USD",
    )


@pytest.fixture(scope="module")
def japan_run(tmp_path_factory) -> dict:
    """One japan trial through POST /api/eval/trials, the Evaluation page's code path."""
    with TestClient(create_app(_settings(tmp_path_factory.mktemp("japan")))) as client:
        response = client.post("/api/eval/trials", json={"dataset_id": "japan", "experiment_tag": "tc02"})
    assert response.status_code == 200, response.text
    return response.json()["run"]


def _cases(settings: Settings) -> dict[str, EvalCase]:
    return {case.id: case for case in EvalService(SQLiteStore(settings.database_path), settings).load_cases("japan")}


def _city(name: str) -> dict:
    return load_replay()["cities"][name]


def _replayed(city: str, call, faults: list | None = None):
    replay = Replay(_city(city)["routes"], faults)

    async def run():
        with use_transport(replay.transport()):
            return await call()

    return asyncio.run(run()), replay


def test_tc02_replay_covers_every_japan_case_provider_and_kind_of_fragment(settings: Settings) -> None:
    """Proposal TC-02 (p4): geocoder, places, weather and web search, fragmented on purpose."""
    data = load_replay()
    assert set(data["cases"]) == set(_cases(settings)) == {f"jp0{n}" for n in range(1, 7)}
    assert {spec["city"] for spec in data["cases"].values()} == {"Tokyo", "Kyoto", "Osaka"}
    routes = {route["id"] for city in data["cities"].values() for route in city["routes"]}
    assert {
        "google.geocode", "openmeteo.geocode", "nominatim.geocode", "google.attractions", "nominatim.attractions",
        "wikivoyage", "openmeteo.forecast", "openmeteo.archive", "deepseek.search",
    } <= routes
    faults = [fault for spec in data["cases"].values() for fault in spec["faults"]]
    assert {500, 503, 429} <= {fault.get("status") for fault in faults}
    assert any("text" in fault and fault.get("status") == 200 for fault in faults)  # an HTML error page
    assert any(fault.get("drop_days") for fault in faults)  # a forecast day left out
    for city in data["cities"].values():
        names = [name for place in city["truth"] for name in place["names"]]
        assert any(not name.isascii() for name in names) and any(name.isascii() for name in names)


def test_tc02_japan_run_reports_the_replay_scores_per_case(japan_run: dict) -> None:
    """Proposal TC-02 (p4): the japan trial report carries parse, dedupe, conflict and gap scores."""
    metrics = {metric["key"]: metric for metric in japan_run["metrics"]}
    for key in ("replay_parse_rate", "replay_dedupe_rate", "replay_conflicts_rate", "replay_gaps_rate"):
        assert metrics[key]["unit"] == "%" and metrics[key]["value"] == 100.0, metrics.get(key)
    assert "offline replay" in japan_run["model_snapshot"]["japan_replay"]
    for case in japan_run["cases"]:
        replay = case["plan_checks"]["replay"]
        assert replay["mode"] == "replay" and replay["faults"], case["case_id"]
        assert case["scores"]["replay_parse"] == case["scores"]["replay_dedupe"] == case["scores"]["replay_gaps"] == 1.0
        assert "Replay: parse" in case["judge_reason"]
        assert case["passed"], (case["case_id"], case["failure_class"])
        assert not replay.get("unrecorded_requests"), replay.get("unrecorded_requests")
    conflicts = [case["scores"].get("replay_conflicts") for case in japan_run["cases"]]
    assert sum(value is not None for value in conflicts) >= 4


def test_tc02_parsers_keep_well_formed_rows_and_drop_malformed_ones(settings: Settings) -> None:
    """Proposal TC-02 (p4): missing fields are dropped or left empty, never invented."""
    rs = eval_replay.replay_settings(settings, live=False)
    centre = GeoPoint(lat=35.0116, lng=135.7681)
    google, _ = _replayed("Kyoto", lambda: GooglePlacesSource(rs).search(destination="Kyoto", geo=centre, category="attraction", limit=10))
    names = [row.name for row in google]
    assert len(google) == 6 and "Gion Corner" not in names and "Unnamed place" not in names  # no location / no name
    osm, _ = _replayed("Tokyo", lambda: NominatimPlacesSource(rs).search(destination="Tokyo", geo=centre, category="attraction", limit=10))
    assert len(osm) == 7 and "東京国立博物館" not in [row.name for row in osm]  # longitude missing
    assert [row.name for row in osm].count("浅草寺") == 2  # one taken from display_name
    assert "Sensō-ji" in next(row for row in osm if row.name == "浅草寺" and row.aliases).aliases
    wiki, _ = _replayed("Kyoto", lambda: WikivoyagePlacesSource(rs).search(destination="Kyoto", geo=centre, category="attraction", limit=10))
    ginkaku = next(row for row in wiki if row.name == "Ginkaku-ji")
    assert len(wiki) == 6 and ginkaku.geo is None and ginkaku.aliases == ["銀閣寺"]
    web, _ = _replayed("Kyoto", lambda: DeepSeekSearchSource(rs).search(query="hotels in Kyoto", count=5, retries=0))
    assert len(web) == 2 and all(row.url for row in web)
    start = date.today() + timedelta(days=2)
    location = GeoLocation(name="Kyoto", geo=centre)
    fault = [{"route": "openmeteo.forecast", "drop_days": [1], "null": {"temperature_2m_max": [0]}}]
    days, _ = _replayed("Kyoto", lambda: OpenMeteoWeather(rs).forecast(location, start, start + timedelta(days=2)), fault)
    assert [day.status for day in days] == [DataStatus.OK, DataStatus.UNAVAILABLE, DataStatus.OK]
    assert days[0].high_c is None and days[0].low_c is not None


def test_tc02_failed_responses_become_classified_provider_errors(settings: Settings) -> None:
    """Proposal TC-02 (p4): a 5xx, a 429 and an HTML page are classified, not crashes."""
    rs = eval_replay.replay_settings(settings, live=False)
    centre = GeoPoint(lat=35.0116, lng=135.7681)
    cases = [
        ("google.attractions", {"status": 503, "text": "Service Unavailable"}, "server_error",
         lambda: GooglePlacesSource(rs).search(destination="Kyoto", geo=centre, category="attraction", limit=10)),
        ("deepseek.search", {"status": 429, "text": "rate limited"}, "rate_limit",
         lambda: DeepSeekSearchSource(rs).search(query="hotels in Kyoto", count=5, retries=0)),
        ("wikivoyage", {"status": 200, "text": "<!DOCTYPE html><html></html>"}, "parse",
         lambda: WikivoyagePlacesSource(rs).search(destination="Kyoto", geo=centre, category="attraction", limit=10)),
    ]
    for route, fault, kind, call in cases:
        with pytest.raises(ProviderError) as caught:
            _replayed("Kyoto", call, [{"route": route, **fault}])
        assert caught.value.kind.value == kind, route


def test_tc02_merge_joins_japanese_and_english_names_and_keeps_neighbours_apart() -> None:
    """Proposal TC-02 (p4): one record per real place across scripts and spellings."""
    def place(name, lat, lng, *aliases, category="Attraction"):
        return PlaceCandidate(name=name, category=category, geo=GeoPoint(lat=lat, lng=lng), aliases=list(aliases))

    merged = _merge_places([
        place("伏見稲荷大社", 34.9672, 135.7731, "Fushimi Inari Taisha"),
        place("金閣寺", 35.0394, 135.7292),  # an ASCII-only name filter made these two "identical"
        place("Fushimi Inari Taisha", 34.9671, 135.7727),
        place("Kinkaku-ji", 35.0394, 135.7293, "金閣寺"),
        place("Ginkaku-ji", 35.0270, 135.7982),  # 0.89 alike to Kinkaku-ji, 6 km away
        place("Sensō-ji", 35.7148, 135.7967),
        place("Senso-ji Temple", 35.7147, 135.7966),
        place("Asakusa Shrine", 35.7153, 135.7978, "浅草神社"),  # 110 m from Senso-ji
        place("Starbucks Coffee", 35.0, 135.76, category="Cafe"),
        place("Starbucks Coffee", 35.03, 135.76, category="Cafe"),  # a chain, 3 km apart
    ])
    groups = sorted(sorted([record.name, *record.aliases]) for record in merged)
    assert ["Fushimi Inari Taisha", "伏見稲荷大社"] in groups
    assert ["Kinkaku-ji", "金閣寺"] in [sorted(set(group)) for group in groups]
    assert ["Ginkaku-ji"] in groups and ["Asakusa Shrine", "浅草神社"] in groups
    assert ["Senso-ji Temple", "Sensō-ji"] in groups
    assert [group for group in groups if group == ["Starbucks Coffee"]] == [["Starbucks Coffee"]] * 2
    assert len(merged) == 7


def test_tc02_conflicting_coordinates_settle_on_the_point_two_sources_agree_on() -> None:
    """Proposal TC-02 (p4): a swapped digit in one source does not move the place."""
    wrong = PlaceCandidate(name="Nijo Castle", geo=GeoPoint(lat=35.0412, lng=135.7482))
    osm = PlaceCandidate(name="元離宮二条城", geo=GeoPoint(lat=35.0142, lng=135.7481), aliases=["Nijō Castle", "二条城"])
    guide = PlaceCandidate(name="Nijō Castle", geo=GeoPoint(lat=35.0142, lng=135.7482), aliases=["二条城"])
    (merged,) = _merge_places([wrong, osm, guide])
    assert abs(merged.geo.lat - 35.0142) < 0.001
    assert {"元離宮二条城", "Nijō Castle", "二条城"} <= set(merged.aliases)


def test_tc02_partial_failures_are_on_the_plan_record(settings: Settings) -> None:
    """Proposal TC-02 (p4): every injected failure is a gap, a data note or an unavailable day."""
    cases = _cases(settings)
    found = {}
    for case_id in ("jp01", "jp02", "jp04", "jp06"):
        report = asyncio.run(replay_case(cases[case_id], settings))
        assert report.scores["replay_gaps"] == 1.0, report.details["gaps"]
        found[case_id] = [check["found"] for check in report.details["gaps"]["checks"]]
    assert "place source failed" in found["jp01"][0]
    assert "Search did not answer in time" in found["jp02"][0]
    assert "restaurant source failed" in found["jp04"][0]  # was dropped once places existed
    assert found["jp06"][0].startswith("day 2: unavailable")


def test_tc02_replay_catches_a_merge_that_reads_only_ascii_names(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proposal TC-02 (p4): the scores have teeth; the merge as it was before scores below 1."""
    def ascii_merge(places):
        merged = []
        for place in places:
            norm = re.sub(r"[^a-z0-9 ]", "", place.name.lower()).strip()
            for existing in merged:
                if SequenceMatcher(None, re.sub(r"[^a-z0-9 ]", "", existing.name.lower()).strip(), norm).ratio() > 0.86:
                    break
            else:
                merged.append(place)
        return merged

    monkeypatch.setattr(eval_replay, "_merge_places", ascii_merge)
    report = asyncio.run(replay_case(_cases(settings)["jp06"], settings))
    assert report.scores["replay_dedupe"] < 1.0 and report.scores["replay_conflicts"] < 1.0
    assert not report.passed and report.failure_class == "replay_dedupe"


def test_tc02_replay_stays_offline_and_leaves_the_geocode_cache_alone(settings: Settings) -> None:
    """Proposal TC-02 (p4): no request leaves the replay; replayed coordinates are not cached."""
    geocoding._GEOCODE_CACHE[("sentinel", "")] = "kept"
    try:
        report = asyncio.run(replay_case(_cases(settings)["jp06"], settings))
        assert geocoding._GEOCODE_CACHE == {("sentinel", ""): "kept"}
    finally:
        geocoding._GEOCODE_CACHE.pop(("sentinel", ""), None)
    assert report.mode == "replay" and "unrecorded_requests" not in report.details
    replay = Replay(_city("Osaka")["routes"])
    response = replay.transport().handle_request(httpx.Request("GET", "https://example.com/elsewhere"))
    assert response.status_code == 404 and replay.misses == ["GET https://example.com/elsewhere"]


@pytest.mark.skipif(not LIVE, reason=f"live providers: set {LIVE_ENV}=1 to run (never in CI)")
def test_tc02_live_keyless_providers_parse_and_merge(settings: Settings) -> None:
    """Proposal TC-02 (p4), live mode: the real keyless providers answer, parse and merge."""
    report = asyncio.run(replay_case(_cases(settings)["jp01"], settings))
    assert report.mode == "live"
    assert report.scores["replay_parse"] is not None and report.scores["replay_parse"] > 0, report.details["parse"]
    assert report.details["dedupe"]["records"] > 0, report.details["dedupe"]
