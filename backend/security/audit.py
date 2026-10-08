"""Aggregate injection logging without personal data.

Proposal: "Suspected injections are logged in aggregate, without personal data." Only counts are
kept, bucketed by (UTC day, kind, category, path). No message text, no session ids, no user ids,
no IPs are ever accepted by this API: `record` takes enum members only and rejects anything else,
so there is no parameter through which free text or an identifier could be passed. Optionally the
snapshot is persisted as JSON to `path` on each record.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from backend.schemas.common import PathName, utcnow
from backend.schemas.security import (
    AuditBucket,
    AuditSnapshot,
    InjectionCategory,
    ScreenResult,
    SecurityEventKind,
)

_BucketKey = tuple[str, SecurityEventKind, InjectionCategory | None, PathName | None]


class SecurityAudit:
    def __init__(self, path: Path | None = None, *, now: Callable[[], datetime] = utcnow) -> None:
        """`path` is a filesystem location for the JSON snapshot (not a PathName). `now` is an
        injectable clock (tests)."""
        self._file = path
        self._now = now
        self._since = now()
        self._counts: dict[_BucketKey, int] = {}

    def record(
        self,
        kind: SecurityEventKind,
        *,
        category: InjectionCategory | None = None,
        path: PathName | None = None,
    ) -> None:
        if not isinstance(kind, SecurityEventKind):
            raise TypeError("kind must be a SecurityEventKind")
        if category is not None and not isinstance(category, InjectionCategory):
            raise TypeError("category must be an InjectionCategory or None")
        if path is not None and not isinstance(path, PathName):
            raise TypeError("path must be a PathName or None")
        day = self._now().strftime("%Y-%m-%d")
        key: _BucketKey = (day, kind, category, path)
        self._counts[key] = self._counts.get(key, 0) + 1
        self._persist()

    def record_screen(self, result: ScreenResult, *, path: PathName | None = None) -> None:
        """Record one INPUT_FLAGGED event per matched category of a screening result (nothing if
        the message was not flagged). Convenience for the orchestrator; adds no new data."""
        for category in result.categories:
            self.record(SecurityEventKind.INPUT_FLAGGED, category=category, path=path)

    def snapshot(self) -> AuditSnapshot:
        ordered = sorted(
            self._counts.items(),
            key=lambda item: (
                item[0][0],
                item[0][1].value,
                item[0][2].value if item[0][2] is not None else "",
                item[0][3].value if item[0][3] is not None else "",
            ),
        )
        buckets = [
            AuditBucket(kind=kind, category=category, path=path, day=day, count=count)
            for (day, kind, category, path), count in ordered
        ]
        return AuditSnapshot(since=self._since, buckets=buckets)

    def reset(self) -> None:
        """Clear all counts and restart the `since` window."""
        self._counts.clear()
        self._since = self._now()
        self._persist()

    def _persist(self) -> None:
        if self._file is None:
            return
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_name(self._file.name + ".tmp")
            tmp.write_text(self.snapshot().model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, self._file)
        except OSError:
            # Auditing must never break a turn; the in-memory counts remain authoritative.
            return
