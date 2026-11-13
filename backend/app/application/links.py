"""Links on a plan's stops, stays and transport.

Every link is built here from fields a source returned (a place's website and Maps page, a hotel's or flight's own
booking link) or from known coordinates. Nothing a model wrote becomes a link, and only http and https addresses are
kept. The links are derived data: ``attach_links`` writes them again from the fields each time, so they follow an edit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from app.domain.models import (
    GeoPoint,
    HotelStay,
    Link,
    PlanAlternative,
    PlanItem,
    TicketOption,
    TripPlan,
)

#: Where a Google Maps page lives: a place's own page, or a directions link built from two place names.
_MAP_HOSTS = ("google.com/maps", "maps.google.", "maps.app.goo.gl", "goo.gl/maps")


def _get(row: Any, key: str) -> Any:
    return row.get(key) if isinstance(row, Mapping) else getattr(row, key, None)


def _http(url: Any) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    parts = urlsplit(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _is_map(url: str) -> bool:
    lower = url.lower()
    return any(host in lower for host in _MAP_HOSTS)


def map_search_link(geo: GeoPoint | Mapping | None) -> str:
    """A Google Maps search link for a point: built from its two coordinates and nothing else."""
    lat, lng = _get(geo, "lat"), _get(geo, "lng")
    if lat is None or lng is None:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"


def _unique(links: list[Link]) -> list[Link]:
    seen: set[str] = set()
    out: list[Link] = []
    for link in links:
        if link.url not in seen:
            seen.add(link.url)
            out.append(link)
    return out


def links_for_place(row: Any) -> list[Link]:
    """Links for a stop, a meal or an alternative (a research row, or a plan object with the same fields).

    The place's booking link; its own website (the source reference the place source titled "… website"); its Maps
    page (the source reference on Google Maps), else a Maps link built from its coordinates.
    """
    links: list[Link] = []
    booking = _get(row, "booking_url")
    if _http(booking):
        links.append(Link(kind="booking", url=booking.strip()))
    refs = _get(row, "source_refs") or []
    website = _get(row, "website")
    if not _http(website):
        website = next(
            (
                _get(ref, "url")
                for ref in refs
                if str(_get(ref, "title") or "").strip().lower().endswith("website")
                and _http(_get(ref, "url"))
            ),
            "",
        )
    if _http(website):
        links.append(Link(kind="official", url=website.strip()))
    maps = next(
        (
            _get(ref, "url")
            for ref in refs
            if _http(_get(ref, "url")) and _is_map(_get(ref, "url"))
        ),
        "",
    )
    maps = maps or map_search_link(_get(row, "geo"))
    if _http(maps):
        links.append(Link(kind="map", url=maps.strip()))
    return _unique(links)


def links_for_stay(stay: HotelStay) -> list[Link]:
    """The hotel source's booking link, and a Maps link from the stay's coordinates."""
    links: list[Link] = []
    if _http(stay.booking_url):
        links.append(Link(kind="booking", url=stay.booking_url.strip()))
    maps = map_search_link(stay.geo)
    if maps:
        links.append(Link(kind="map", url=maps))
    return _unique(links)


def links_for_ticket(ticket: TicketOption) -> list[Link]:
    """A flight's own booking link; the directions link of an estimated leg (or any Maps address) as a Maps link."""
    url = ticket.booking_url
    if not _http(url):
        return []
    kind = "map" if (ticket.estimated or _is_map(url)) else "booking"
    return [Link(kind=kind, url=url.strip())]


def _alternative(alternative: PlanAlternative) -> None:
    alternative.links = links_for_place(alternative)


def _item(item: PlanItem) -> None:
    item.links = links_for_place(item)
    for alternative in item.alternatives:
        _alternative(alternative)


def attach_links(plan: TripPlan) -> None:
    """Write the links of every stop, alternative, stay and transport option of the plan again, from their fields."""
    for _, item in plan.iter_items():
        _item(item)
    for stay in [*plan.hotels, *plan.hotel_options]:
        stay.links = links_for_stay(stay)
    for ticket in plan.tickets:
        ticket.links = links_for_ticket(ticket)
