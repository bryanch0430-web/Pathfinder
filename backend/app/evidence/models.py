"""Evidence: the only facts a plan may contain.

A specialist agent turns a provider answer into :class:`Evidence` records.  The
planner is then asked to *choose among* records, never to invent facts: names,
addresses, coordinates, prices, hours and sources are copied from evidence by
code.  That is what makes "no parametric filling" structural instead of a
prompt request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.domain.models import (
    DataGap,
    GeoPoint,
    ImageRef,
    Link,
    Money,
    SourceRef,
    VideoNote,
    WeatherDay,
    now_iso,
)

EvidenceKind = Literal["attraction", "hotel", "meal", "ticket", "transit", "note"]


@dataclass
class Evidence:
    ref: str
    kind: EvidenceKind
    name: str
    address: str = ""
    geo: GeoPoint | None = None
    summary: str = ""
    source_refs: list[SourceRef] = field(default_factory=list)
    data_status: str = "ok"
    fetched_at: str = field(default_factory=now_iso)
    rating: float | None = None
    review_count: int | None = None
    price_level: str | None = None
    cuisine: str | None = None
    opening_hours: list[str] = field(default_factory=list)
    hours_stated: bool = False
    reservable: bool | None = None
    step_free: bool | None = None
    price: Money | None = None
    price_kind: str = "none"
    booking_url: str = ""
    image: ImageRef | None = None
    links: list[Link] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)
    # Planning hints that are not shown as facts.
    suggested_minutes: int = 90
    best_time: str = "any"
    area: str = ""

    def brief(self) -> dict[str, Any]:
        """The compact form the planner sees: enough to choose, no prose wall."""
        row: dict[str, Any] = {
            "ref": self.ref,
            "name": self.name,
            "kind": self.kind,
            "area": self.area or self.address,
        }
        if self.summary:
            row["about"] = self.summary[:240]
        if self.rating is not None:
            row["rating"] = self.rating
        if self.cuisine:
            row["cuisine"] = self.cuisine
        if self.price is not None:
            row["price"] = f"{self.price.amount:.0f} {self.price.currency}"
        if self.opening_hours:
            row["hours"] = self.opening_hours[:3]
        if self.tags:
            row["tags"] = self.tags[:6]
        row["visit_minutes"] = self.suggested_minutes
        return row

    def to_item_kwargs(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "address": self.address,
            "geo": self.geo,
            "source_refs": self.source_refs,
            "data_status": self.data_status,
            "fetched_at": self.fetched_at,
            "price": self.price,
            "price_kind": self.price_kind,
            "booking_url": self.booking_url,
            "opening_hours": self.opening_hours,
            "hours_status": "stated" if self.hours_stated else "unknown",
            "rating": self.rating,
            "review_count": self.review_count,
            "price_level": self.price_level,
            "cuisine": self.cuisine,
            "reservable": self.reservable,
            "source_types": self.source_types,
            "source_summary": self.summary,
            "links": self.links,
            "image": self.image,
            "step_free": self.step_free,
            "tags": self.tags,
        }

    def to_hotel_kwargs(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "area": self.area,
            "geo": self.geo,
            "price_per_night": self.price,
            "price_kind": self.price_kind,
            "rating": self.rating,
            "review_count": self.review_count,
            "source_refs": self.source_refs,
            "data_status": self.data_status,
            "booking_url": self.booking_url,
            "amenities": self.tags,
            "links": self.links,
            "image": self.image,
        }

    def to_ticket_kwargs(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mode": (self.tags[0] if self.tags else "ticket"),
            "price": self.price,
            "price_kind": self.price_kind,
            "source_refs": self.source_refs,
            "data_status": self.data_status,
            "booking_url": self.booking_url,
            "notes": self.summary,
            "links": self.links,
            "estimated": self.price_kind == "estimate",
        }


@dataclass
class Bundle:
    """Everything the agents found for one turn."""

    items: list[Evidence] = field(default_factory=list)
    gaps: list[DataGap] = field(default_factory=list)
    sources_used: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    weather: list["WeatherDay"] = field(default_factory=list)
    video_notes: list["VideoNote"] = field(default_factory=list)

    def add(self, evidence: list[Evidence], section: str) -> None:
        for entry in evidence:
            if entry.source_refs:
                self.sources_used.update(ref.source for ref in entry.source_refs if ref.source)
            else:
                self.gaps.append(
                    DataGap(
                        section=section,
                        kind="unsourced",
                        message=f"No source confirmed {entry.name}; it is not used in the plan.",
                        retryable=False,
                    )
                )
                continue
            self.items.append(entry)

    def gap(self, section: str, kind: str, message: str, retryable: bool = True) -> None:
        self.gaps.append(DataGap(section=section, kind=kind, message=message, retryable=retryable))

    def of_kind(self, *kinds: str) -> list[Evidence]:
        return [item for item in self.items if item.kind in kinds]

    def get(self, ref: str) -> Evidence | None:
        for item in self.items:
            if item.ref == ref:
                return item
        return None

    def dedupe(self) -> None:
        seen: set[tuple[str, str]] = set()
        unique: list[Evidence] = []
        for item in self.items:
            key = (item.kind, item.name.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        self.items = unique
