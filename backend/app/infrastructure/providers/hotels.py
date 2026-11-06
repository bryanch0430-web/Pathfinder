"""Hotel discovery: SerpApi Google Hotels, then Wikivoyage Sleep listings."""

from __future__ import annotations

import re
from datetime import date

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import (
    DataStatus,
    GeoPoint,
    HotelCandidate,
    Money,
    PriceKind,
    SourceRef,
    utcnow,
)
from app.infrastructure.http import request_json
from app.infrastructure.providers.places import (
    WikivoyagePlacesSource,
    _clean,
    _sections,
    _templates,
    parse_price,
)


#: When the trip budget leaves no stay under the nightly cap, this many of the cheapest are kept, marked over budget.
OVER_BUDGET_KEPT = 3
#: The note on a stay kept although its rate is over the trip budget's share for a night.
OVER_BUDGET_NOTE = (
    "Over the trip budget: one of the cheapest stays found. Indicative nightly rate; availability is not confirmed."
)


class SerpApiHotelSource:
    name = "serpapi-hotels"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = bool(settings.serpapi_key)

    async def search(
        self,
        *,
        destination: str,
        check_in: date,
        check_out: date,
        party_size: int,
        budget: float | None,
        currency: str,
    ) -> list[HotelCandidate]:
        if not self.available:
            raise ProviderNoData("SerpApi key is not configured", source=self.name)
        nights = max((check_out - check_in).days, 1)
        data = await request_json(
            "GET",
            "https://serpapi.com/search.json",
            source=self.name,
            params={
                "engine": "google_hotels",
                "q": f"hotels in {destination}",
                "check_in_date": check_in.isoformat(),
                "check_out_date": check_out.isoformat(),
                "adults": max(party_size, 1),
                "currency": currency,
                "hl": "en",
                "gl": "us",
                "api_key": self.settings.serpapi_key,
            },
            timeout=max(self.settings.http_timeout_seconds, 20),
            retries=self.settings.http_retries,
            user_agent=self.settings.user_agent,
        )
        if data.get("error"):
            raise ProviderError(f"{self.name} returned an error", source=self.name, detail=str(data["error"])[:300])
        properties = data.get("properties") or []
        if not properties:
            return []
        nightly_cap = (budget * 0.45 / nights) if budget else None
        out: list[HotelCandidate] = []
        over_cap: list[tuple[float, dict]] = []
        for row in properties[:20]:
            price = _rate(row)
            if nightly_cap and price and price > nightly_cap * 1.15:
                over_cap.append((price, row))
                continue
            out.append(self._candidate(row, price, currency))
        if not out and over_cap:
            # The budget leaves no stay at all. The cheapest few are kept, marked over budget, so the trip still has
            # a real hotel and its cost says "over" instead of the plan having none.
            over_cap.sort(key=lambda pair: pair[0])
            return [self._candidate(row, price, currency, over_budget=True) for price, row in over_cap[:OVER_BUDGET_KEPT]]
        out.sort(key=lambda hotel: (hotel.rating or 0, -(hotel.nightly_price.amount if hotel.nightly_price else 10**9)), reverse=True)
        return out[:8]

    def _candidate(self, row: dict, price: float | None, currency: str, *, over_budget: bool = False) -> HotelCandidate:
        coords = row.get("gps_coordinates") or {}
        lat, lng = coords.get("latitude"), coords.get("longitude")
        link = row.get("link") or ""
        return HotelCandidate(
            name=str(row.get("name") or "Hotel"),
            area=str(row.get("neighborhood") or ""),
            geo=GeoPoint(lat=float(lat), lng=float(lng)) if lat is not None and lng is not None else None,
            nightly_price=Money(amount=price, currency=currency) if price is not None else None,
            price_kind=PriceKind.REFERENCE if price is not None else PriceKind.NONE,
            rating=_num(row.get("overall_rating")),
            review_count=int(row["reviews"]) if row.get("reviews") is not None else None,
            booking_url=link,
            source_refs=[
                SourceRef(
                    title=f"Google Hotels: {row.get('name', 'hotel')}",
                    url=link,
                    source=self.name,
                    fetched_at=utcnow(),
                )
            ],
            notes=OVER_BUDGET_NOTE if over_budget else "Indicative nightly rate; availability is not confirmed.",
        )


class WikivoyageHotelSource:
    name = "wikivoyage-hotels"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._wiki = WikivoyagePlacesSource(settings)

    async def search(
        self,
        *,
        destination: str,
        check_in: date,
        check_out: date,
        party_size: int,
        budget: float | None,
        currency: str,
    ) -> list[HotelCandidate]:
        wikitext, title = await self._wiki._wikitext(destination)
        section = _sections(wikitext).get("sleep", "")
        out: list[HotelCandidate] = []
        for kind, params in _templates(section):
            if kind not in {"sleep", "listing"}:
                continue
            name = _clean(params.get("name", ""))
            if not name:
                continue
            amount, price_text = parse_price(params.get("price", ""))
            currency_code = _currency_from_text(params.get("price", ""), destination) or currency
            lat = _float(params.get("lat"))
            lng = _float(params.get("long"))
            url = params.get("url", "").strip() or f"https://en.wikivoyage.org/wiki/{title.replace(' ', '_')}"
            out.append(
                HotelCandidate(
                    name=name,
                    area=_clean(params.get("address", "")),
                    geo=GeoPoint(lat=lat, lng=lng) if lat is not None and lng is not None else None,
                    nightly_price=Money(amount=amount, currency=currency_code) if amount is not None else None,
                    price_kind=PriceKind.REFERENCE if amount is not None else PriceKind.NONE,
                    booking_url=url,
                    source_refs=[
                        SourceRef(
                            title=f"Wikivoyage: {title}",
                            url=url,
                            source=self.name,
                            fetched_at=utcnow(),
                        )
                    ],
                    notes=f"Guide listing ({price_text}). Not a live rate." if price_text else "Guide listing.",
                )
            )
        return out[:8]


def _rate(row: dict) -> float | None:
    rate = row.get("rate_per_night") or {}
    if isinstance(rate, dict):
        for key in ("extracted_lowest", "lowest"):
            value = rate.get(key)
            if value is not None:
                return _money_number(value)
    value = row.get("total_rate") or {}
    if isinstance(value, dict) and value.get("extracted_lowest") is not None:
        return _money_number(value["extracted_lowest"])
    return None


def _money_number(value: object) -> float | None:
    match = re.search(r"[0-9][0-9,]*(?:\.[0-9]+)?", str(value).replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _num(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: str | None) -> float | None:
    try:
        return float(str(value)) if value else None
    except ValueError:
        return None


def _currency_from_text(text: str, destination: str) -> str:
    if "€" in text:
        return "EUR"
    if "£" in text:
        return "GBP"
    if "$" in text:
        return "USD"
    if "¥" in text or "円" in text:
        if destination.lower() in {"tokyo", "kyoto", "osaka", "japan", "sapporo", "fukuoka", "nara"}:
            return "JPY"
        return "CNY"
    if "HK$" in text:
        return "HKD"
    return ""
