"""Embedding client interface.

TODO(provisional): Appendix B names OpenAI text-embedding-3-large; only the deterministic
offline `HashingEmbeddingClient` is concrete, so everything runs with no API key.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from backend.settings import Settings


class EmbeddingClient(Protocol):
    model: str
    dim: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One L2-normalised vector of length `dim` per input text."""
        ...


# CJK scripts have no spaces between words, so every character is its own token (the adjacent
# bigrams then carry the word-level signal). Everything else is a run of letters/digits;
# `[^\W_]` is the Unicode-aware "alphanumeric" class (underscore excluded).
_CJK_RANGES = "぀-ヿ㐀-䶿一-鿿豈-﫿가-힯"
_TOKEN_RE = re.compile(rf"[{_CJK_RANGES}]|(?:(?![{_CJK_RANGES}])[^\W_])+")


# Fields of a structured text are joined with this; the embedder splits on the bare delimiter.
FIELD_DELIMITER = "|"
FIELD_SEPARATOR = " | "


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric words (Unicode aware; CJK characters split individually)."""
    return _TOKEN_RE.findall(unicodedata.normalize("NFKC", text).lower())


@lru_cache(maxsize=65536)
def _slot(feature: str, dim: int) -> tuple[int, float]:
    """(index, sign) for a feature. blake2b, NOT Python's `hash()`, which is salted per process
    and would make vectors differ between runs (and between the writer and a later reader)."""
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    index = int.from_bytes(digest[:6], "big") % dim
    sign = 1.0 if digest[7] & 1 else -1.0
    return index, sign


class HashingEmbeddingClient:
    """Deterministic feature-hashing embedder (token unigrams + bigrams), L2-normalised, so
    texts sharing destination / categories score high cosine similarity. Offline mock.

    Each unigram and each adjacent-token bigram is hashed to one of `dim` buckets with a
    +/-1 sign (the "hashing trick"). Empty / token-less text maps to the ZERO vector, whose
    cosine similarity with anything is defined as 0 by the repositories.

    Fields. Structured texts are written as `FIELD_SEPARATOR`-delimited fields (see
    `backend.memory.retrieval`). Each field is hashed and L2-normalised on its own before the
    fields are summed, so a long list of place names cannot drown a one-word destination (the
    failure of a plain bag of words: a rich saved plan scores low against a short new request
    for the same city). The FIRST field is the headline - the destination - and counts
    `head_weight` times as much, which is what makes "same city" dominate "same party size".
    Bigrams never span a field boundary. Text with no separator is one field: an ordinary
    normalised hashed bag of words.
    """

    def __init__(
        self,
        dim: int,
        model: str = "mock-hashing-v1",
        *,
        head_weight: float = 3.0,
        bigram_weight: float = 1.0,
    ) -> None:
        if dim < 1:
            raise ValueError("dim must be positive")
        self.dim = dim
        self.model = model
        self._head_weight = head_weight
        self._bigram_weight = bigram_weight

    def _field_features(self, field: str) -> dict[int, float]:
        tokens = tokenize(field)
        features: dict[int, float] = {}
        for token in tokens:
            index, sign = _slot("u:" + token, self.dim)
            features[index] = features.get(index, 0.0) + sign
        for left, right in zip(tokens, tokens[1:], strict=False):
            index, sign = _slot(f"b:{left} {right}", self.dim)
            features[index] = features.get(index, 0.0) + sign * self._bigram_weight
        return features

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for position, field in enumerate(text.split(FIELD_DELIMITER)):
            features = self._field_features(field)
            norm = math.sqrt(sum(v * v for v in features.values()))
            if norm == 0.0:
                continue
            weight = self._head_weight if position == 0 else 1.0
            for index, value in features.items():
                vector[index] += weight * value / norm
        total = math.sqrt(sum(x * x for x in vector))
        if total == 0.0:
            return vector
        return [x / total for x in vector]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]


class OpenAIEmbeddingClient:
    """TODO(provisional): OpenAI text-embedding-3-large (Appendix B). Not implemented: raises
    `NotImplementedError` on construction so a misconfigured deployment fails at startup."""

    model: str
    dim: int

    def __init__(self, *, api_key: str, model: str, dim: int) -> None:
        # TODO(provisional): OpenAI text-embedding-3-large (request `dimensions=dim`).
        raise NotImplementedError("TODO(provisional): OpenAI text-embedding-3-large")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        # TODO(provisional): OpenAI text-embedding-3-large
        raise NotImplementedError("TODO(provisional): OpenAI text-embedding-3-large")


def build_embedding_client(settings: Settings) -> EmbeddingClient:
    if settings.embedding_provider == "mock":
        # The mock keeps its own model name: a hashing vector must never be stored under (or
        # compared with) the name of a real embedding model.
        return HashingEmbeddingClient(dim=settings.embedding_dim)
    if settings.embedding_provider == "openai":
        # TODO(provisional): OpenAI text-embedding-3-large
        return OpenAIEmbeddingClient(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.embedding_model,
            dim=settings.embedding_dim,
        )
    raise ValueError(f"unknown embedding_provider: {settings.embedding_provider!r}")
