"""Korean place names in English text, written by rule rather than by a model.

Hangul is romanized with the Revised Romanization of Korean (the system on Korean road
signs), including its sound changes between syllables. A name ending in a common place
type gets that type in English, as Korea's own English signage does: 해운대해수욕장 is
"Haeundae Beach" and 경복궁 is "Gyeongbokgung Palace".
"""

from __future__ import annotations

import re

_INITIALS = ("g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h")
_VOWELS = (
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae",
    "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
)  # fmt: skip
_FINALS = (
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l",
    "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t",
)  # fmt: skip
#: A final consonant before a vowel moves over to it: (what stays, what moves).
_LIAISON = {
    1: ("", "g"), 2: ("", "kk"), 3: ("k", "s"), 4: ("", "n"), 5: ("n", "j"), 6: ("", "n"),
    7: ("", "d"), 8: ("", "r"), 9: ("l", "g"), 10: ("l", "m"), 11: ("l", "b"), 12: ("l", "s"),
    13: ("l", "t"), 14: ("l", "p"), 15: ("", "r"), 16: ("", "m"), 17: ("", "b"), 18: ("p", "s"),
    19: ("", "s"), 20: ("", "ss"), 22: ("", "j"), 23: ("", "ch"), 24: ("", "k"), 25: ("", "t"),
    26: ("", "p"), 27: ("", ""),
}  # fmt: skip
#: Finals that sound ng, n or m before n or m (백마 baengma, 독립문 dongnimmun).
_NASAL = {
    **{final: "ng" for final in (1, 2, 3, 9, 24)},
    **{final: "n" for final in (7, 19, 20, 22, 23, 25, 27)},
    **{final: "m" for final in (14, 17, 18, 26)},
}
_L_FINALS = {8, 11, 12, 13, 15}
_N, _R, _M, _SILENT = 2, 5, 6, 11
_FINAL_D, _FINAL_T, _FINAL_NG, _VOWEL_I = 7, 25, 21, 20

#: Place types Korea's English signage translates, replacing the Korean word (longest first).
_PLACE_TYPES = (
    ("자연휴양림", "Natural Recreation Forest"),
    ("해수욕장", "Beach"),
    ("산림욕장", "Forest Bathing Area"),
    ("초등학교", "Elementary School"),
    ("고등학교", "High School"),
    ("전통시장", "Traditional Market"),
    ("휴양림", "Recreation Forest"),
    ("수목원", "Arboretum"),
    ("식물원", "Botanical Garden"),
    ("동물원", "Zoo"),
    ("박물관", "Museum"),
    ("미술관", "Art Museum"),
    ("기념관", "Memorial Hall"),
    ("과학관", "Science Museum"),
    ("전시관", "Exhibition Hall"),
    ("체험관", "Experience Center"),
    ("체험장", "Experience Center"),
    ("전망대", "Observatory"),
    ("놀이터", "Playground"),
    ("도서관", "Library"),
    ("대학교", "University"),
    ("중학교", "Middle School"),
    ("저수지", "Reservoir"),
    ("백화점", "Department Store"),
    ("정류장", "Bus Stop"),
    ("터미널", "Terminal"),
    ("공원", "Park"),
    ("시장", "Market"),
    ("쉼터", "Rest Area"),
    ("거리", "Street"),
    ("마을", "Village"),
    ("성당", "Cathedral"),
    ("교회", "Church"),
    ("폭포", "Falls"),
    ("호수", "Lake"),
    ("계곡", "Valley"),
    ("해변", "Beach"),
    ("광장", "Plaza"),
    ("식당", "Restaurant"),
    ("시청", "City Hall"),
    ("온천", "Hot Spring"),
    ("타워", "Tower"),
    ("센터", "Center"),
)
#: One-syllable place types: the whole name stays romanized and the type is added
#: ("Gyeongbokgung Palace"); station names drop the syllable ("Daejeon Station").
_KEPT_TYPES = {"궁": "Palace", "교": "Bridge", "문": "Gate", "강": "River", "사": "Temple"}
_DROPPED_TYPES = {"역": "Station"}
#: English loanwords written in Hangul, used when a word is made of nothing else.
_LOANWORDS = {
    "스카이": "Sky", "로드": "Road", "타워": "Tower", "파크": "Park", "랜드": "Land", "월드": "World",
    "센터": "Center", "카페": "Cafe", "호텔": "Hotel", "플라자": "Plaza", "스퀘어": "Square",
    "시티": "City", "아쿠아리움": "Aquarium", "스트리트": "Street", "하우스": "House", "마켓": "Market",
    "비치": "Beach", "가든": "Garden", "아트": "Art", "뮤지엄": "Museum", "리조트": "Resort",
    "시네마": "Cinema", "몰": "Mall",
}  # fmt: skip
_HANGUL = re.compile(r"[가-힣]")


def _syllable(char: str) -> tuple[int, int, int] | None:
    code = ord(char) - 0xAC00
    if not 0 <= code < 11172:
        return None
    return code // 588, (code % 588) // 28, code % 28


def _boundary(final: int, initial: int, vowel: int) -> tuple[str, str]:
    """The romanized final of one syllable and initial of the next, written together."""
    if final == 0:
        return "", _INITIALS[initial]
    if initial == _SILENT:
        if final == _FINAL_NG:
            return "ng", ""
        if vowel == _VOWEL_I and final in (_FINAL_D, _FINAL_T):  # 해돋이 haedoji
            return "", "j" if final == _FINAL_D else "ch"
        return _LIAISON[final]
    if initial == _R:
        if final == 4 or final in _L_FINALS:  # 신라 silla
            return "l", "l"
        initial = _N  # 종로 jongno, 독립 dongnip
    if initial == _N and final in _L_FINALS:  # 설날 seollal
        return "l", "l"
    if initial in (_N, _M) and final in _NASAL:
        return _NASAL[final], _INITIALS[initial]
    return _FINALS[final], _INITIALS[initial]


def romanize(text: str) -> str:
    """Hangul in ``text`` by the Revised Romanization rules; other characters are kept."""
    parts = [_syllable(char) for char in text]
    initials: list[str | None] = [None] * len(text)
    finals: list[str] = [""] * len(text)
    for index, part in enumerate(parts):
        if part is None:
            continue
        if initials[index] is None:
            initials[index] = _INITIALS[part[0]]
        following = parts[index + 1] if index + 1 < len(parts) else None
        if following is None:
            finals[index] = _FINALS[part[2]]
        else:
            finals[index], initials[index + 1] = _boundary(part[2], following[0], following[1])
    out = "".join(
        char if part is None else f"{initials[index]}{_VOWELS[part[1]]}{finals[index]}"
        for index, (char, part) in enumerate(zip(text, parts))
    )
    return re.sub(r"(^|\s)([a-z])", lambda match: match.group(1) + match.group(2).upper(), out)


def _loanword(word: str) -> str | None:
    """"Sky Road" for 스카이로드: a word made only of known loanwords, else None."""
    if not word:
        return ""
    for size in range(len(word), 0, -1):
        english = _LOANWORDS.get(word[:size])
        rest = _loanword(word[size:]) if english else None
        if english and rest is not None:
            return f"{english} {rest}".strip()
    return None


def _words(text: str) -> str:
    return " ".join(_loanword(word) or romanize(word) for word in text.split())


def english_place_name(name: str) -> str:
    """An English rendering of a Korean place name; a name with no Hangul is returned as is."""
    name = (name or "").strip()
    if not _HANGUL.search(name):
        return name
    for korean, english in _PLACE_TYPES:
        specific = name[: -len(korean)].strip()
        if name.endswith(korean) and specific:
            return f"{_words(specific)} {english}"
    syllables = len(_HANGUL.findall(name))
    if syllables >= 3 and name[-1] in _KEPT_TYPES:
        return f"{_words(name)} {_KEPT_TYPES[name[-1]]}"
    if syllables >= 3 and name[-1] in _DROPPED_TYPES:
        return f"{_words(name[:-1])} {_DROPPED_TYPES[name[-1]]}"
    return _words(name)
