"""Report plumbing: a common base for metric reports, the summary row printed by the CLI, and
name matching shared by the code-scored metrics."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from pydantic import BaseModel, Field

from backend.schemas.common import utcnow


class SummaryRow(BaseModel):
    metric: str
    split: str
    n: int
    headline: str
    target: str
    met: bool | None = None


class MetricReport(BaseModel):
    metric: str
    split: str
    generated_at: datetime = Field(default_factory=utcnow)
    notes: list[str] = Field(default_factory=list)

    def summary_row(self) -> SummaryRow:  # overridden by every metric report
        return SummaryRow(metric=self.metric, split=self.split, n=0, headline="", target="")


def fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def fmt_num(value: float | None, digits: int = 0) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


# ---- name matching (venue names, categories) ------------------------------------------------------


def norm(text: str) -> str:
    """Case-, accent- and punctuation-insensitive form: "Kinkaku-ji (Golden Pavilion)" ->
    "kinkaku ji golden pavilion"."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii") or text
    return " ".join(re.sub(r"[^0-9a-z]+", " ", folded.casefold().replace("'", "")).split())


def _variants(name: str) -> set[str]:
    out = {norm(name)}
    match = re.match(r"^(.*?)\s*\((.*)\)\s*$", name)
    if match:
        out |= {norm(match.group(1)), norm(match.group(2))}
    return {v for v in out if v}


def names_match(a: str, b: str) -> bool:
    """Same place name: exact after normalisation (with or without a parenthetical), or one
    contains the other as whole words when the contained part is specific (2+ words or 8+
    characters), so "teamLab Planets" matches "teamLab Planets TOKYO" but "museum" matches
    nothing."""
    va, vb = _variants(a), _variants(b)
    if va & vb:
        return True
    for x in va:
        for y in vb:
            inner, outer = (x, y) if len(x) <= len(y) else (y, x)
            specific = " " in inner or len(inner) >= 8
            if specific and f" {inner} " in f" {outer} ":
                return True
    return False


def category_match(wanted: str, category: str) -> bool:
    """'theme_park' == 'theme park' == 'theme parks'; 'museums' == 'museum'."""

    def base(text: str) -> str:
        words = norm(text.replace("_", " ")).split()
        return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)

    return bool(base(wanted)) and base(wanted) == base(category)
