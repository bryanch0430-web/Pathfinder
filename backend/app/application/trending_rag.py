"""Retrieval over saved journeys for the trending rail.

At platform scale a full scan with a Counter does not answer "what did people
like me plan". This module generates candidates with two retrievers over the
kept journeys and fuses them:

    BM25 (lexical)  +  dense vector (the embedding stored on each saved trip)
        -> reciprocal rank fusion  ->  top-N journeys
        -> `trending.aggregate` turns them into destination cards

A free-text query, the current destination, and the caller's own preference
tags form the query. With none of them the rail is a popularity aggregate.

The dense channel uses the same 192-dimension hashing embedder as trip memory,
so the index needs no model download and no extra column. The scoring functions
are deliberately small and pure Python: 5,000 journeys stay well under a
millisecond per query.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from app.application.memory import embed
from app.application.trending import aggregate
from app.domain.models import SavedTrip
from app.infrastructure.store import SQLiteStore

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "trip", "trips", "day", "days",
    "plan", "plans", "traveller", "travellers", "traveller", "trip", "a", "an", "to", "in",
    "of", "on", "at", "is", "it", "my", "our", "we", "i",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def terms(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in STOPWORDS]


class BM25:
    """Small, dependency-free Okapi BM25."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1 = k1
        self.b = b
        self.count = len(docs)
        self.avg_len = sum(len(doc) for doc in docs) / max(1, self.count)
        self.df: Counter[str] = Counter()
        for doc in docs:
            self.df.update(set(doc))
        self.idf = {
            term: math.log(1 + (self.count - freq + 0.5) / (freq + 0.5))
            for term, freq in self.df.items()
        }

    def scores(self, query: Sequence[str]) -> list[float]:
        out: list[float] = []
        for doc in self.docs:
            tf = Counter(doc)
            total = 0.0
            for term in query:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                denom = freq + self.k1 * (1 - self.b + self.b * len(doc) / max(1e-9, self.avg_len))
                total += self.idf.get(term, 0.0) * freq * (self.k1 + 1) / denom
            out.append(total)
        return out


@dataclass(slots=True)
class Doc:
    trip: SavedTrip
    text: str
    embedding: list[float]
    tokens: list[str]


def _document(trip: SavedTrip) -> Doc | None:
    plan = trip.plan
    if plan is None or not plan.days:
        return None
    places = [
        item.name
        for day in plan.days
        for item in day.items
        if item.type.value == "attraction" and item.name
    ][:10]
    tags = sorted({tag for day in plan.days for item in day.items for tag in item.tags})
    year = plan.dates.start.year if plan.dates and plan.dates.start else ""
    text = " ".join(
        part
        for part in [
            plan.destination.name or trip.destination,
            f"{len(plan.days)} day",
            str(year),
            " ".join(places),
            " ".join(tags),
            trip.summary[:180],
        ]
        if part
    )
    embedding = trip.embedding or embed(text)
    return Doc(trip=trip, text=text, embedding=embedding, tokens=terms(text))


class TrendingIndex:
    def __init__(self, docs: list[Doc]):
        self.docs = docs
        self.bm25 = BM25([doc.tokens for doc in docs])
        self.norms = [math.sqrt(sum(value * value for value in doc.embedding)) or 1.0 for doc in docs]

    def dense(self, vector: Sequence[float]) -> list[float]:
        return [
            sum(a * b for a, b in zip(vector, doc.embedding)) / self.norms[index]
            for index, doc in enumerate(self.docs)
        ]

    def search(self, query_text: str, limit: int) -> tuple[list[SavedTrip], list[float], str]:
        query_terms = terms(query_text)
        if not query_terms:
            return [doc.trip for doc in self.docs[:limit]], [0.0] * min(limit, len(self.docs)), "popularity"

        lexical = self.bm25.scores(query_terms)
        dense = self.dense(embed(query_text))
        if not any(lexical) and not any(score > 0.02 for score in dense):
            # No honest signal: do not pretend a hybrid match exists.
            return [doc.trip for doc in self.docs[:limit]], [0.0] * min(limit, len(self.docs)), "popularity"

        lexical_rank = {index: rank for rank, index in enumerate(sorted(range(len(lexical)), key=lambda i: lexical[i], reverse=True))}
        dense_rank = {index: rank for rank, index in enumerate(sorted(range(len(dense)), key=lambda i: dense[i], reverse=True))}
        fused = [
            1.0 / (60 + lexical_rank[index]) + 1.0 / (60 + dense_rank[index])
            for index in range(len(self.docs))
        ]
        peak = max(fused) or 1.0
        fused = [value / peak for value in fused]
        order = sorted(range(len(fused)), key=lambda index: fused[index], reverse=True)[:limit]
        return [self.docs[index].trip for index in order], [fused[index] for index in order], "hybrid"


_LOCK = threading.Lock()
_CACHE: dict[str, object] = {"key": None, "index": None}

INDEX_LIMIT = 5000


def _key(trips: list[SavedTrip]) -> tuple[int, str | None]:
    newest: datetime | None = trips[0].created_at if trips else None
    return len(trips), newest.isoformat() if newest else None


def invalidate() -> None:
    """Drop the cached index (tests, seeding, or an out-of-band DB change)."""
    with _LOCK:
        _CACHE["key"] = None
        _CACHE["index"] = None


def get_index(store: SQLiteStore) -> TrendingIndex:
    trips = store.list_saved_trips(INDEX_LIMIT)
    key = _key(trips)
    with _LOCK:
        if _CACHE.get("key") == key and isinstance(_CACHE.get("index"), TrendingIndex):
            return _CACHE["index"]  # type: ignore[return-value]
        docs = [doc for trip in trips if (doc := _document(trip)) is not None]
        index = TrendingIndex(docs)
        _CACHE["key"] = key
        _CACHE["index"] = index
        return index


def trending_report(
    store: SQLiteStore,
    query: str = "",
    limit: int = 6,
    *,
    preferences: Sequence[str] | None = None,
    destination: str = "",
) -> dict:
    index = get_index(store)
    query_text = " ".join(
        part for part in [query.strip(), destination.strip(), " ".join(preferences or [])] if part
    )
    candidates, candidate_scores, mode = index.search(query_text, max(200, limit * 30))
    if not index.docs:
        return aggregate([], query=query, limit=limit, preferences=preferences, mode="popularity")

    score_by_trip = {trip.trip_id: score for trip, score in zip(candidates, candidate_scores)}
    if mode == "hybrid":
        # Rank only destinations that retrieval actually matched, but count and
        # describe them over every kept journey of that destination.
        wanted = {(trip.destination or "").casefold() for trip in candidates}
        docs = [doc for doc in index.docs if (doc.trip.destination or "").casefold() in wanted]
    else:
        docs = list(index.docs)

    plans = [doc.trip.plan for doc in docs if doc.trip.plan is not None]
    scores = [score_by_trip.get(doc.trip.trip_id, 0.0) for doc in docs if doc.trip.plan is not None]
    return aggregate(
        plans,
        scores=scores,
        preferences=preferences,
        query=query,
        limit=limit,
        mode=mode,
    )
