"""A list of SerpAPI keys. One is in use. The next ready key takes over when that account runs out.

Paused and exhausted keys stay in the list and are skipped until an admin turns them back on.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.infrastructure.http import request_json

logger = logging.getLogger(__name__)

STATUSES = frozenset({"active", "ready", "exhausted", "paused"})
_QUOTA = re.compile(
    r"run out of searches|out of searches|quota|depleted|invalid api key|api key.{0,24}expired",
    re.IGNORECASE,
)

_ring: SerpApiRing | None = None


def quota_failure(detail: str) -> bool:
    """True when this SerpAPI answer means the key itself cannot search, so the next key should try."""
    return bool(_QUOTA.search(detail or ""))


def parse_pool(raw: str) -> list[tuple[str, str, str]]:
    """``status|label|key`` rows separated by semicolons. A second active row waits as ready."""
    rows: list[tuple[str, str, str]] = []
    seen_active = False
    for piece in (raw or "").split(";"):
        piece = piece.strip()
        if not piece:
            continue
        parts = [part.strip() for part in piece.split("|")]
        if len(parts) != 3:
            continue
        status, label, key = parts
        if status not in STATUSES or len(key) < 8 or any(character.isspace() for character in key):
            continue
        if status == "active":
            if seen_active:
                status = "ready"
            seen_active = True
        rows.append((status, label[:80], key))
    return rows


class SerpApiRing:
    def __init__(self, store: Any, box: Any, settings: Settings):
        self.store = store
        self.box = box
        self.settings = settings

    def seed(self, entries: list[tuple[str, str, str]]) -> None:
        """Fill an empty table. A later start leaves keys an admin has changed."""
        if self.store.serpapi_count():
            return
        for status, label, key in entries:
            if self.store.serpapi_known(self.box, key):
                continue
            self.store.serpapi_add(
                key_id="s" + secrets.token_hex(8),
                ciphertext=self.box.encrypt(key),
                key_tail=key[-4:],
                label=label,
                status=status,
            )
        self.apply()

    def apply(self) -> None:
        """Point the live setting at the key marked in use."""
        self.settings.serpapi_key = self.store.serpapi_active_plain(self.box)

    def current(self) -> str:
        key = self.store.serpapi_active_plain(self.box)
        if key:
            self.settings.serpapi_key = key
            return key
        return str(self.settings.serpapi_key or "")

    def retire(self, used: str) -> str:
        """The key that just failed becomes used up. The next ready key is returned, or nothing."""
        nxt = self.store.serpapi_retire(self.box, used)
        self.settings.serpapi_key = nxt
        if nxt:
            logger.info("serpapi: the key ending %s is used up; the next key is in use", used[-4:])
        else:
            logger.info("serpapi: the key ending %s is used up; no ready key is left", used[-4:])
        return nxt


def bind_ring(ring: SerpApiRing | None) -> None:
    global _ring
    _ring = ring


def current_ring() -> SerpApiRing | None:
    return _ring


def install_ring(store: Any, box: Any, settings: Settings) -> SerpApiRing:
    """Seed from the pool (or the single environment key) and use the active key for searches."""
    ring = SerpApiRing(store, box, settings)
    entries = parse_pool(settings.serpapi_key_pool)
    if not entries and settings.serpapi_key:
        entries = [("active", "", settings.serpapi_key)]
    ring.seed(entries)
    ring.apply()
    bind_ring(ring)
    return ring


async def serpapi_json(
    settings: Settings,
    *,
    source: str,
    params: dict[str, Any],
    timeout: float,
    retries: int,
    user_agent: str,
) -> dict[str, Any]:
    """One SerpAPI search. A used-up key is retired and the same search is tried with the next ready key."""
    ring = current_ring()
    tried: set[str] = set()
    while True:
        key = ring.current() if ring is not None else str(settings.serpapi_key or "")
        if not key:
            raise ProviderNoData("SerpApi key is not configured", source=source)
        if key in tried:
            raise ProviderError(f"{source} returned an error", source=source, detail="No ready SerpAPI key is left")
        tried.add(key)
        try:
            data = await request_json(
                "GET",
                "https://serpapi.com/search.json",
                source=source,
                params={**params, "api_key": key},
                timeout=timeout,
                retries=retries,
                user_agent=user_agent,
            )
        except ProviderError as exc:
            if ring is not None and quota_failure(f"{exc} {exc.detail}"):
                if ring.retire(key):
                    continue
            raise
        error = data.get("error") if isinstance(data, dict) else None
        if error and quota_failure(str(error)):
            if ring is not None and ring.retire(key):
                continue
            raise ProviderError(f"{source} returned an error", source=source, detail=str(error)[:300])
        if error:
            raise ProviderError(f"{source} returned an error", source=source, detail=str(error)[:300])
        return data if isinstance(data, dict) else {}


def public_rows(store: Any) -> list[dict[str, Any]]:
    """Rows safe to show: label, tail and status. Never the key."""
    return [
        {
            "id": row["id"],
            "label": row["label"],
            "key_tail": row["key_tail"],
            "status": row["status"],
            "position": row["position"],
        }
        for row in store.serpapi_keys()
    ]

