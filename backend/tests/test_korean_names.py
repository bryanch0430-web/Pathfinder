"""Korean place names in English text: Revised Romanization, with the English place type
Korea's own English signage adds ("Gyeongbokgung Palace", "Haeundae Beach")."""

import pytest

from app.domain.korean_names import english_place_name, romanize
from app.domain.place_names import english_name


@pytest.mark.parametrize(
    ("hangul", "roman"),
    [
        ("대전", "Daejeon"),
        ("부산", "Busan"),
        ("서울", "Seoul"),
        ("해운대", "Haeundae"),
        ("경복궁", "Gyeongbokgung"),
        ("불국사", "Bulguksa"),
        ("한국어", "Hangugeo"),  # a final consonant carries over to a following vowel
        ("백마", "Baengma"),  # k before m sounds ng
        ("종로", "Jongno"),  # r after ng sounds n
        ("독립문", "Dongnimmun"),
        ("신라", "Silla"),  # n before r sounds l
        ("설날", "Seollal"),
        ("묵호", "Mukho"),  # no aspiration in nouns
        ("해돋이", "Haedoji"),
        ("송림마을", "Songnimmaeul"),
        ("김밥명품", "Gimbammyeongpum"),
        ("대전 중구", "Daejeon Junggu"),
    ],
)
def test_hangul_is_romanized_by_the_revised_romanization_rules(hangul: str, roman: str) -> None:
    assert romanize(hangul) == roman


@pytest.mark.parametrize(
    ("name", "english"),
    [
        ("상소동산림욕장", "Sangsodong Forest Bathing Area"),
        ("해운대해수욕장", "Haeundae Beach"),
        ("물과학체험장", "Mulgwahak Experience Center"),
        ("남대문시장", "Namdaemun Market"),
        ("대전역", "Daejeon Station"),
        ("경복궁", "Gyeongbokgung Palace"),
        ("목척교", "Mokcheokgyo Bridge"),
        ("계룡문", "Gyeryongmun Gate"),
        ("불국사", "Bulguksa Temple"),
        ("스카이로드", "Sky Road"),
        ("부산", "Busan"),  # a two-syllable name keeps no guessed place type
        ("김밥명품", "Gimbammyeongpum"),
    ],
)
def test_a_korean_place_gets_an_english_name(name: str, english: str) -> None:
    assert english_place_name(name) == english


def test_names_in_other_scripts_are_left_as_written() -> None:
    assert english_place_name("Cinema Street") == "Cinema Street"
    assert english_place_name("東京タワー") == "東京タワー"
    assert english_place_name("") == ""


def test_the_source_english_name_wins_over_romanization() -> None:
    assert english_name("계룡문", {"en": "Gyeryong Gate Square"}) == "Gyeryong Gate Square"
    assert english_name("계룡문", {}) == "Gyeryongmun Gate"
    assert english_name("東京タワー", None) == "東京タワー"
