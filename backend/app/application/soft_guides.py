"""Versioned soft-guide corpus and citation-first retrieval.

Documents live beside this module as Markdown files with frontmatter. They cover
only slow-changing advice (neighbourhoods, timing, etiquette). Prices, opening
hours and availability stay in the live evidence agents.

The retriever is lexical (BM25-like overlap) so it needs no model download; the
interface is ready for an embedding/cross-encoder reranker later.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

GUIDE_DIR = Path(__file__).resolve().parents[1] / "guides"
STALE_AFTER_DAYS = 365


@dataclass(frozen=True)
class GuideChunk:
    id: str
    destination: str
    country_code: str
    section: str
    text: str
    title: str
    url: str
    license: str
    last_verified: str
    aliases: tuple[str, ...]
    content_hash: str


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, parts[2].strip()


def _load() -> list[GuideChunk]:
    chunks: list[GuideChunk] = []
    for path in sorted(GUIDE_DIR.glob("*.md")):
        meta, text = _parse_frontmatter(path.read_text(encoding="utf-8"))
        if not text or not meta.get("id"):
            continue
        aliases = tuple(
            value.strip().lower() for value in meta.get("aliases", "").split(",") if value.strip()
        )
        chunks.append(
            GuideChunk(
                id=meta["id"],
                destination=meta.get("destination", "").lower(),
                country_code=meta.get("country_code", "").upper(),
                section=meta.get("section", ""),
                text=text,
                title=meta.get("title", meta.get("section", meta["id"])),
                url=meta.get("url", ""),
                license=meta.get("license", "Original summary; source cited"),
                last_verified=meta.get("last_verified", ""),
                aliases=aliases,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
            )
        )
    return chunks


GUIDES = _load()
CORPUS_VERSION = hashlib.sha256(
    "\n".join(f"{chunk.id}:{chunk.content_hash}" for chunk in GUIDES).encode("utf-8")
).hexdigest()[:12]

STOPWORDS = {
    "the", "and", "a", "an", "to", "of", "in", "on", "for", "with", "is", "are",
    "this", "that", "it", "at", "by", "from", "or", "as", "be", "we", "you",
}


def _terms(text: str) -> Counter[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return Counter(word for word in words if len(word) > 2 and word not in STOPWORDS)


def _matches(chunk: GuideChunk, destination: str) -> bool:
    city = destination.strip().lower()
    return (
        chunk.destination == city
        or city in chunk.aliases
        or (chunk.destination and chunk.destination in city)
    )


def _stale(last_verified: str) -> bool:
    try:
        return (date.today() - date.fromisoformat(last_verified)).days > STALE_AFTER_DAYS
    except ValueError:
        return True


def retrieve(destination: str, query: str = "", limit: int = 3) -> list[dict]:
    candidates = [chunk for chunk in GUIDES if _matches(chunk, destination)]
    query_terms = _terms(query)
    scored: list[tuple[float, GuideChunk]] = []
    for chunk in candidates:
        terms = _terms(f"{chunk.section} {chunk.title} {chunk.text}")
        overlap = sum(min(count, terms[word]) for word, count in query_terms.items())
        score = overlap + math.log1p(sum(terms.values())) / 20.0
        scored.append((score, chunk))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "id": chunk.id,
            "destination": chunk.destination,
            "country_code": chunk.country_code,
            "section": chunk.section,
            "text": chunk.text,
            "title": chunk.title,
            "url": chunk.url,
            "license": chunk.license,
            "last_verified": chunk.last_verified,
            "stale": _stale(chunk.last_verified),
            "content_hash": chunk.content_hash,
            "corpus_version": CORPUS_VERSION,
            "score": round(score, 4),
        }
        for score, chunk in scored[:limit]
    ]
