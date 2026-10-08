"""Injection blocklist screen.

Proposal: "A blocklist screens for prompt overrides, requests for hidden instructions, and
smuggled tool calls." Screening FLAGS a message; it never drops or rewrites it. The message is
still passed on as fenced trip data (see fence.py), never as an instruction. A flag only feeds
the aggregate audit log (audit.py) and lets callers raise their guard, so a false positive costs
one counter increment, not a lost trip request. That is why the patterns catch the phrasing
attackers actually use while still leaving ordinary trip talk alone ("ignore the museum, plan
more food stops", "what route should we take", "act fast, tickets sell out", "Canary Islands").

Text is normalised before matching: Unicode NFKC (folds full-width and other compatibility
characters to plain ASCII), casefold, zero-width / bidi / soft-hyphen characters removed,
whitespace collapsed. Chinese patterns are additionally matched against the text with ALL
whitespace removed (Chinese is written without spaces, so "忽 略 之前 的 指令" must still hit),
and a few high-signal English phrases are matched against an alphanumerics-only form so that
"i-g-n-o-r-e previous instructions" does not slip through.
"""

from __future__ import annotations

import re
import unicodedata

from backend.schemas.security import InjectionCategory, ScreenResult

# --- normalisation ------------------------------------------------------------------------------

# Characters that render as nothing (or only steer text direction) and are used to split keywords.
_INVISIBLE = re.compile(
    "[­͏؜ᅟᅠ឴឵᠋-᠎​-‏‪-‮"
    "⁠-⁯ㅤ︀-️﻿ﾠ]"
)
_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _INVISIBLE.sub("", folded)
    # casefold can produce characters that NFKC would change again; settle once more.
    folded = _INVISIBLE.sub("", unicodedata.normalize("NFKC", folded))
    return _WHITESPACE.sub(" ", folded).strip()


def _compact(normalised: str) -> str:
    """Whitespace-free form used for Chinese patterns."""
    return normalised.replace(" ", "")


def _squash(normalised: str) -> str:
    """Letters and digits only (any script)."""
    return "".join(ch for ch in normalised if ch.isalnum())


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


# --- English patterns (run on the normalised, casefolded text) -----------------------------------

_DET = r"(?:(?:all|any|every|the|your|my|our|these|those|of|ur)\s+)*"
_ADJ = (
    r"(?:(?:previous|prior|above|earlier|preceding|former|original|initial|safety|system|"
    r"current)\s+)*"
)

# The route enum words. "ask" must not be followed by another word ("set the route to ask me
# first" is ordinary English); the others are matched as whole words.
_ENUM = r"[\"']?(?:plan\b|modify\b|unclear\b|ask\b(?!\s+\w))"
_ROUTE_SUBJECT = r"(?:this|it|me|my\s+(?:request|message))"

_PROMPT_OVERRIDE = _compile(
    # "ignore (all|any|the) (previous|prior|above) instructions", "disregard your rules",
    # "forget your task", "override the system prompt" ...
    r"\b(?:ignore|disregard|forget|override|bypass)\s+" + _DET + _ADJ
    + r"(?:instructions?|prompts?|rules|directives?|guidelines?|commands?|programming|training"
    r"|task|orders)\b",
    # "ignore previous messages", "ignore the above", "ignore everything above"
    r"\b(?:ignore|disregard|forget)\s+" + _DET
    + r"(?:previous|prior|earlier|preceding)\s+(?:messages?|context|conversation|chat|input|text)\b",
    r"\b(?:ignore|disregard|forget)\s+(?:everything|all|anything)?(?:\s+of)?\s*(?:the\s+)?"
    r"(?:above|preceding)(?![\w-])",
    r"\b(?:ignore|disregard|forget)\s+(?:everything|all)\s+(?:you\s+(?:were|have\s+been)\s+told"
    r"|before|so\s+far|(?:that\s+)?(?:came|was\s+said)\s+before)\b",
    # persona / mode switches
    r"\byou\s+are\s+now\b",
    r"\byou(?:'|’)?re\s+now\s+(?:a|an|the|my|dan|in|no\s+longer)\b",
    r"\bfrom\s+now\s+on\s*,?\s*(?:you|your|act|behave|respond|answer|ignore)\b",
    r"\bact\s+as\s+(?:an?\s+|the\s+|if\b|though\b|my\b|our\b|your\b|dan\b|root\b|admin)",
    r"\bpretend\s+(?:to\s+be|that\s+you|you\s+are|you(?:'|’)?re)\b",
    r"\bbehave\s+(?:like|as)\s+(?:an?\s+|the\s+)",
    r"\bnew\s+(?:instructions?|rules|directives?|system\s+prompt)\s*[:\-]",
    r"\bhere\s+(?:are|is)\s+(?:your\s+)?new\s+(?:instructions?|rules|directives?)\b",
    r"\b(?:developer|dev|debug|god|admin|sudo|jailbreak|jailbroken|unrestricted|dan)\s+mode\b",
    r"\bdo\s+anything\s+now\b",
    r"\bjailbreak(?:ed)?\b",
    r"\b(?:disable|turn\s+off|remove|bypass)\s+(?:all\s+|your\s+|the\s+)*"
    r"(?:safety|safeguards?|guardrails?|content\s+filters?|filters)\b",
    # forged role / chat-template markers
    r"^(?:system|developer|assistant)\s*:",
    r"<\s*/?\s*(?:system|assistant|developer)\s*>",
    r"<\|\s*(?:im_start|im_end|system|endoftext)\s*\|>",
    r"\[\s*/?\s*(?:inst|system)\s*\]",
    r"<<\s*/?\s*sys\s*>>",
)

_HIDDEN_INSTRUCTION = _compile(
    r"\bsystem\s+(?:prompts?|messages?|instructions?)\b",
    r"\b(?:hidden|secret|internal|confidential)\s+(?:instructions?|prompts?|rules|directives?|"
    r"messages?|guidelines?)\b",
    r"\b(?:developer|initial|original)\s+(?:prompt|instructions?|message)\b",
    # reveal|print|show|repeat ... your / the-hidden prompt|instructions
    r"\b(?:reveal|print|show|repeat|display|output|dump|leak|disclose|tell|give|share|expose|"
    r"recite|echo|paste|copy|translate|encode|summari[sz]e|paraphrase|rephrase|spell|read|list|"
    r"write)\b(?:\s+\S+){0,5}?\s+(?:your|ur|the\s+(?:system|hidden|secret|initial|original|"
    r"above|preceding|full|entire|exact|internal|confidential|previous)|above|previous|prior)"
    r"\s+(?:\w+\s+){0,2}?(?:prompts?|instructions?|rules|guidelines?|directives?|"
    r"configuration|setup|programming)\b",
    r"\b(?:repeat|print|output|show|display|recite)\s+(?:the\s+)?(?:words|text|everything|all|"
    r"content|conversation)\s+(?:above|before|so\s+far)\b",
    r"\bwhat(?:\s+is|\s+are|\s+were|'s|’s)\s+your\s+(?:system\s+|initial\s+|original\s+|exact\s+)?"
    r"(?:instructions?|rules|prompts?|guidelines?|directives?)\b",
    r"\bwhat\s+(?:were|was|have)\s+you\s+(?:been\s+)?(?:told|instructed|programmed|given)\b",
    # "canary" is also a place name (Canary Islands, Canary Wharf): do not flag those trips.
    r"\bcanary\b(?!\s+(?:islands?|isles?|wharf|yellow|coast|row))",
    r"\bpf-canary\b",
    r"\bconfidential\s+(?:marker|token|code)\b",
    r"\bsecret\s+(?:marker|token|key|code)\b",
)

_SMUGGLED_TOOL_CALL = _compile(
    # tag-style tool calls: <tool_call>, </tool>, <function_call>, <invoke ...>
    r"<\s*/?\s*tool",
    r"<\s*/?\s*(?:function|invoke|antml)",
    r"\b(?:tool|function)_(?:calls?|use|name|result)\b",
    r"\bfunction[_ ]call(?:s|ing)?\b",
    # JSON-ish smuggling of our own tool vocabulary
    r"[\"']operation[\"']\s*:",
    r"\boperation\s*[:=]\s*[\"']?(?:web_search|geocode|forecast|places_search|ticket_search|"
    r"reservation_check)\b",
    r"[\"'](?:tool|tool_name|function|function_name|name)[\"']\s*:\s*[\"']"
    r"(?:web_search|maps|weather|places|tickets|geocode|forecast|places_search|ticket_search|"
    r"reservation_check)[\"']",
    r"\b(?:web_search|places_search|ticket_search|reservation_check)\s*\(",
    r"\b(?:geocode|forecast)\(",
    # natural-language requests to run a tool
    r"\bcall\s+(?:the\s+|a\s+|your\s+)?(?:\w+\s+)?tool\b",
    r"\binvoke\s+(?:the\s+|a\s+|your\s+)?(?:\w+\s+)?(?:tool|function)\b",
    r"\b(?:use|run|execute|trigger)\s+(?:the\s+|your\s+)(?:\w+\s+){0,2}?tool\b",
    r"\b(?:call|invoke|execute|trigger)\s+(?:the\s+|your\s+)(?:\w+\s+){0,2}?(?:api|endpoint)\b",
)

_ROUTE_OVERRIDE = _compile(
    # "route this as plan", "route me to the modify path". A bare "route me to the airport" or
    # "route this as a loop" is a navigation request, not an override, and stays unflagged.
    r"\broute\s+" + _ROUTE_SUBJECT + r"\s+(?:as|to|into|through)\s+(?:(?:an?|the)\s+)?" + _ENUM,
    r"\b(?:set|change|switch|force|override|choose|select)\s+(?:the\s+|your\s+|my\s+)?"
    r"(?:route|routing|router|intent)\s*(?:to|as|=|:|->)\s*(?:(?:an?|the)\s+)?" + _ENUM,
    r"\bclassify\s+(?:this|it|my\s+(?:request|message)|the\s+(?:request|message))\s+as\b",
    r"\b(?:treat|handle|process)\s+(?:this|it)\s+as\s+(?:(?:an?|the)\s+)?" + _ENUM,
    r"[\"']route[\"']\s*:",
    r"\broute\s*[:=]\s*" + _ENUM,
    r"[\"'](?:confidence|clarity|needs_clarification|affected_parts)[\"']\s*:",
    r"\b(?:set|make)\s+(?:the\s+)?(?:confidence|clarity)\s+(?:to|=|at)\b",
)

# --- Chinese patterns (run on the whitespace-free text; Traditional and Simplified) --------------

_ZH_GAP = r"[^。.!?！？\n]"

_PROMPT_OVERRIDE_ZH = _compile(
    # 忽略之前的指令 / 忽略以上所有指令 / 忘記你的規則
    r"(?:忽略|无视|無視|忽视|忽視|不要理会|不要理會|不理会|不理會|忘记|忘記|忘掉|绕过|繞過|"
    r"覆盖|覆蓋)(?:之前|以前|先前|上面|上述|以上|前面|所有|全部|一切|任何|你的|你们的|你們的|"
    r"那些|这些|這些|的)*(?:指令|指示|规则|規則|提示词|提示詞|命令)",
    r"(?:忘记|忘記|忘掉|忽略|无视|無視)你(?:的|们的|們的)(?:任务|任務|职责|職責|设定|設定|提示)",
    r"你(?:现在|現在)是(?!否|不是)",
    r"(?:从|從)(?:现在|現在)(?:开始|開始|起)[，,]?你",
    r"(?:请|請)?你(?:要|必须|必須|需要)?(?:扮演|假装|假裝|冒充)",
    r"新(?:的)?(?:指令|指示|规则|規則)[:：]",
    r"(?:开发者|開發者|开发人员|開發人員|调试|調試)模式",
    r"(?:越狱|越獄)",
    r"(?:关闭|關閉|解除|禁用)(?:你的)?(?:安全|限制|过滤|過濾)",
)

_HIDDEN_INSTRUCTION_ZH = _compile(
    # 显示你的提示词 / 顯示你的系統提示 / 告诉我你的指令
    r"(?:显示|顯示|告诉|告訴|输出|輸出|打印|列出|透露|泄露|洩露|泄漏|洩漏|重复|重複|复述|複述|"
    r"说出|說出|给我|給我|展示|公开|公開|发给我|發給我|翻译|翻譯|总结|總結)" + _ZH_GAP
    + r"{0,12}?(?:你的|你们的|你們的|系统|系統|隐藏|隱藏|初始|原始|内部|內部|机密|機密|秘密)"
    + _ZH_GAP + r"{0,6}?(?:提示词|提示詞|提示|指令|指示|规则|規則|设定|設定|配置|prompt)",
    r"(?:系统|系統)(?:提示词|提示詞|提示|指令|指示|prompt)",
    r"(?:隐藏|隱藏|机密|機密|秘密)的?(?:指令|指示|规则|規則|提示词|提示詞|提示|标记|標記)",
    r"(?:金丝雀|金絲雀)(?:令牌|标记|標記|口令|密钥|密鑰)",
    r"(?:你|您)(?:被)?(?:告知|指示|设定|設定|编程|編程)(?:了)?(?:什么|什麼|哪些)",
)

_SMUGGLED_TOOL_CALL_ZH = _compile(
    # "交通工具" (means of transport) is ordinary trip vocabulary and must not match.
    r"(?:调用|調用|呼叫|执行|執行|触发|觸發)" + _ZH_GAP + r"{0,8}?(?:(?<!交通)工具|函数|函數|接口|api)",
    r"使用(?:票务|票務|天气|天氣|地图|地圖|搜索|搜尋|订票|訂票|酒店|景点|景點)(?:查询|查詢)?工具",
    r"(?:工具|函数|函數)(?:调用|調用)",
)

_ROUTE_OVERRIDE_ZH = _compile(
    r"(?:把|将|將)(?:这|這)?(?:个|個|条|條)?(?:请求|請求|消息|訊息|信息|问题|問題)?"
    r"(?:路由|分类|分類|归类|歸類)(?:为|為|成|到)",
    r"路由(?:到|为|為|成|至)(?:plan|modify|ask|unclear|计划|計劃|修改|询问|詢問)",
    r"(?:设置|設置|设定|設定|修改|更改)(?:路由|意图|意圖)(?:为|為|成|到)",
)

# --- Squashed (alphanumerics-only) phrases: obfuscation by separators ----------------------------
# Only long, high-signal phrases: short ones ("you are now", "route this as") would match inside
# innocent run-together text such as "you are nowhere near" or "route this astonishing".

_SQUASHED: tuple[tuple[InjectionCategory, re.Pattern[str]], ...] = (
    (
        InjectionCategory.PROMPT_OVERRIDE,
        re.compile(
            r"(?:ignore|disregard|forget)(?:all|any|every|the|your|my)*"
            r"(?:previous|prior|above|earlier|preceding)(?:instructions|prompt|rules|directives|"
            r"guidelines|commands)"
            r"|(?:ignore|disregard|forget)(?:your|the)(?:instructions|rules|guidelines|directives)"
            r"|developermode|doanythingnow"
        ),
    ),
    (
        InjectionCategory.HIDDEN_INSTRUCTION_REQUEST,
        re.compile(
            r"(?:reveal|print|show|repeat|display|output|tell|leak)(?:me)?"
            r"(?:your(?:system|hidden|secret|initial|original)?"
            r"|the(?:system|hidden|secret|initial|original))"
            r"(?:prompt|instructions)"
            r"|confidentialmarker|hiddeninstructions|pfcanary"
        ),
    ),
)

_ENGLISH_RULES: tuple[tuple[InjectionCategory, tuple[re.Pattern[str], ...]], ...] = (
    (InjectionCategory.PROMPT_OVERRIDE, _PROMPT_OVERRIDE),
    (InjectionCategory.HIDDEN_INSTRUCTION_REQUEST, _HIDDEN_INSTRUCTION),
    (InjectionCategory.SMUGGLED_TOOL_CALL, _SMUGGLED_TOOL_CALL),
    (InjectionCategory.ROUTE_OVERRIDE, _ROUTE_OVERRIDE),
)

_CHINESE_RULES: tuple[tuple[InjectionCategory, tuple[re.Pattern[str], ...]], ...] = (
    (InjectionCategory.PROMPT_OVERRIDE, _PROMPT_OVERRIDE_ZH),
    (InjectionCategory.HIDDEN_INSTRUCTION_REQUEST, _HIDDEN_INSTRUCTION_ZH),
    (InjectionCategory.SMUGGLED_TOOL_CALL, _SMUGGLED_TOOL_CALL_ZH),
    (InjectionCategory.ROUTE_OVERRIDE, _ROUTE_OVERRIDE_ZH),
)


def _any_match(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def screen_message(text: str) -> ScreenResult:
    """Return which injection categories the text matches (case-insensitive, whitespace- and
    unicode-normalised). Categories: PROMPT_OVERRIDE ('ignore previous instructions', 'you are
    now', 'disregard your rules'...), HIDDEN_INSTRUCTION_REQUEST ('reveal your system prompt',
    'print your hidden instructions'...), SMUGGLED_TOOL_CALL ('<tool_call>', '"operation":',
    'call the tickets tool', 'function_call'...), ROUTE_OVERRIDE ('route this as', 'set route to',
    'classify this as plan'...).

    The text is only inspected, never altered; `categories` is the sorted list of unique matches.
    """
    normalised = _normalise(text)
    if not normalised:
        return ScreenResult(flagged=False, categories=[])
    compact = _compact(normalised)
    squashed = _squash(normalised)

    found: set[InjectionCategory] = set()
    for category, patterns in _ENGLISH_RULES:
        if _any_match(patterns, normalised):
            found.add(category)
    for category, patterns in _CHINESE_RULES:
        if _any_match(patterns, compact):
            found.add(category)
    for category, pattern in _SQUASHED:
        if category not in found and pattern.search(squashed):
            found.add(category)

    categories = sorted(found, key=lambda c: c.value)
    return ScreenResult(flagged=bool(categories), categories=categories)
