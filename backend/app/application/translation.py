"""Display translation of a plan (en -> zh-Hant, Hong Kong conventions) with a fact check.

The English plan stays the single source of truth: this module only produces overlay
text for the display fields (summary, day titles and notes, stop notes, data-gap
messages). Numbers, times, dates, prices, URLs, ids and place names are never taken from
the translation; place names and URLs are hidden behind placeholders before the model
sees the text, and every translated field is checked against its English original. Any
failure returns ``status="fallback"`` with empty fields, so the UI shows the English plan.

Cache: in-process LRU (not SQLite). Plan-level results are keyed on (session id, trip id,
plan version, locale, English-text fingerprint); field translations are keyed on
(locale, sha256 of the English text), so after an edit only fields whose English text
changed go to the model again.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import unicodedata
from collections import Counter, OrderedDict
from typing import Any, Protocol

from app.domain.models import TripPlan

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES = ("en", "zh-Hant")

DISCLAIMERS = {
    "en": (
        "This Traditional Chinese version was translated by an AI model and may contain errors. "
        "The English version is the original. Please check key details such as dates, times and "
        "prices against it."
    ),
    "zh-Hant": "此繁體中文版本由 AI 模型翻譯，可能有錯誤。英文版本為原文，請以英文版本核對日期、時間及價格等重要資料。",
}

#: Fixed Hong Kong wording for recurring plan terms; sent with every request.
GLOSSARY: dict[str, str] = {
    "HKD": "HKD (keep the code; never 港元/港幣/元)",
    "unavailable": "暫時未能提供",
    "outdated": "資料過時",
    "stale": "資料過時",
    "refreshed": "已更新",
    "hours unknown": "開放時間不詳",
    "opening hours": "開放時間",
    "check-in": "辦理入住",
    "check-out": "退房",
    "hotel": "酒店",
    "taxi": "的士",
    "bus": "巴士",
    "minibus": "小巴",
    "MTR": "港鐵",
    "metro": "地鐵",
    "walk": "步行",
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "booking": "預訂",
    "reservation": "預約",
    "source": "資料來源",
    "data gap": "資料缺漏",
    "typhoon": "颱風",
    "itinerary": "行程",
}

#: Glossary terms the fact check enforces: an English field using the term must carry
#: one of these renderings in its translation.
REQUIRED_TERMS: dict[str, tuple[str, ...]] = {
    "unavailable": ("暫時未能提供", "未能提供"),
    "outdated": ("過時",),
    "stale": ("過時",),
}

CHUNK = 40

_URL = re.compile(r"https?://[^\s)\]}>\"'，。、]+")
_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_CURRENCY = re.compile(
    r"(?<![A-Za-z])(?:HKD|USD|JPY|KRW|CNY|RMB|EUR|GBP|TWD|MOP|SGD|THB|AUD|CAD|NZD|MYR|VND|PHP|IDR|INR|CHF)(?![A-Za-z])"
    r"|[$¥₩€£]"
)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_PLACEHOLDER = re.compile(r"\{\{\s*([PU]\d+)\s*\}\}")


class Translator(Protocol):
    @property
    def available(self) -> bool: ...

    async def translate(
        self, segments: list[dict[str, str]], *, locale: str, glossary: dict[str, str]
    ) -> list[Any]: ...


class LLMTranslator:
    """The writer model behind the guarded gateway; offline when no key is configured."""

    def __init__(self, gateway: Any):
        self.gateway = gateway

    @property
    def available(self) -> bool:
        return bool(getattr(self.gateway, "available", False))

    async def translate(
        self, segments: list[dict[str, str]], *, locale: str, glossary: dict[str, str]
    ) -> list[Any]:
        payload = {"locale": locale, "glossary": glossary, "segments": segments}
        data = await self.gateway.json(
            "translate", user=json.dumps(payload, ensure_ascii=False), purpose="translate"
        )
        rows = data.get("segments") if isinstance(data, dict) else None
        return rows if isinstance(rows, list) else []


class _LRU:
    def __init__(self, size: int):
        self.size = size
        self.data: OrderedDict[Any, Any] = OrderedDict()

    def get(self, key: Any) -> Any:
        if key not in self.data:
            return None
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key: Any, value: Any) -> None:
        self.data[key] = value
        self.data.move_to_end(key)
        while len(self.data) > self.size:
            self.data.popitem(last=False)


def _empty_fields() -> dict[str, Any]:
    return {"summary": "", "days": {}, "items": {}, "gaps": {}}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def english_segments(plan: TripPlan) -> list[tuple[str, tuple[str, ...], str]]:
    """(segment id, where it goes in ``fields``, English text) in plan order."""
    out: list[tuple[str, tuple[str, ...], str]] = []
    if plan.summary.strip():
        out.append(("summary", ("summary",), plan.summary))
    for day in plan.days:
        stamp = day.date.isoformat()
        if day.title.strip():
            out.append((f"day:{stamp}:title", ("days", stamp, "title"), day.title))
        if day.note.strip():
            out.append((f"day:{stamp}:note", ("days", stamp, "note"), day.note))
        for item in day.items:
            if item.notes.strip():
                out.append((f"item:{item.id}:notes", ("items", item.id, "notes"), item.notes))
    for index, gap in enumerate(plan.data_quality.gaps):
        if gap.message.strip():
            out.append((f"gap:{index}", ("gaps", str(index)), gap.message))
    return out


def protected_names(plan: TripPlan) -> list[str]:
    """Strings the model must not touch: stop, hotel and destination names (with their
    trusted localized forms)."""
    values: set[str] = set()
    if plan.destination.name.strip():
        values.add(plan.destination.name.strip())
    for day in plan.days:
        for item in day.items:
            values.add(item.name.strip())
            values.update(value.strip() for value in item.names.values())
    for hotel in plan.hotels:
        values.add(hotel.name.strip())
    return sorted((value for value in values if len(value) >= 2), key=len, reverse=True)


def _protect(text: str, names: list[str], table: dict[str, str]) -> str:
    """Swap URLs and place names for {{U#}} / {{P#}} placeholders (``table`` maps back)."""
    reverse = {value: key for key, value in table.items()}

    def token(value: str, prefix: str) -> str:
        if value not in reverse:
            key = f"{prefix}{sum(1 for k in table if k.startswith(prefix)) + 1}"
            table[key] = value
            reverse[value] = key
        return "{{" + reverse[value] + "}}"

    text = _URL.sub(lambda match: token(match.group(0), "U"), text)
    for name in names:
        pattern = re.compile(r"(?<![\w{])" + re.escape(name) + r"(?![\w}])")
        text = pattern.sub(lambda match: token(match.group(0), "P"), text)
    return text


def _restore(text: str, table: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda match: table.get(match.group(1), match.group(0)), text)


def facts(text: str) -> dict[str, Counter]:
    """The checkable facts in a text: URLs, dates, times, currency markers and numbers."""
    rest = unicodedata.normalize("NFKC", text or "")
    urls = _URL.findall(rest)
    rest = _URL.sub(" ", rest)
    dates = _DATE.findall(rest)
    rest = _DATE.sub(" ", rest)
    times = [f"{int(hour):02d}:{minute}" for hour, minute in _TIME.findall(rest)]
    rest = _TIME.sub(" ", rest)
    currency = [value.replace("RMB", "CNY") for value in _CURRENCY.findall(rest)]
    numbers = [value.replace(",", "") for value in _NUMBER.findall(rest)]
    return {
        "urls": Counter(urls),
        "dates": Counter(dates),
        "times": Counter(times),
        "currency": Counter(currency),
        "numbers": Counter(numbers),
    }


def fact_check(english: str, translated: str) -> str:
    """'' when the translation carries exactly the English facts, else what differs."""
    source, target = facts(english), facts(translated)
    for kind in ("urls", "dates", "times", "currency", "numbers"):
        if source[kind] != target[kind]:
            missing = sorted((source[kind] - target[kind]).elements())
            extra = sorted((target[kind] - source[kind]).elements())
            return f"{kind} differ (missing {missing}, extra {extra})"
    return ""


def glossary_check(english: str, translated: str) -> str:
    for term, renderings in REQUIRED_TERMS.items():
        if re.search(rf"\b{re.escape(term)}\b", english, flags=re.IGNORECASE) and not any(
            rendering in translated for rendering in renderings
        ):
            return f"glossary term {term!r} not rendered as {renderings[0]}"
    return ""


class TranslationService:
    def __init__(self, translator: Translator, *, plan_cache_size: int = 128, field_cache_size: int = 4096):
        self.translator = translator
        self.plans = _LRU(plan_cache_size)
        self.fields = _LRU(field_cache_size)

    def _response(
        self, locale: str, plan: TripPlan, status: str, reason: str = "", fields: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "locale": locale,
            "plan_version": plan.version,
            "status": status,
            "reason": reason,
            "disclaimer": "" if status == "english" else DISCLAIMERS.get(locale, DISCLAIMERS["en"]),
            "fields": fields if fields is not None else _empty_fields(),
        }

    async def translate_plan(self, plan: TripPlan, locale: str) -> dict[str, Any]:
        if locale == "en":
            return self._response(locale, plan, "english")
        if locale not in SUPPORTED_LOCALES:
            raise ValueError(f"unsupported locale {locale!r}")
        segments = english_segments(plan)
        fingerprint = _sha(json.dumps([(seg_id, text) for seg_id, _, text in segments], ensure_ascii=False))
        plan_key = (plan.session_id, plan.trip_id, plan.version, locale, fingerprint)
        cached = self.plans.get(plan_key)
        if cached is not None:
            return copy.deepcopy(cached)

        names = protected_names(plan)
        translations: dict[str, str] = {}
        # English-text hash -> (segment id, English, text with placeholders, placeholder table)
        pending: dict[str, tuple[str, str, str, dict[str, str]]] = {}
        for seg_id, _, english in segments:
            digest = _sha(english)
            hit = self.fields.get((locale, digest))
            if hit is not None:
                translations[digest] = hit
            elif digest not in pending:
                table: dict[str, str] = {}
                pending[digest] = (seg_id, english, _protect(english, names, table), table)

        if pending and not self.translator.available:
            return self._response(
                locale, plan, "unavailable", "No translation model is configured; showing the English plan."
            )

        failures: list[str] = []
        digests = list(pending)
        for start in range(0, len(digests), CHUNK):
            chunk = digests[start : start + CHUNK]
            # Segment ids carry the stop ids, so a dropped, added or reordered stop shows.
            request = [{"id": pending[digest][0], "text": pending[digest][2]} for digest in chunk]
            try:
                rows = await self.translator.translate(request, locale=locale, glossary=GLOSSARY)
            except Exception as exc:  # a provider failure is a fallback, never a broken plan
                logger.warning("translation failed: %s", exc)
                return self._response(locale, plan, "fallback", f"The translator failed ({type(exc).__name__}).")
            returned = [row.get("id") if isinstance(row, dict) else None for row in rows]
            if returned != [entry["id"] for entry in request]:
                failures.append("segment count, order or ids changed")
                break
            for digest, entry, row in zip(chunk, request, rows):
                _, english, protected_text, table = pending[digest]
                text = row.get("text")
                if not isinstance(text, str) or (english.strip() and not text.strip()):
                    failures.append(f"{entry['id']}: empty translation")
                    continue
                if Counter(_PLACEHOLDER.findall(protected_text)) != Counter(_PLACEHOLDER.findall(text)):
                    failures.append(f"{entry['id']}: place names or URLs changed")
                    continue
                restored = _restore(text, table)
                problem = fact_check(english, restored) or glossary_check(english, restored)
                if problem:
                    failures.append(f"{entry['id']}: {problem}")
                    continue
                translations[digest] = restored
                self.fields.put((locale, digest), restored)

        if failures:
            result = self._response(
                locale, plan, "fallback", "Fact check failed: " + "; ".join(failures[:3])
            )
            self.plans.put(plan_key, result)
            return copy.deepcopy(result)

        fields = _empty_fields()
        for _, path, english in segments:
            text = translations[_sha(english)]
            if path[0] == "summary":
                fields["summary"] = text
            elif path[0] == "gaps":
                fields["gaps"][path[1]] = text
            else:
                fields[path[0]].setdefault(path[1], {})[path[2]] = text
        result = self._response(locale, plan, "translated", fields=fields)
        self.plans.put(plan_key, result)
        return copy.deepcopy(result)
