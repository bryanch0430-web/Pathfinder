"""Place discovery: Google Places (New), Wikivoyage and Wikipedia geosearch."""

from __future__ import annotations

import html
import re

from app.config import Settings
from app.domain.errors import ProviderNoData
from app.domain.models import GeoPoint, PlaceCandidate, SourceRef, utcnow
from app.domain.place_names import osm_names
from app.infrastructure.http import haversine_km, request_json

GOOGLE_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.regularOpeningHours",
        "places.websiteUri",
        "places.googleMapsUri",
        "places.editorialSummary",
        "places.priceLevel",
        "places.reviews",
        "places.types",
    ]
)

TYPE_LABELS = {
    "tourist_attraction": "Attraction",
    "museum": "Museum",
    "park": "Park",
    "temple": "Temple",
    "shrine": "Shrine",
    "historical_landmark": "Landmark",
    "restaurant": "Restaurant",
    "cafe": "Cafe",
    "food": "Food",
}


class GooglePlacesSource:
    name = "google-places"
    #: its top text-search hit is the place asked for, even across languages.
    ranks_by_relevance = True

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = bool(settings.google_places_api_key)

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
            raise ProviderNoData("Google Places key is not configured", source=self.name)
        text_query = query.strip() if query and query.strip() else f"best {category} in {destination}"
        if query and destination.lower() not in text_query.lower():
            text_query = f"{text_query} in {destination}"
        base = (self.settings.google_places_proxy or "https://places.googleapis.com").rstrip("/")
        data = await request_json(
            "POST",
            f"{base}/v1/places:searchText",
            source=self.name,
            headers={
                "X-Goog-Api-Key": self.settings.google_places_api_key,
                "X-Goog-FieldMask": GOOGLE_FIELD_MASK,
                "Content-Type": "application/json",
            },
            payload={
                "textQuery": text_query,
                "languageCode": "en",
                "maxResultCount": max(limit * 2, 10),
                "includedType": "restaurant" if category in {"restaurant", "meal", "food"} else "tourist_attraction",
            },
            timeout=self.settings.http_timeout_seconds,
            retries=self.settings.http_retries,
            user_agent=self.settings.user_agent,
        )
        rows = data.get("places") or []
        if not rows:
            return []
        out: list[PlaceCandidate] = []
        for row in rows:
            location = row.get("location") or {}
            lat, lng = location.get("latitude"), location.get("longitude")
            if lat is None or lng is None:
                continue
            if geo and haversine_km(geo.lat, geo.lng, float(lat), float(lng)) > 80:
                continue
            types = [str(t) for t in (row.get("types") or [])]
            label = next((TYPE_LABELS[t] for t in types if t in TYPE_LABELS), "")
            hours = ((row.get("regularOpeningHours") or {}).get("weekdayDescriptions") or [])[:7]
            summary = (row.get("editorialSummary") or {}).get("text") or ""
            display = str((row.get("displayName") or {}).get("text") or "").strip()
            if not display:
                continue  # never invent a name for a place the source did not name
            url = row.get("googleMapsUri") or row.get("websiteUri") or ""
            refs = [
                SourceRef(
                    title=display,
                    url=url,
                    source=self.name,
                    fetched_at=utcnow(),
                )
            ]
            if row.get("websiteUri") and row.get("websiteUri") != url:
                refs.append(SourceRef(title=f"{display} website", url=row["websiteUri"], source=self.name))
            out.append(
                PlaceCandidate(
                    name=str(display),
                    category=label or ("Restaurant" if "restaurant" in types else "Attraction"),
                    description=summary,
                    geo=GeoPoint(lat=float(lat), lng=float(lng)),
                    address=str(row.get("formattedAddress") or ""),
                    rating=float(row["rating"]) if row.get("rating") is not None else None,
                    review_count=int(row["userRatingCount"]) if row.get("userRatingCount") is not None else None,
                    opening_hours=[str(hour) for hour in hours],
                    price_level=str(row.get("priceLevel") or ""),
                    website=str(row.get("websiteUri") or ""),
                    reviews=[
                        {
                            "text": str(((review.get("text") or {}).get("text")) or "")[:600],
                            "rating": review.get("rating"),
                            "author": ((review.get("authorAttribution") or {}).get("displayName") or ""),
                        }
                        for review in (row.get("reviews") or [])[:5]
                        if (review.get("text") or {}).get("text")
                    ],
                    source_refs=refs,
                    tags=types[:6],
                    # Asked for in English; Google says which language the name is in.
                    names=(
                        {"en": display}
                        if str((row.get("displayName") or {}).get("languageCode") or "").startswith("en")
                        else {}
                    ),
                )
            )
        return out[:limit]


class WikivoyagePlacesSource:
    """Keyless fallback. Reads the See / Eat / Sleep sections of a Wikivoyage page."""

    name = "wikivoyage"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = True

    async def search(
        self,
        *,
        destination: str,
        geo: GeoPoint | None,
        category: str,
        limit: int,
        query: str | None = None,
    ) -> list[PlaceCandidate]:
        wikitext, title = await self._wikitext(destination)
        sections = _sections(wikitext)
        if category in {"restaurant", "meal", "food"}:
            raw = sections.get("eat", "") + "\n" + sections.get("drink", "")
            templates = _templates(raw)
            kinds = {"eat", "drink", "listing"}
        else:
            raw = sections.get("see", "") + "\n" + sections.get("do", "")
            templates = _templates(raw)
            kinds = {"see", "do", "listing"}
        out: list[PlaceCandidate] = []
        for kind, params in templates:
            if kind not in kinds:
                continue
            name = _clean(params.get("name", ""))
            if not name:
                continue
            lat = _float(params.get("lat"))
            lng = _float(params.get("long"))
            if geo and lat is not None and lng is not None:
                if haversine_km(geo.lat, geo.lng, lat, lng) > 80:
                    continue
            description = _clean(params.get("content", ""))
            url = params.get("url", "").strip() or f"https://en.wikivoyage.org/wiki/{title.replace(' ', '_')}"
            out.append(
                PlaceCandidate(
                    name=name,
                    category="Restaurant" if kind in {"eat", "drink"} else "Attraction",
                    description=description[:600],
                    geo=GeoPoint(lat=lat, lng=lng) if lat is not None and lng is not None else None,
                    address=_clean(params.get("address", "")),
                    opening_hours=[_clean(params.get("hours", ""))] if params.get("hours") else [],
                    website=url,
                    source_refs=[
                        SourceRef(
                            title=f"Wikivoyage: {title}",
                            url=url,
                            source=self.name,
                            fetched_at=utcnow(),
                        )
                    ],
                    tags=["wikivoyage"],
                    # `alt=` is the listing's other name, usually the local script.
                    aliases=[
                        alias
                        for alias in (part.strip() for part in re.split(r"[,;、]", _clean(params.get("alt", ""))))
                        if alias and alias != name
                    ],
                )
            )
            if len(out) >= limit:
                break
        return out

    async def _wikitext(self, destination: str) -> tuple[str, str]:
        page = destination.replace(" ", "_")
        data = await request_json(
            "GET",
            "https://en.wikivoyage.org/w/api.php",
            source=self.name,
            params={
                "action": "parse",
                "page": page,
                "prop": "wikitext",
                "format": "json",
                "formatversion": 2,
                "redirects": 1,
            },
            timeout=self.settings.http_timeout_seconds,
            retries=0,  # deliberate: Wikimedia asks clients not to repeat failed requests
            user_agent=self.settings.wikimedia_user_agent,
        )
        parsed = data.get("parse") or {}
        wikitext = parsed.get("wikitext")
        if not wikitext:
            raise ProviderNoData(f"Wikivoyage has no page for {destination}", source=self.name)
        return str(wikitext), str(parsed.get("title") or destination)


class NominatimPlacesSource:
    """Keyless OSM search fallback, used when Google Places is unavailable."""

    name = "nominatim-places"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = True

    async def search(
        self,
        *,
        destination: str,
        geo: GeoPoint | None,
        category: str,
        limit: int,
        query: str | None = None,
    ) -> list[PlaceCandidate]:
        term = "restaurants" if category in {"restaurant", "meal", "food"} else "attractions"
        text = query.strip() if query and query.strip() else f"{term} in {destination}"
        params = {
            "q": text,
            "format": "jsonv2",
            "limit": max(limit * 2, 12),
            "addressdetails": 1,
            "extratags": 1,
            "namedetails": 1,
        }
        if geo:
            params["viewbox"] = f"{geo.lng - 0.5},{geo.lat + 0.5},{geo.lng + 0.5},{geo.lat - 0.5}"
            params["bounded"] = 1
        data = await request_json(
            "GET",
            "https://nominatim.openstreetmap.org/search",
            source=self.name,
            params=params,
            timeout=self.settings.http_timeout_seconds,
            retries=0,  # deliberate: OpenStreetMap's usage policy asks clients not to repeat requests
            user_agent=self.settings.user_agent,
        )
        rows = data.get("data") if isinstance(data.get("data"), list) else data
        out: list[PlaceCandidate] = []
        if not isinstance(rows, list):
            return out
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or str(row.get("display_name") or "").split(",")[0]).strip()
            if not name:
                continue
            try:
                lat = float(row["lat"])
                lng = float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            osm_type = str(row.get("osm_type") or "node")
            osm_id = row.get("osm_id")
            url = (
                f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
                if osm_id
                else f"https://www.openstreetmap.org/search?query={name}"
            )
            cuisine = str(row.get("type") or "place").replace("_", " ")
            extratags = row.get("extratags") or {}
            # OSM's opening_hours tag, verbatim ("Mo-Fr 09:00-17:00; Sa 10:00-14:00").
            hours = str(extratags.get("opening_hours") or "").strip()
            out.append(
                PlaceCandidate(
                    name=name,
                    category="Restaurant" if "restaurant" in term else "Attraction",
                    description=str(row.get("display_name") or "")[:400],
                    geo=GeoPoint(lat=lat, lng=lng),
                    address=str(row.get("display_name") or ""),
                    opening_hours=[hours] if hours else [],
                    website=str(extratags.get("website") or ""),
                    source_refs=[
                        SourceRef(
                            title=f"OpenStreetMap: {name}",
                            url=url,
                            source=self.name,
                            fetched_at=utcnow(),
                        )
                    ],
                    tags=["openstreetmap", cuisine],
                    aliases=sorted(
                        {
                            str(value).strip()
                            for key, value in (row.get("namedetails") or {}).items()
                            if key.startswith(("name", "alt_name", "official_name", "int_name", "old_name"))
                            and str(value).strip()
                            and str(value).strip() != name
                        }
                    )[:12],
                    names=osm_names(row.get("namedetails")),
                )
            )
            if len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------------------
# Wikitext helpers (also used by the hotel fallback)
# ---------------------------------------------------------------------------


def _sections(wikitext: str) -> dict[str, str]:
    """Group each level-2 section together with all of its subsections.

    Wikivoyage puts most See/Do/Eat listings under nested headings such as
    ``=== World Heritage Sites ===``. Flattening only the level-2 body would
    drop every listing, so nested headings are folded into their parent.
    """
    sections: dict[str, str] = {}
    current = ""
    buffer: list[str] = []
    for line in wikitext.splitlines():
        match = re.match(r"^(={2,})\s*([^=]+?)\s*\1\s*$", line)
        if match:
            level = len(match.group(1))
            if level == 2:
                if current:
                    sections[current] = "\n".join(buffer)
                current = match.group(2).strip().lower()
                buffer = [line]
            else:
                buffer.append(line)
        else:
            buffer.append(line)
    if current:
        sections[current] = "\n".join(buffer)
    return sections


def _templates(text: str) -> list[tuple[str, dict[str, str]]]:
    out: list[tuple[str, dict[str, str]]] = []
    for match in re.finditer(r"\{\{\s*(see|do|eat|drink|sleep|listing|go|buy)\b", text, re.IGNORECASE):
        kind = match.group(1).lower()
        start = match.end()
        depth = 2
        index = start
        while index < len(text) and depth > 0:
            if text.startswith("{{", index):
                depth += 2
                index += 2
                continue
            if text.startswith("}}", index):
                depth -= 2
                index += 2
                continue
            index += 1
        body = text[start : max(start, index - 2)]
        params: dict[str, str] = {}
        for part in re.split(r"\|(?=[a-zA-Z_ -]+=)", body):
            if "=" in part:
                key, value = part.split("=", 1)
                params[key.strip().lower()] = value.strip()
        out.append((kind, params))
    # simple bullet listings: * [[Name]] description
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("*") and "[[" in line:
            link = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", line)
            if link:
                name = (link.group(2) or link.group(1)).strip()
                desc = re.sub(r"\[\[[^\]]+\]\]", "", line[1:]).strip()
                out.append(("listing", {"name": name, "content": desc}))
    return out


def _clean(value: str) -> str:
    value = re.sub(r"<ref[^>]*>.*?</ref>", "", value, flags=re.S)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"'''?", "", value)
    value = value.replace("&nbsp;", " ")
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def _float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def parse_price(value: str) -> tuple[float | None, str]:
    """Extract the first plausible amount from a Wikivoyage price string."""
    if not value:
        return None, ""
    cleaned = _clean(value)
    match = re.search(r"([$€£¥]|USD|EUR|GBP|JPY|HKD|CNY|SGD|THB)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", cleaned)
    if not match:
        match = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)", cleaned)
        if not match:
            return None, cleaned
        return float(match.group(1).replace(",", "")), cleaned
    amounts = re.findall(r"([0-9][0-9,]*(?:\.[0-9]+)?)", cleaned)
    if not amounts:
        return None, cleaned
    return float(amounts[0].replace(",", "")), cleaned


_AUTOCOMPLETE_CACHE: dict[str, list[dict]] = {}


class GooglePlacesAutocomplete:
    """Places API (New) Autocomplete for the destination field."""

    name = "google-places-autocomplete"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._key = settings.google_places_api_key
        self.available = bool(self._key)

    async def suggest(self, query: str, limit: int = 5) -> list[dict]:
        text = query.strip()
        if not self.available or len(text) < 3:
            return []
        cached = _AUTOCOMPLETE_CACHE.get(text.lower())
        if cached is not None:
            return cached[:limit]
        base = (self.settings.google_places_proxy or "https://places.googleapis.com").rstrip("/")
        data = await request_json(
            "POST",
            f"{base}/v1/places:autocomplete",
            source=self.name,
            headers={"X-Goog-Api-Key": self._key, "Content-Type": "application/json"},
            payload={"input": text, "languageCode": "en"},
            timeout=self.settings.http_timeout_seconds,
            retries=self.settings.http_retries,
            user_agent=self.settings.user_agent,
        )
        items: list[dict] = []
        for suggestion in data.get("suggestions") or []:
            prediction = suggestion.get("placePrediction") or {}
            structured = prediction.get("structuredFormat") or {}
            main = ((structured.get("mainText") or {}).get("text")) or ((prediction.get("text") or {}).get("text"))
            secondary = (structured.get("secondaryText") or {}).get("text") or ""
            if not main:
                continue
            label = f"{main}, {secondary}".strip(", ") if secondary else str(main)
            items.append({"label": label, "place_id": str(prediction.get("placeId") or "")})
            if len(items) >= 10:
                break
        _AUTOCOMPLETE_CACHE[text.lower()] = items
        return items[:limit]

