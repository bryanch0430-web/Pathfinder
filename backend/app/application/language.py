"""Plan display language: the set_language, unsupported, refresh and Chinese-input turns.

Language is a display setting, not a plan edit. These rules decide the turn
before the UI selection, the understanding step or any model is consulted, so
"Make it in Traditional Chinese" with a stop selected never becomes a stop edit
and never calls a planner, a tool or the writer.

Two separate enum slots come out of a message: ``language`` (en, zh-Hant,
other) here, and ``cuisine`` in the modify path. A language word followed by a
food word ("a Chinese restaurant", "traditional Chinese food") is a cuisine,
never a language; ``strip_language_phrases`` removes language phrases before
the cuisine slot is read, so "Traditional Chinese" never fills it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SUPPORTED_LOCALES: tuple[str, ...] = ("en", "zh-Hant")

LanguageCode = Literal["en", "zh-Hant", "other"]

_FOOD_AFTER = (
    r"(?:\s+style)?\s+(?:restaurants?|food|foods|cuisine|places?|meals?|dinners?|lunch(?:es)?|"
    r"breakfasts?|brunch|dish(?:es)?|eatery|eateries|cafes?|café|bbq|barbecue|hot\s?pot|noodles?|"
    r"dumplings?|dim\s?sum|tea|bakery|buffet|spots?|joints?|options?|kitchen|takeaway|street food|snacks?)\b"
)

# (pattern, code, display name). Longer phrases come first so "traditional
# chinese" wins over "chinese".
_ENGLISH_NAMES: list[tuple[str, LanguageCode, str]] = [
    (r"(?:traditional|hong kong|hk|cantonese)\s+chinese", "zh-Hant", "Traditional Chinese"),
    (r"simplified\s+chinese", "other", "Simplified Chinese"),
    (r"mandarin", "other", "Mandarin"),
    (r"chinese", "zh-Hant", "Traditional Chinese"),
    (r"cantonese", "zh-Hant", "Traditional Chinese"),
    (r"english", "en", "English"),
    (r"japanese", "other", "Japanese"),
    (r"korean", "other", "Korean"),
    (r"french", "other", "French"),
    (r"german", "other", "German"),
    (r"spanish", "other", "Spanish"),
    (r"italian", "other", "Italian"),
    (r"portuguese", "other", "Portuguese"),
    (r"thai", "other", "Thai"),
    (r"vietnamese", "other", "Vietnamese"),
    (r"indonesian", "other", "Indonesian"),
    (r"malay", "other", "Malay"),
    (r"russian", "other", "Russian"),
    (r"arabic", "other", "Arabic"),
    (r"hindi", "other", "Hindi"),
    (r"dutch", "other", "Dutch"),
    (r"tagalog|filipino", "other", "Filipino"),
]

_CHINESE_NAMES: list[tuple[str, LanguageCode, str]] = [
    (r"(?:简体|簡體|简体|簡体)(?:中文|字)?|简中|簡中|普通話|普通话", "other", "Simplified Chinese"),
    (r"(?:繁體|繁体|正體|正体)(?:中文|字)?|繁中|廣東話|广东话|粵語|粤语|中文|華語", "zh-Hant", "Traditional Chinese"),
    (r"英文|英語|英语", "en", "English"),
    (r"日文|日語|日语", "other", "Japanese"),
    (r"韓文|韩文|韓語|韩语", "other", "Korean"),
    (r"法文|法語|法语", "other", "French"),
    (r"德文|德語|德语", "other", "German"),
    (r"西班牙文|西班牙語", "other", "Spanish"),
]

_EXPLICIT_MARKER = re.compile(
    r"\b(translat\w*|languages?|version|traditional chinese|simplified chinese|locale)\b", re.IGNORECASE
)
_DISPLAY_VERB = re.compile(
    r"\b(show|make|switch|change|display|give|write|put|view|see|read|present|render|set|turn|"
    r"convert|back|use|want|prefer|speak|reply|answer|respond|can|could|would|please|go)\b",
    re.IGNORECASE,
)
_FRAME = r"\b(?:in|into|to)\s+(?:the\s+)?(?:an?\s+)?"
_MEAL_WORD = re.compile(
    r"\b(restaurants?|food|cuisine|meals?|dinner|lunch|breakfast|brunch|eat|eating|dish(?:es)?|cafe|"
    r"café|dim sum|sushi|ramen|noodles?)\b",
    re.IGNORECASE,
)
_CHINESE_VERB = re.compile(r"[用轉转改換换顯显示睇看變变切譯译版返回要講讲寫写]")
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


@dataclass(frozen=True)
class LanguageRequest:
    """The ``language`` slot of a set_language turn."""

    language: LanguageCode
    name: str

    @property
    def supported(self) -> bool:
        return self.language in SUPPORTED_LOCALES


#: what may follow a framed language word: "in Japanese", "to English please", "into French."
_FRAME_END = (
    r"(?=\s*(?:$|[.,!?;:)]|please\b|pls\b|now\b|instead\b|again\b|too\b|version\b|translation\b|"
    r"language\b|text\b|for\b|thanks\b|thank\b|and\b|so\b|if\b))"
)


def _mentions(text: str) -> list[tuple[int, LanguageCode, str, bool]]:
    """(position, code, name, framed) for every language word not used as a cuisine."""
    lower = text.lower()
    found: list[tuple[int, LanguageCode, str, bool]] = []
    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        if any(start < t_end and end > t_start for t_start, t_end in taken):
            return False
        taken.append((start, end))
        return True

    for pattern, code, name in _ENGLISH_NAMES:
        for match in re.finditer(rf"\b(?:{pattern})\b(?!{_FOOD_AFTER})", lower):
            if not free(*match.span()):
                continue
            framed = bool(re.search(rf"{_FRAME}$", lower[: match.start()])) and bool(
                re.match(_FRAME_END, lower[match.end():])
            )
            found.append((match.start(), code, name, framed))
    for pattern, code, name in _CHINESE_NAMES:
        for match in re.finditer(pattern, text):
            if not free(*match.span()):
                continue
            framed = bool(re.search(r"(做|成|為|为|到|返|回|用|去)\s*$", text[: match.start()]))
            found.append((match.start(), code, name, framed))
    return sorted(found)


def language_slot(message: str) -> LanguageRequest | None:
    """The language a display-language request asks for, or None when the message is not one.

    A language word only counts inside a language frame: "translate ...",
    "... version", "in/into/to <language>" with a display verb, a Chinese
    language word with a display verb, or a short message that is just the
    language ("繁體中文", "English please"). A language word followed by a food
    word is a cuisine and never counts.
    """
    text = (message or "").strip()
    if not text or len(text) > 240:
        return None
    lower = text.lower()

    mentions = _mentions(text)
    if not mentions:
        return None

    words = re.findall(r"[a-z']+", lower)
    has_cjk = bool(_CJK.search(text))
    explicit = bool(_EXPLICIT_MARKER.search(lower)) or bool(re.search(r"翻譯|翻译|語言|语言|版本", text))
    framed_any = any(framed for *_, framed in mentions)
    short = len(words) + len(_CJK.findall(text)) <= 6
    language_words_only = not re.sub(
        r"(?i)\b(traditional|simplified|hong kong|hk|chinese|english|cantonese|please|pls|thanks|"
        r"thank you|in|to|version|mode|ok|okay|now|instead|back)\b|[^a-z]",
        "",
        lower,
    )
    chinese_frame = has_cjk and bool(_CHINESE_VERB.search(text))

    is_request = (
        explicit
        or (framed_any and (short or bool(_DISPLAY_VERB.search(lower))) and not _MEAL_WORD.search(lower))
        or chinese_frame
        or (short and (language_words_only or has_cjk))
    )
    if not is_request:
        return None
    framed = [mention for mention in mentions if mention[3]]
    _, code, name, _ = (framed or mentions)[-1]
    return LanguageRequest(language=code, name=name)


def strip_language_phrases(message: str) -> str:
    """The message without language phrases, for reading the cuisine slot."""
    if language_slot(message) is None:
        return message
    text = message
    for pattern, _, _ in _ENGLISH_NAMES:
        # A language word followed by a food word ("a Chinese restaurant") stays.
        text = re.sub(rf"(?i)\b(?:{pattern})\b(?!{_FOOD_AFTER})", " ", text)
    return text


_CUISINE = re.compile(
    r"\b(japanese|sushi|ramen|chinese|korean|thai|italian|indian|french|vietnamese)\b", re.IGNORECASE
)


def cuisine_slot(message: str) -> str | None:
    """The ``cuisine`` slot. A language phrase ("in Traditional Chinese") never fills it."""
    match = _CUISINE.search(strip_language_phrases(message))
    return match.group(1).lower() if match else None


_REFRESH = re.compile(
    r"\b(?:refresh|re-?fetch|reload|re-?check)\b.*\b(?:stale|outdated|out[- ]of[- ]date|old|data|info|"
    r"information|prices?|fares?|forecast|weather|plan|itinerary|details?|sources?|status)\b|"
    r"\bupdate\b.*\b(?:stale|outdated|out[- ]of[- ]date)\b|"
    r"\b(?:refresh|re-?fetch)\b\s*(?:it|this|everything|all)?\s*(?:please|now)?\s*[.!]?$|"
    r"更新|刷新",
    re.IGNORECASE,
)


def is_refresh_request(message: str) -> bool:
    """"Refresh the stale data": re-fetch the outdated sections, nothing else."""
    text = (message or "").strip()
    return bool(text) and len(text) <= 200 and bool(_REFRESH.search(text))


def mainly_chinese(message: str) -> bool:
    """At least two Chinese characters, and they outnumber the Latin letters."""
    text = message or ""
    cjk = len(_CJK.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return cjk >= 2 and cjk >= latin


# --- fixed replies -----------------------------------------------------------

DISCLAIMER = {
    "en": (
        "This Traditional Chinese version was translated by an AI model and may contain errors. "
        "The English version is the original. Please check key details such as dates, times and "
        "prices against it."
    ),
    "zh-Hant": "此繁體中文版本由 AI 模型翻譯，可能有錯誤。英文版本為原文，請以英文版本核對日期、時間及價格等重要資料。",
}

CHINESE_INPUT_REPLY = {
    "en": "Please type in English for now, but I can show the plan in Traditional Chinese.",
    "zh-Hant": "暫時請用英文輸入，但我可以用繁體中文顯示行程。",
}


def set_language_reply(locale: str, *, changed: bool) -> str:
    if locale == "zh-Hant":
        lead = "已將行程改為以繁體中文顯示。" if changed else "行程已經以繁體中文顯示。"
        return lead + DISCLAIMER["zh-Hant"] + "行程內容沒有改動。"
    lead = "The plan is now shown in English." if changed else "The plan is already shown in English."
    return lead + " Nothing in the plan changed."


def unsupported_reply(name: str, locale: str) -> str:
    if locale == "zh-Hant":
        return (
            f"暫時未能以{_ZH_NAMES.get(name, name)}顯示行程。支援的語言：英文（English）及繁體中文。"
        )
    return (
        f"I can't show the plan in {name} yet. Supported languages: English and Traditional Chinese "
        "(繁體中文)."
    )


_ZH_NAMES = {
    "Simplified Chinese": "簡體中文",
    "Mandarin": "普通話",
    "Japanese": "日文",
    "Korean": "韓文",
    "French": "法文",
    "German": "德文",
    "Spanish": "西班牙文",
    "Italian": "意大利文",
    "Thai": "泰文",
    "Vietnamese": "越南文",
}


_ZH_SECTIONS = {"weather": "天氣預報", "tickets": "機票價格", "hotels": "酒店價格"}


def chinese_input_reply(locale: str) -> str:
    if locale == "zh-Hant":
        return CHINESE_INPUT_REPLY["en"] + "\n" + CHINESE_INPUT_REPLY["zh-Hant"]
    return CHINESE_INPUT_REPLY["en"]


def refresh_reply(locale: str, *, has_plan: bool, refreshed: list[str], outdated: list[str], labels: dict[str, str]) -> str:
    zh = locale == "zh-Hant"
    if not has_plan:
        return "未有行程可以更新。" if zh else "There is no plan yet, so there is nothing to refresh."
    if refreshed:
        if zh:
            names = "、".join(_ZH_SECTIONS.get(section, section) for section in refreshed)
            return f"已重新取得過時的資料：{names}。"
        parts = [labels.get(section, section) for section in refreshed]
        names = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        return f"Refreshed the outdated {names}."
    if outdated:
        return (
            "部分資料已過時，但來源暫時未能提供新資料，現保留原有資料。"
            if zh
            else "Some data is outdated, but the sources could not be reached again; the earlier data is kept."
        )
    return "行程中的資料仍在有效期內，毋須更新。" if zh else "Every section of the plan is within its freshness window; nothing needed refreshing."
