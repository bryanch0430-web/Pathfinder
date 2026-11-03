"""Plan display translation (en -> zh-Hant) and trusted place names. No network: the
translator is a fake that rewrites a few words and keeps everything else."""

from __future__ import annotations

import asyncio
import re
from datetime import date

import pytest

from app.application.agents import _merge_places
from app.application.planner import _meal_item
from app.application.translation import DISCLAIMERS, LLMTranslator, TranslationService
from app.config import Settings
from app.domain.models import (
    DataGap,
    DataQuality,
    DateRange,
    DayPlan,
    Destination,
    FailureKind,
    GeoPoint,
    PlaceCandidate,
    PlanItem,
    SourceRef,
    TripPlan,
)
from app.domain.place_names import display_name, osm_names, trusted_names
from app.infrastructure.providers import places as places_module
from app.infrastructure.providers.places import NominatimPlacesSource

WORDS = {
    "Palace": "宮殿",
    "visit": "參觀",
    "Lunch": "午餐",
    "Tickets cost": "門票",
    "Opens at": "開放時間",
    "Day one": "第一天",
    "Old town": "舊城",
    "Walk": "步行",
    "Weather data is unavailable": "天氣資料暫時未能提供",
    "Three days in": "三日遊",
}


def _zh(text: str) -> str:
    for english, chinese in WORDS.items():
        text = text.replace(english, chinese)
    return text


class FakeTranslator:
    """Translates by word swap; ``corrupt`` lets a test damage the answer."""

    available = True

    def __init__(self, corrupt=None):
        self.corrupt = corrupt
        self.calls: list[list[dict]] = []

    async def translate(self, segments, *, locale, glossary):
        self.calls.append([dict(segment) for segment in segments])
        rows = [{"id": segment["id"], "text": _zh(segment["text"])} for segment in segments]
        return self.corrupt(rows) if self.corrupt else rows


def _source(name: str) -> list[SourceRef]:
    return [SourceRef(title=name, url=f"https://example.test/{name}", source="test")]


def _plan(*, version: int = 1, palace_note: str = "Opens at 09:00; Tickets cost KRW 3,000.") -> TripPlan:
    return TripPlan(
        session_id="sess_t",
        trip_id="trip_t",
        version=version,
        destination=Destination(name="Seoul", geo=GeoPoint(lat=37.57, lng=126.98)),
        dates=DateRange(start=date(2027, 4, 1), end=date(2027, 4, 2)),
        summary="Three days in Seoul with 2 palaces; see https://example.test/plan for sources.",
        days=[
            DayPlan(
                date=date(2027, 4, 1),
                title="Day one: Old town",
                note="Walk 15 min between stops.",
                items=[
                    PlanItem(
                        id="item_p1",
                        type="attraction",
                        name="경복궁",
                        names={"en": "Gyeongbokgung Palace", "zh-Hant": "景福宮"},
                        start="09:30",
                        end="11:30",
                        notes=f"경복궁 visit. {palace_note}",
                        source_refs=_source("palace"),
                    ),
                    PlanItem(
                        id="item_m1",
                        type="meal",
                        name="Tosokchon",
                        start="12:00",
                        end="13:00",
                        notes="Lunch at Tosokchon, about KRW 20,000 per person.",
                    ),
                ],
            ),
            DayPlan(
                date=date(2027, 4, 2),
                title="Day two",
                items=[
                    PlanItem(
                        id="item_p2",
                        type="attraction",
                        name="Bukchon Hanok Village",
                        start="10:00",
                        end="12:00",
                        notes="Walk the lanes from 10:00 to 12:00.",
                        source_refs=_source("bukchon"),
                    )
                ],
            ),
        ],
        data_quality=DataQuality(
            gaps=[DataGap(section="weather", kind=FailureKind.NO_DATA, message="Weather data is unavailable for 2027-04-02.")]
        ),
    )


def _run(service: TranslationService, plan: TripPlan, locale: str = "zh-Hant") -> dict:
    return asyncio.run(service.translate_plan(plan, locale))


def test_translation_good_translation_passes_and_keeps_ids_and_order() -> None:
    fake = FakeTranslator()
    result = _run(TranslationService(fake), _plan())

    assert result["status"] == "translated", result["reason"]
    assert result["locale"] == "zh-Hant" and result["plan_version"] == 1
    assert result["disclaimer"] == DISCLAIMERS["zh-Hant"]
    fields = result["fields"]
    assert list(fields["items"]) == ["item_p1", "item_m1", "item_p2"]
    assert list(fields["days"]) == ["2027-04-01", "2027-04-02"]
    assert fields["items"]["item_p1"]["notes"] == "경복궁 參觀. 開放時間 09:00; 門票 KRW 3,000."
    assert fields["days"]["2027-04-01"] == {"title": "第一天: 舊城", "note": "步行 15 min between stops."}
    assert fields["gaps"] == {"0": "天氣資料暫時未能提供 for 2027-04-02."}
    assert "https://example.test/plan" in fields["summary"] and "Seoul" in fields["summary"]
    # The model never saw a place name or URL, only placeholders.
    sent = " ".join(segment["text"] for segment in fake.calls[0])
    assert "경복궁" not in sent and "Seoul" not in sent and "https://" not in sent
    assert "{{P" in sent and "{{U1}}" in sent
    # Ids sent to the model are the stop ids, in plan order.
    assert [segment["id"] for segment in fake.calls[0] if segment["id"].startswith("item:")] == [
        "item:item_p1:notes",
        "item:item_m1:notes",
        "item:item_p2:notes",
    ]


def _change_time(rows):
    return [{**row, "text": row["text"].replace("09:00", "10:00")} for row in rows]


def _drop_number(rows):
    return [{**row, "text": row["text"].replace("20,000", "")} for row in rows]


def _reorder(rows):
    return list(reversed(rows))


def _rename_place(rows):
    # An LLM-supplied name in place of the source's name placeholder.
    return [{**row, "text": re.sub(r"\{\{P\d+\}\}", "景福宫", row["text"]), "names": {"zh-Hant": "景福宫"}} for row in rows]


@pytest.mark.parametrize(
    ("corrupt", "reason"),
    [
        (_change_time, "times differ"),
        (_drop_number, "numbers differ"),
        (_reorder, "order"),
        (_rename_place, "place names"),
    ],
)
def test_translation_corrupted_translation_falls_back(corrupt, reason: str) -> None:
    result = _run(TranslationService(FakeTranslator(corrupt)), _plan())

    assert result["status"] == "fallback"
    assert reason in result["reason"]
    assert result["fields"] == {"summary": "", "days": {}, "items": {}, "gaps": {}}
    assert result["disclaimer"] == DISCLAIMERS["zh-Hant"]


def test_translation_glossary_term_is_enforced() -> None:
    def no_glossary(rows):
        return [{**row, "text": row["text"].replace("暫時未能提供", "不可用")} for row in rows]

    result = _run(TranslationService(FakeTranslator(no_glossary)), _plan())
    assert result["status"] == "fallback" and "glossary" in result["reason"]


def test_translation_unavailable_without_model_and_english_needs_none(offline_gateway) -> None:
    service = TranslationService(LLMTranslator(offline_gateway))

    result = _run(service, _plan())
    assert result["status"] == "unavailable"
    assert result["fields"] == {"summary": "", "days": {}, "items": {}, "gaps": {}}
    assert result["disclaimer"] == DISCLAIMERS["zh-Hant"]

    english = _run(service, _plan(), "en")
    assert english["status"] == "english" and english["disclaimer"] == "" and english["reason"] == ""


def test_translation_cache_hit_and_partial_retranslation_after_edit() -> None:
    fake = FakeTranslator()
    service = TranslationService(fake)
    first = _run(service, _plan())
    assert len(fake.calls) == 1

    again = _run(service, _plan())
    assert again == first and len(fake.calls) == 1  # plan-level cache hit

    edited = _plan(version=2, palace_note="Opens at 09:00; Tickets cost KRW 5,000.")
    result = _run(service, edited)
    assert result["status"] == "translated" and result["plan_version"] == 2
    assert len(fake.calls) == 2
    assert [segment["id"] for segment in fake.calls[1]] == ["item:item_p1:notes"]  # only the changed stop
    assert result["fields"]["items"]["item_p1"]["notes"].endswith("KRW 5,000.")
    assert result["fields"]["items"]["item_m1"] == first["fields"]["items"]["item_m1"]


def test_translation_names_come_only_from_source_data(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    async def fake_request_json(*args, **kwargs):
        return {"data": [
            {
                "name": "경복궁",
                "lat": "37.5796",
                "lon": "126.9770",
                "osm_type": "way",
                "osm_id": 1,
                "display_name": "경복궁, Seoul",
                "namedetails": {
                    "name": "경복궁",
                    "name:en": "Gyeongbokgung Palace",
                    "name:zh": "景福宫",  # Simplified: not a zh-Hant name
                    "name:zh-Hant": "景福宮",
                    "name:ja": "景福宮",
                },
            },
            {
                "name": "남산타워",
                "lat": "37.5512",
                "lon": "126.9882",
                "namedetails": {"name": "남산타워", "name:zh": "南山塔"},
            },
        ]}

    monkeypatch.setattr(places_module, "request_json", fake_request_json)
    found = asyncio.run(
        NominatimPlacesSource(settings).search(destination="Seoul", geo=None, category="attraction", limit=5)
    )
    assert found[0].names == {"en": "Gyeongbokgung Palace", "zh-Hant": "景福宮"}
    assert found[1].names == {"zh-Hant": "南山塔"}  # name:zh counts when it is Traditional
    assert osm_names({"name:zh-HK": "維多利亞港", "name:zh-TW": "維多利亞港灣"}) == {"zh-Hant": "維多利亞港"}
    assert osm_names({"name:zh": "维多利亚港"}) == {}

    # The merge keeps the first source's names and fills gaps from the others.
    merged = _merge_places(
        [
            PlaceCandidate(name="경복궁", geo=GeoPoint(lat=37.5796, lng=126.977), names={"en": "Gyeongbokgung Palace"}),
            PlaceCandidate(
                name="Gyeongbokgung Palace",
                aliases=["경복궁"],
                geo=GeoPoint(lat=37.5797, lng=126.9771),
                names={"en": "Other", "zh-Hant": "景福宮"},
            ),
        ]
    )
    assert len(merged) == 1 and merged[0].names == {"en": "Gyeongbokgung Palace", "zh-Hant": "景福宮"}

    # Plan items take names from the source row only, filtered to trusted forms.
    row = {
        "id": "m1",
        "name": "경복궁",
        "source_refs": [],
        "names": {"en": "Gyeongbokgung Palace", "zh-Hant": "景福宫", "ko": "경복궁"},
    }
    assert _meal_item("Lunch", "12:00", "13:00", row).names == {"en": "Gyeongbokgung Palace"}
    assert _meal_item("Lunch", "12:00", "13:00", {"id": "m2", "name": "X", "source_refs": []}).names == {}
    assert trusted_names("景福宮") == {}

    # A translation that tries to supply a name is ignored: names are not in the overlay.
    def with_names(rows):
        return [{**row, "names": {"zh-Hant": "假名"}} for row in rows]

    result = _run(TranslationService(FakeTranslator(with_names)), _plan())
    assert result["status"] == "translated"
    assert "假名" not in str(result) and "names" not in str(result["fields"])


def test_translation_english_display_uses_names_en_for_a_korean_place() -> None:
    assert display_name("경복궁", {"en": "Gyeongbokgung Palace", "zh-Hant": "景福宮"}, "en") == "Gyeongbokgung Palace"
    assert display_name("경복궁", {}, "en") == "경복궁"


def test_translation_zh_hant_display_name_shows_trusted_chinese_then_original() -> None:
    assert display_name("경복궁", {"en": "Gyeongbokgung Palace", "zh-Hant": "景福宮"}, "zh-Hant") == "景福宮 (경복궁)"
    assert display_name("경복궁", {"en": "Gyeongbokgung Palace"}, "zh-Hant") == "경복궁"
    assert display_name("天壇大佛", {"zh-Hant": "天壇大佛"}, "zh-Hant") == "天壇大佛"


def test_translation_endpoint_matches_the_contract(settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from app.interface.api import create_app

    with TestClient(create_app(settings)) as client:
        session_id = client.post("/api/journeys", json={"client_token": "translation-test"}).json()["session"]["id"]
        guest = {"X-Client-Token": "translation-test"}
        container = client.app.state.container
        plan = _plan()
        plan.session_id = session_id
        container.store.save_plan(plan, "test")

        offline = client.get(f"/api/journeys/{session_id}/translation", params={"locale": "zh-Hant"}, headers=guest).json()
        assert set(offline) == {"locale", "plan_version", "status", "reason", "disclaimer", "fields"}
        assert offline["status"] == "unavailable" and offline["disclaimer"] == DISCLAIMERS["zh-Hant"]

        default = client.get(f"/api/journeys/{session_id}/translation", headers=guest).json()
        assert default["locale"] == "en" and default["status"] == "english" and default["disclaimer"] == ""

        container.translation = TranslationService(FakeTranslator())
        translated = client.get(
            f"/api/journeys/{session_id}/translation", params={"version": 1, "locale": "zh-Hant"}, headers=guest
        ).json()
        assert translated["status"] == "translated" and translated["plan_version"] == 1
        assert set(translated["fields"]) == {"summary", "days", "items", "gaps"}
        assert list(translated["fields"]["items"]) == ["item_p1", "item_m1", "item_p2"]

        assert client.get(f"/api/journeys/{session_id}/translation", params={"locale": "ja"}, headers=guest).status_code == 422
        assert client.get(f"/api/journeys/{session_id}/translation", params={"version": 9}, headers=guest).status_code == 404
        assert client.get("/api/journeys/nope/translation", headers=guest).status_code == 404
