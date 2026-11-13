"""Paid searches kept for the length of a session.

A multi-city trip searches a hotel for every city and a flight for every leg, and an edit that changes one city should
pay for that city only. Results are kept per session, by what was asked (place and dates, never who asked), and are
ignored once they are older than ``MAX_AGE``. ``CachedToolbox`` puts the cache in front of ``hotels.search`` and
``flights.search`` for the one-way searches a multi-city trip makes; everything else goes through as it did.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.toolbox import Toolbox
from app.domain.models import FlightCandidate, HotelCandidate

MAX_AGE = timedelta(hours=6)

#: The tool whose results are kept, the kind they are filed under and the model a kept row is read back as.
_TOOLS: dict[str, tuple[str, type]] = {
    "hotels.search": ("hotels", HotelCandidate),
    "flights.search": ("flights", FlightCandidate),
}


def _now() -> datetime:
    return datetime.now(UTC)


class SearchCache:
    """Results by kind and key for one session, in the store (or, with no store, in memory)."""

    def __init__(
        self,
        store: Any | None,
        session_id: str,
        *,
        clock: Callable[[], datetime] = _now,
        max_age: timedelta = MAX_AGE,
    ) -> None:
        self.store = store
        self.session_id = session_id
        self.clock = clock
        self.max_age = max_age
        self._memory: dict[tuple[str, str], tuple[str, str]] = {}

    @staticmethod
    def _text(key: tuple) -> str:
        return json.dumps(list(key), default=str)

    def get(self, kind: str, key: tuple) -> list | None:
        """The rows kept for ``key``, or None when none were kept or they are older than the limit."""
        text = self._text(key)
        found = (
            self.store.get_search_rows(self.session_id, kind, text)
            if self.store is not None
            else self._memory.get((kind, text))
        )
        if found is None:
            return None
        rows, stored_at = found
        try:
            kept = datetime.fromisoformat(stored_at)
        except ValueError:
            return None
        if kept.tzinfo is None:
            kept = kept.replace(tzinfo=UTC)
        if self.clock() - kept > self.max_age:
            return None
        return json.loads(rows)

    def put(self, kind: str, key: tuple, rows: list) -> None:
        text, stored = self._text(key), self.clock().isoformat()
        payload = json.dumps(rows, default=str)
        if self.store is not None:
            self.store.put_search_rows(self.session_id, kind, text, payload, stored)
        else:
            self._memory[(kind, text)] = (payload, stored)


def search_key(tool: str, kwargs: dict[str, Any]) -> tuple | None:
    """What a hotel or flight search was asked, as the key its results are kept under; None when it is not kept."""
    try:
        if tool == "hotels.search":
            return (
                str(kwargs["destination"]).strip().casefold(),
                kwargs["check_in"].isoformat(),
                kwargs["check_out"].isoformat(),
                kwargs["party_size"],
                str(kwargs["currency"]).upper(),
            )
        if tool == "flights.search" and kwargs.get("return_date") is None:
            return (
                str(kwargs["origin"]).strip().casefold(),
                str(kwargs["destination"]).strip().casefold(),
                kwargs["depart_date"].isoformat(),
                kwargs["party_size"],
                str(kwargs["currency"]).upper(),
            )
    except (KeyError, AttributeError):
        return None
    return None


class CachedToolbox(Toolbox):
    """A toolbox that answers a hotel or one-way flight search from the session's cache when it can.

    It shares everything with the toolbox it wraps (the count of calls, the cap, the log), so a search that is
    answered from the cache is not a tool call: nothing is logged and nothing is counted.
    """

    def __init__(self, inner: Toolbox, cache: SearchCache) -> None:
        self.__dict__ = inner.__dict__
        self._search_cache = cache

    async def call(self, tool: str, fn: Callable[..., Any], /, **kwargs: Any) -> Any:
        kept = _TOOLS.get(tool)
        key = search_key(tool, kwargs) if kept else None
        if kept and key is not None:
            kind, model = kept
            rows = self._search_cache.get(kind, key)
            if rows is not None:
                return [model.model_validate(row) for row in rows]
        result = await super().call(tool, fn, **kwargs)
        if kept and key is not None and result:
            self._search_cache.put(
                kept[0], key, [row.model_dump(mode="json") for row in result]
            )
        return result
