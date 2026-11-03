"""Long-term trip memory with a deterministic local embedding.

A saved trip is recalled only above the configured similarity cutoff. The
embedding is a signed hashing of words and character trigrams: no external model,
no personal data, stable across restarts. This is the documented single-node
degraded mode for the vector store in the proposal.
"""

from __future__ import annotations

import hashlib
import math
from typing import Sequence

from app.config import Settings
from app.domain.models import Feedback, PreferenceProfile, SavedTrip, TripContext, TripPlan, utcnow
from app.infrastructure.store import SQLiteStore

DIMENSIONS = 192


def embed(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    tokens = [token for token in _normalise(text).split() if token]
    grams: list[str] = list(tokens)
    for token in tokens:
        padded = f"^{token}$"
        for index in range(max(1, len(padded) - 2)):
            grams.append(padded[index : index + 3])
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % DIMENSIONS
        sign = 1.0 if (value >> 63) & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


def _normalise(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else " " for ch in text)


class MemoryService:
    def __init__(self, store: SQLiteStore, settings: Settings):
        self.store = store
        self.settings = settings

    def recall(self, context: TripContext, *, owner: str) -> list[tuple[float, SavedTrip]]:
        """The owner's own similar saved trips. Another traveller's trip, and
        anything at all for an empty owner, is never recalled."""
        query = " ".join(
            [
                context.destination,
                f"{context.days or ''} days",
                f"{context.party_size or ''} travellers",
                context.hotel_style,
                " ".join(context.preferences),
            ]
        )
        if not context.destination:
            return []
        return self.store.search_saved_trips(
            embedding=embed(query),
            destination=context.destination,
            cutoff=self.settings.memory_similarity_cutoff,
            owner=owner,
            limit=3,
        )

    def save_useful_trip(
        self, plan: TripPlan, *, stars: int | None = None, owner: str | None = None
    ) -> SavedTrip:
        """Remember a useful trip for `owner` (`user:<id>` or a guest token)."""
        text = " ".join(
            [
                plan.destination.name,
                plan.summary,
                " ".join(preference for day in plan.days for item in day.items for preference in item.tags),
                f"{len(plan.days)} days",
                f"{plan.party_size} travellers",
            ]
        )
        saved = SavedTrip(
            trip_id=plan.trip_id,
            destination=plan.destination.name,
            rating=(stars / 5.0) if stars else 1.0,
            stars=stars,
            summary=plan.summary,
            embedding=embed(text),
            plan=plan,
            created_at=utcnow(),
        )
        self.store.save_saved_trip(saved, owner=owner)
        return saved

    def record_feedback(
        self,
        plan: TripPlan,
        *,
        useful: bool,
        stars: int | None,
        reason_code: str,
        owner: str | None = None,
    ) -> Feedback:
        feedback = Feedback(
            trip_id=plan.trip_id,
            useful=useful,
            stars=stars,
            reason_code=reason_code,
        )
        self.store.add_feedback(feedback)
        if useful:
            self.save_useful_trip(plan, stars=stars, owner=owner)
        return feedback

    def remember_preferences(self, owner: str, context: TripContext) -> PreferenceProfile | None:
        """Add a session's preferences to `owner`'s profile (`user:<id>` or a guest token).

        No owner has no profile: an empty key would be shared by every session that has none.
        """
        if not owner:
            return None
        existing = self.store.get_preferences(owner)
        values = list(existing.preferences) if existing else []
        for preference in context.preferences:
            if preference not in values:
                values.append(preference)
        profile = PreferenceProfile(
            client_token=owner,
            preferences=values[-30:],
            updated_at=utcnow(),
        )
        self.store.upsert_preferences(profile)
        return profile

    @staticmethod
    def memory_summaries(matches: Sequence[tuple[float, SavedTrip]]) -> list[str]:
        return [f"{trip.destination} · {trip.summary[:180]}" for _, trip in matches]
