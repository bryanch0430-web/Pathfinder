"""Trusted localized place names: what a place source called a place, per display locale.

Names are display data. They come only from a source's own fields (OSM ``name:en`` /
``name:zh-HK`` / ..., Google's English ``displayName``) and are never written by a model.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.korean_names import english_place_name

#: The display locales a plan item can carry a trusted name for.
NAME_LOCALES = ("en", "zh-Hant")

#: OSM keys for a Traditional Chinese name, best first: Hong Kong wording, then the
#: generic Traditional tag, then Taiwan. ``name:zh`` counts only when it is Traditional.
OSM_ZH_HANT_KEYS = ("name:zh-HK", "name:zh-Hant", "name:zh-Hant-HK", "name:zh-TW", "name:zh-Hant-TW", "name:zh")

# Common characters that exist only in Simplified Chinese. One of them in a name means
# the name is not Traditional; it is a heuristic, not a converter.
_SIMPLIFIED_ONLY = frozenset(
    "这们个来时为说国过发会对动学经还没现点业从东车门问间开关头见实机两边体样书变馆园区桥楼场广汉龙马鸟鱼"
    "钟铁银寿岛湾庙观览纪际图画历华丽乐兰济湿县镇乡庄闸阳阴陆鲁齐韩苏粤贵尔鸡饭饮汤烧烤鸭虾蚝酱卖买价钱产厂"
    "处备杂灵爱宁宝寻导层岁帅归当录忆怀态总恋战户执扩护报拥择挤挥损换据摄携敌数斋断无旧显晒术杀权条杨极构枪"
    "标栏树档梦检欢残毕气汇沟沪泪泽浅测浏浓涂涛润涧渊渔滨满滚灯灾炉热爷牵犹狮独猎献环玛珑琼电畅疗盐监盖盘码"
    "础硕确礼祯离种积称稳穷竞笔签简粮紧纳纵线练组细织终结绕绘给络绝统继绩续维绿缆网罗罚职联聪肃胜脑艺节芦苍"
    "苹茧荐荣药莱获营萨蓝虽蚁补装规视觉触计订认讨让训议记讲许论设访证评识诉词译试诗话诞询该详语误请读课谁调"
    "谈谢贝负贡财责贤败货质贩贫购贯贸费贺资赏赛赞赵赶趋跃践轨转轮软轻载较辆辉辑输辖辽达迁运进远违连迟适选递"
    "逻遗邓邮邻郑释鉴针钓钢钥铜铺链销锁锋错锦镜长闭闲闻阁阅队阶陈险随隐难雾页顶项顺须顾顿预领频题颜额风飞饼"
    "驾驶验骑鲜鸣鹤麦齿龟宫厅亚圣坛"
)
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN = re.compile(r"[A-Za-z]")


def is_traditional(text: str) -> bool:
    """True when ``text`` has Chinese characters and none of them is Simplified-only."""
    return bool(_HAN.search(text or "")) and not any(char in _SIMPLIFIED_ONLY for char in text)


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return text if 0 < len(text) <= 160 else ""


def osm_names(namedetails: dict[str, Any] | None) -> dict[str, str]:
    """The trusted localized names in an OSM ``namedetails`` block."""
    details = namedetails or {}
    names: dict[str, str] = {}
    english = _clean(details.get("name:en"))
    if english and _LATIN.search(english):
        names["en"] = english
    for key in OSM_ZH_HANT_KEYS:
        value = _clean(details.get(key))
        if value and is_traditional(value):
            names["zh-Hant"] = value
            break
    return names


def trusted_names(raw: Any) -> dict[str, str]:
    """Keep only well-formed names for the supported locales (a source row's ``names``)."""
    if not isinstance(raw, dict):
        return {}
    names: dict[str, str] = {}
    english = _clean(raw.get("en"))
    if english and _LATIN.search(english):
        names["en"] = english
    chinese = _clean(raw.get("zh-Hant"))
    if chinese and is_traditional(chinese):
        names["zh-Hant"] = chinese
    return names


def display_name(name: str, names: dict[str, str] | None, locale: str) -> str:
    """The shared display rule: en -> names.en or name; zh-Hant -> 「中文 (original)」 when
    a trusted Chinese name exists, else the original name."""
    names = names or {}
    if locale == "zh-Hant":
        chinese = names.get("zh-Hant", "")
        if chinese and chinese != name:
            return f"{chinese} ({name})"
        return chinese or name
    return names.get("en") or name


def english_name(name: str, names: dict[str, str] | None) -> str:
    """A place's name in generated English text such as a day title: the source's own English
    name, else a Korean name romanized by rule ("Gyeryongmun Gate"), else the name as written."""
    return (names or {}).get("en") or english_place_name(name)
