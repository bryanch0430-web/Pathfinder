"""Screening for a plan file that came from someone else.

An imported plan is stored and later quoted to the model, so every free-text field
is checked for text that tries to steer it or to smuggle markup, and every URL is
checked for a scheme the browser should never follow. A hit neutralises that one
field; it never rejects the file.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

PLACEHOLDER = "[removed: unsafe text]"
MAX_URL_CHARS = 2000
MAX_SUMMARY_CHARS = 4000
MAX_NAME_CHARS = 200
MAX_NOTE_CHARS = 1000
MAX_WARNINGS = 20

# Keys whose values (or, for `names`, whose children) are short labels.
_NAME_KEYS = {
    "name",
    "title",
    "label",
    "area",
    "city",
    "origin",
    "destination",
    "names",
    "operator",
    "source",
    "airline",
}

_INVISIBLE = re.compile("[­͏؜ᅟᅠ឴឵᠎​-‏‪-‮⁠-⁯ㅤ﻿ﾠ]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# Look-alike letters from other scripts that a model reads as Latin.
_CONFUSABLE = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "һ": "h",
        "ԁ": "d",
        "ο": "o",
        "ι": "i",
        "ν": "v",
        "α": "a",
        "ρ": "p",
        "κ": "k",
        "Α": "A",
        "Ε": "E",
        "Ο": "O",
        "Ι": "I",
        "Ν": "N",
        "Т": "T",
        "М": "M",
        "Н": "H",
        "В": "B",
        "К": "K",
        "С": "C",
        "Р": "P",
    }
)

_BENIGN_TOPIC = (
    r"(?:the\s+|my\s+|our\s+)?(?:hotel|flight|booking|room|restaurant|itinerary|trip|day|tour"
    r"|reservation|ticket|museum|hostel|train|transfer)"
)
_EVENT = (
    r"(?:load|error|click|dblclick|mouse\w+|focus\w*|blur|submit|change|input|key\w+|toggle|animation\w+"
    r"|pointer\w+|touch\w+|begin|abort|select|scroll|resize|wheel|drag\w*|drop|copy|cut|paste|message"
    r"|hashchange|pageshow|unload)"
)
_QUALIFIER = r"(?:system|hidden|initial|original|secret|developer)"

_UNSAFE = re.compile(
    r"(?:"
    # ignore / disregard the previous instructions
    r"\b(?:ignore|disregard|forget|override|overrule)\s+(?:all\s+|any\s+|of\s+|the\s+|your\s+|my\s+|these\s+|those\s+)*"
    r"(?:above|previous|prior|earlier|preceding|former|foregoing|system|developer)\s+"
    r"(?:instructions?|rules?|prompts?|directions?|guidelines?|messages?|context)\b"
    r"(?!\s+(?:about|regarding|concerning)\s+" + _BENIGN_TOPIC + r")"
    r"|\b(?:ignore|disregard|forget)\s+everything\s+(?:above|before|you\s+were\s+told)"
    r"|\b(?:ignore|disregard|forget|bypass|circumvent|override)\s+(?:all\s+|any\s+|of\s+)*(?:your|all|any)\s+(?:the\s+)?"
    r"(?:instructions?|rules|guardrails?|guidelines|restrictions|safety|safeguards|system\s+prompt)"
    r"|\bbypass\s+(?:the\s+)?(?:guardrails?|safety|safeguards)"
    # show me the system prompt
    r"|\b(?:show|reveal|print|repeat|output|display|leak|dump|disclose)\s+(?:me\s+|us\s+)?"
    r"(?:your\s+(?:" + _QUALIFIER + r"\s+)?(?:prompt|instructions|rules)"
    r"|the\s+" + _QUALIFIER + r"\s+(?:prompt|instructions|rules))\b"
    r"|\bsystem\s+prompt\b.{0,24}\b(?:above|verbatim|word\s+for\s+word)"
    # role and mode switches
    r"|\byou\s+are\s+now\b.{0,48}\b(?:developer\s+mode|dan\b|unrestricted|unfiltered|jailbr\w+)"
    r"|\bdeveloper\s+mode\b"
    r"|\b(?:enable|enter|activate|switch\s+to|start|use)\s+(?:a\s+)?(?:jailbreak|dan)\b"
    r"|\bjailbreak\s+(?:mode|prompt|yourself|the\s+(?:model|assistant|ai))\b"
    r"|\bact\s+as\s+(?:a\s+|an\s+|the\s+)?(?:developer|system|admin|administrator|root|dan)\b"
    r"|\b(?:router|system|safety|previous|above|prior|earlier)\s+(?:rules?|restrictions?|guardrails?|instructions?)"
    r"\s+(?:no\s+longer|do\s+not|don'?t|doesn'?t|does\s+not)\s+appl(?:y|ies)\b"
    r"|\bno\s+longer\s+(?:bound|restricted|constrained)\s+by\b.{0,24}\b(?:rules|instructions|guardrails)"
    r"|\bnew\s+(?:system\s+)?instructions?\s*:"
    r"|<\s*\|"
    r"|\[\s*/?\s*(?:inst|sys|system)\s*\]"
    r"|<\s*/?\s*(?:system|assistant|developer)\s*>"
    # markup and script
    r"|<\s*(?:script|svg|iframe|object|embed)\b"
    r"|\b(?:java|vb)script\s*:"
    r"|\bon" + _EVENT + r"\s*="
    r"|\bdata\s*:\s*text/html"
    # Chinese and Japanese
    r"|(?:忽略|无视|無視|忽视|忽視|不要理会|不要理會|別管|别管|忘记|忘記|不理|跳過|跳过)\s*(?:掉)?\s*"
    r"(?:以上|上述|之前|先前|前面|上面|以前|所有|全部|一切|你的|您的|任何|系統|系统)\s*(?:的)?\s*(?:所有|全部|一切)?\s*(?:的)?\s*"
    r"(?:指令|指示|提示|規則|规则|說明|说明|設定|设定|限制|命令)"
    r"|(?:以前|之前|先前|前面|上面|上記|前)の?\s*(?:指示|指令|命令|ルール|プロンプト)\s*(?:を|は)?\s*(?:無視|忽略|忘れ|无视)"
    r"|(?:顯示|显示|輸出|输出|告訴我|告诉我|透露|洩露|泄露|列印|打印|重複|重复|說出|说出|展示|給我看|给我看|公開|公开)\s*(?:出來|出来)?\s*"
    r"(?:你的|您的|你嘅|這個|这个|该|該)?\s*(?:系統|系统|隱藏|隐藏|初始|原始|內部|内部|開發者|开发者)\s*(?:的)?\s*"
    r"(?:提示詞|提示词|提示|指令|指示|規則|规则)"
    r"|(?:開發者|开发者|開發人員|开发人员)\s*模式"
    r"|(?:進入|进入|啟用|启用|開啟|开启)\s*(?:越獄|越狱)"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def strip_invisible(text: str) -> str:
    """Zero-width, bidi and control characters out; ordinary spacing and newlines stay."""
    return _CONTROL.sub("", _INVISIBLE.sub("", text))


def normalise(text: str) -> str:
    """What a model effectively reads: NFKC, look-alikes folded, invisibles gone, spaces collapsed."""
    folded = unicodedata.normalize("NFKC", strip_invisible(text)).translate(_CONFUSABLE)
    return re.sub(r"\s+", " ", folded).strip()


def is_unsafe(text: str) -> bool:
    if not text:
        return False
    return bool(_UNSAFE.search(normalise(text)))


def safe_url(value: Any) -> str:
    """The URL if a browser may follow it, else empty. http(s) or an own /api/images path only."""
    if not isinstance(value, str):
        return ""
    url = strip_invisible(value).strip()
    if not url or len(url) > MAX_URL_CHARS or re.search(r"\s", url):
        return ""
    if re.match(r"https?://[^/\s?#]+", url, re.IGNORECASE):
        return url
    if url.startswith("/api/images/") and ".." not in url:
        return url
    return ""


def clean_text(value: str, limit: int) -> tuple[str, bool]:
    """(text, was_neutralised). Over-long text is cut; unsafe text becomes the placeholder."""
    text = strip_invisible(value)[:limit]
    if is_unsafe(text):
        return PLACEHOLDER, True
    return text, False


def _limit(key: str, parent: str) -> int:
    if key == "summary":
        return MAX_SUMMARY_CHARS
    if key in _NAME_KEYS or parent in _NAME_KEYS:
        return MAX_NAME_CHARS
    return MAX_NOTE_CHARS


def screen_plan(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str], int]:
    """The plan dict with every string screened and every URL checked.

    Returns (plan, warnings, hits). Unsafe text becomes a placeholder, a URL that is
    not http(s) is emptied, and an image or link left without a URL is dropped.
    """
    warnings: list[str] = []
    hits = 0

    def note(path: str, what: str) -> None:
        nonlocal hits
        hits += 1
        if len(warnings) < MAX_WARNINGS:
            warnings.append(f"{path}: {what}")

    def walk(value: Any, key: str, parent: str, path: str) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for child_key, child in value.items():
                name = str(child_key)
                where = f"{path}.{name}" if path else name
                if (name == "url" or name == "link" or name.endswith("_url")) and not isinstance(child, (dict, list)):
                    cleaned = safe_url(child)
                    if isinstance(child, str) and child.strip() and not cleaned:
                        note(where, "link removed (not http or https)")
                    out[name] = cleaned
                else:
                    out[name] = walk(child, name, key, where)
            if key == "image" and "url" in out and not out["url"]:
                return None
            return out
        if isinstance(value, list):
            items = [walk(item, key, parent, f"{path}[{i}]") for i, item in enumerate(value)]
            if key == "links":
                items = [item for item in items if not (isinstance(item, dict) and not item.get("url"))]
            return items
        if isinstance(value, str):
            cleaned, bad = clean_text(value, _limit(key, parent))
            if bad:
                note(path or key, "unsafe text removed")
            return cleaned
        return value

    screened = walk(raw, "", "", "")
    return (screened if isinstance(screened, dict) else {}), warnings, hits
