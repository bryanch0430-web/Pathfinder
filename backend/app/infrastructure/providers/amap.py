"""AMap (Gaode) Web Service: the maps fallback behind Google (Proposal DB-16).

Two endpoints are used, both answering JSON with ``status`` ("1" ok, "0" error),
``info`` and ``infocode``:

* ``/v3/geocode/geo``  — ``geocodes[].location`` for an address or place name;
* ``/v3/place/text``   — ``pois[]`` with ``name``, ``location``, ``address``, ``type``.

AMap answers well only for mainland China, and it publishes GCJ-02 coordinates as
``"lng,lat"`` strings. Every point is converted to WGS-84 here, so the Google map
and the grounding checks see the same coordinates whichever source answered.
Without ``AMAP_KEY`` both sources are disabled and never called.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind, GeoLocation, GeoPoint, PlaceCandidate, SourceRef, utcnow
from app.infrastructure.http import haversine_km, request_json
from app.infrastructure.providers.gcj02 import gcj02_to_wgs84, in_mainland_china

AMAP_BASE = "https://restapi.amap.com"

#: AMap POI type codes: 风景名胜 (scenic spots), 博物馆 (museums), 餐饮服务 (food).
ATTRACTION_TYPES = "110000|140100"
RESTAURANT_TYPES = "050000"
RESTAURANT_CATEGORIES = {"restaurant", "meal", "food"}

#: infocode -> (failure kind, retryable). Anything unlisted is a retryable server error.
_AUTH = (FailureKind.AUTH, False)
_RATE = (FailureKind.RATE_LIMIT, True)
_BAD = (FailureKind.INVALID_REQUEST, False)
_NONE = (FailureKind.NO_DATA, False)
INFOCODE_KINDS: dict[str, tuple[FailureKind, bool]] = {
    "10001": _AUTH,  # INVALID_USER_KEY
    "10002": _AUTH,  # SERVICE_NOT_AVAILABLE (key lacks the service)
    "10005": _AUTH,  # INVALID_USER_IP
    "10006": _AUTH,  # INVALID_USER_DOMAIN
    "10007": _AUTH,  # INVALID_USER_SIGNATURE
    "10008": _AUTH,  # INVALID_USER_SCODE
    "10009": _AUTH,  # USERKEY_PLAT_NOMATCH
    "10012": _AUTH,  # INSUFFICIENT_PRIVILEGES
    "10013": _AUTH,  # USER_KEY_RECYCLED
    "10003": _RATE,  # DAILY_QUERY_OVER_LIMIT
    "10004": _RATE,  # ACCESS_TOO_FREQUENT
    "10010": _RATE,  # IP_QUERY_OVER_LIMIT
    "10014": _RATE,  # QPS_HAS_EXCEEDED_THE_LIMIT
    "10019": _RATE,  # CQPS_HAS_EXCEEDED_THE_LIMIT
    "10020": _RATE,  # CKQPS_HAS_EXCEEDED_THE_LIMIT
    "10021": _RATE,  # CUQPS_HAS_EXCEEDED_THE_LIMIT
    "10044": _RATE,  # USER_DAILY_QUERY_OVER_LIMIT
    "10015": (FailureKind.TIMEOUT, True),  # GATEWAY_TIMEOUT
    "20000": _BAD,  # INVALID_PARAMS
    "20001": _BAD,  # MISSING_REQUIRED_PARAMS
    "20002": _BAD,  # ILLEGAL_REQUEST
    "20011": _NONE,  # INSUFFICIENT_ABROAD_PRIVILEGES (a point outside China)
    "20800": _NONE,  # OUT_OF_SERVICE
}

_STATUS_LABEL = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
_KANA_HANGUL = re.compile(r"[぀-ヿㇰ-ㇿ가-힯]")
_HAN = re.compile(r"[一-鿿]")
_CHINA_WORDS = re.compile(r"\b(china|prc)\b|中国|中國", re.IGNORECASE)
_NOT_MAINLAND_WORDS = re.compile(r"hong\s*kong|macau|macao|taiwan|taipei|香港|澳門|澳门|台灣|台湾|臺灣", re.IGNORECASE)
_CN_HINTS = {"cn", "chn", "china", "prc", "中国", "中國"}


def check_status(data: dict[str, Any], source: str) -> None:
    """Raise the typed failure for an AMap answer whose ``status`` is not "1"."""
    status = str(data.get("status") or "")
    if status == "1":
        return
    info = _text(data.get("info"))
    code = _text(data.get("infocode"))
    kind, retryable = INFOCODE_KINDS.get(code, (FailureKind.SERVER_ERROR, True))
    # AMap names its failures with an upper-case label (INVALID_USER_KEY). Anything else it says
    # is free text, which stays in the detail.
    label = info if _STATUS_LABEL.fullmatch(info) else "unknown error"
    shown_status = status if status.isdigit() else "?"
    shown_code = code if code.isdigit() else "?"
    message = f"AMap returned status {shown_status}: {label} (infocode {shown_code})"
    error = ProviderNoData(message, source=source) if kind is FailureKind.NO_DATA else ProviderError(
        message, kind=kind, source=source, retryable=retryable
    )
    error.detail = f"{info} (infocode {code})"[:300]
    raise error


def parse_location(value: Any) -> GeoPoint | None:
    """A WGS-84 point from AMap's ``"lng,lat"`` GCJ-02 string, or None when malformed."""
    text = _text(value)
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        lng, lat = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    wlat, wlng = gcj02_to_wgs84(lat, lng)
    return GeoPoint(lat=round(wlat, 7), lng=round(wlng, 7))


def mainland_query(name: str, country_hint: str = "") -> bool:
    """Whether a geocoding query is plausibly in mainland China, AMap's coverage."""
    hint = country_hint.strip().lower()
    if hint:
        return hint in _CN_HINTS
    if _NOT_MAINLAND_WORDS.search(name) or _KANA_HANGUL.search(name):
        return False
    return bool(_HAN.search(name) or _CHINA_WORDS.search(name))


def _text(value: Any) -> str:
    # AMap sends an empty list, not "", for a field it has no value for.
    return value.strip() if isinstance(value, str) else ""


def _city(destination: str) -> str:
    return destination.split(",")[0].strip()


class _AmapBase:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._key = (settings.amap_key or "").strip()
        self.available = bool(self._key)

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            raise ProviderNoData("AMap key (AMAP_KEY) is not configured", source=self.name)
        data = await request_json(
            "GET",
            f"{AMAP_BASE}{path}",
            source=self.name,
            params={"key": self._key, "output": "JSON", **params},
            timeout=self.settings.http_timeout_seconds,
            retries=self.settings.http_retries,
            user_agent=self.settings.user_agent,
        )
        check_status(data, self.name)
        return data


class AmapGeocoder(_AmapBase):
    """``/v3/geocode/geo``: tried after Google, for queries in mainland China only."""

    name = "amap-geocoder"

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation | None:
        if not self.available:
            raise ProviderNoData("AMap key (AMAP_KEY) is not configured", source=self.name)
        if not mainland_query(name, country_hint):
            return None  # outside AMap's coverage: the next geocoder answers
        data = await self._get("/v3/geocode/geo", {"address": name})
        for row in data.get("geocodes") or []:
            point = parse_location(row.get("location"))
            if point is None:
                continue
            mainland = in_mainland_china(point.lat, point.lng)
            return GeoLocation(
                name=_text(row.get("formatted_address")) or name,
                country_code="CN" if mainland else "",
                timezone="Asia/Shanghai" if mainland else "",
                geo=point,
                source=self.name,
            )
        return None


class AmapPlacesSource(_AmapBase):
    """``/v3/place/text``: places in a mainland-China city after Google found none."""

    name = "amap-places"
    #: AMap matches Chinese names; its top hit is not trusted for an English wish.
    ranks_by_relevance = False

    async def search(
        self,
        *,
        destination: str,
        geo: GeoPoint | None,
        category: str,
        limit: int,
        query: str | None = None,
    ) -> list[PlaceCandidate]:
        if not self.available:
            raise ProviderNoData("AMap key (AMAP_KEY) is not configured", source=self.name)
        if geo is not None and not in_mainland_china(geo.lat, geo.lng):
            return []  # outside mainland China the next place source answers
        restaurant = category in RESTAURANT_CATEGORIES
        params: dict[str, Any] = {
            "types": RESTAURANT_TYPES if restaurant else ATTRACTION_TYPES,
            "city": _city(destination),
            "citylimit": "true",
            "offset": min(max(limit * 2, 10), 25),
            "page": 1,
            "extensions": "all",
        }
        if query and query.strip():
            params["keywords"] = query.strip()
        data = await self._get("/v3/place/text", params)
        out: list[PlaceCandidate] = []
        for row in data.get("pois") or []:
            display = _text(row.get("name"))
            point = parse_location(row.get("location"))
            if not display or point is None:
                continue  # never invent a name or a position the source did not give
            if geo is not None and haversine_km(geo.lat, geo.lng, point.lat, point.lng) > 80:
                continue
            types = [part for part in _text(row.get("type")).split(";") if part]
            typecode = _text(row.get("typecode"))
            label = "Restaurant" if restaurant else ("Museum" if typecode.startswith("1401") else "Attraction")
            poi_id = _text(row.get("id"))
            url = f"https://www.amap.com/place/{poi_id}" if poi_id else ""
            rating = _text((row.get("biz_ext") or {}).get("rating")) if isinstance(row.get("biz_ext"), dict) else ""
            out.append(
                PlaceCandidate(
                    name=display,
                    category=label,
                    geo=point,
                    address=_text(row.get("address")),
                    rating=_float(rating),
                    source_refs=[SourceRef(title=display, url=url, source=self.name, fetched_at=utcnow())],
                    tags=types[:6],
                )
            )
        return out


def _float(value: str) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None
