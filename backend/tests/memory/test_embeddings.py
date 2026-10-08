"""Hashing embedder: determinism, normalisation, tokenisation, factory."""

from __future__ import annotations

import math
import os
import subprocess
import sys

import pytest

from backend.db.repositories.memory import cosine_similarity
from backend.memory.embeddings import (
    HashingEmbeddingClient,
    OpenAIEmbeddingClient,
    build_embedding_client,
    tokenize,
)
from backend.tests.memory.helpers import default_settings


async def embed_one(client: HashingEmbeddingClient, text: str) -> list[float]:
    [vector] = await client.embed([text])
    return vector


async def test_embedding_is_deterministic_and_l2_normalised() -> None:
    client = HashingEmbeddingClient(dim=256)
    a = await embed_one(client, "kyoto | days: 3 | party: 2")
    b = await embed_one(HashingEmbeddingClient(dim=256), "kyoto | days: 3 | party: 2")
    assert a == b
    assert len(a) == 256 == client.dim
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-9)


async def test_embed_returns_one_vector_per_text_in_order() -> None:
    client = HashingEmbeddingClient(dim=64)
    vectors = await client.embed(["alpha beta", "gamma", "alpha beta"])
    assert len(vectors) == 3
    assert vectors[0] == vectors[2]
    assert vectors[0] != vectors[1]


async def test_case_and_punctuation_do_not_change_the_vector() -> None:
    client = HashingEmbeddingClient(dim=128)
    assert await embed_one(client, "Kyoto, JAPAN!") == await embed_one(client, "kyoto japan")


@pytest.mark.parametrize("text", ["", "   ", "|||", "!!! ??? ---"])
async def test_text_without_tokens_embeds_to_the_zero_vector(text: str) -> None:
    client = HashingEmbeddingClient(dim=32)
    vector = await embed_one(client, text)
    assert vector == [0.0] * 32
    # ... and cosine against a zero vector is defined as 0, never NaN.
    other = await embed_one(client, "kyoto")
    assert cosine_similarity(vector, other) == 0.0
    assert cosine_similarity(vector, vector) == 0.0


def test_tokenize_is_unicode_aware_and_splits_cjk_characters() -> None:
    assert tokenize("Kyoto-2 trip, café_au_lait") == ["kyoto", "2", "trip", "café", "au", "lait"]
    assert tokenize("東京 trip") == ["東", "京", "trip"]
    assert tokenize("ＫＹＯＴＯ") == ["kyoto"]  # NFKC folds full-width letters


async def test_shared_vocabulary_scores_higher_than_unrelated_text() -> None:
    client = HashingEmbeddingClient(dim=1536)
    base = await embed_one(client, "kyoto temple shrine garden")
    related = await embed_one(client, "kyoto temple tour")
    unrelated = await embed_one(client, "hong kong skyline harbour")
    assert cosine_similarity(base, related) > cosine_similarity(base, unrelated) + 0.3


async def test_cjk_destination_matches_itself_not_a_different_city() -> None:
    client = HashingEmbeddingClient(dim=1536)
    tokyo = await embed_one(client, "東京 | days: 3")
    tokyo_again = await embed_one(client, "東京 | days: 3")
    kyoto = await embed_one(client, "京都 | days: 3")
    assert cosine_similarity(tokyo, tokyo_again) == pytest.approx(1.0)
    assert cosine_similarity(tokyo, kyoto) < 0.75


async def test_headline_field_outweighs_a_long_trailing_field() -> None:
    """Field-wise normalisation: a long place list cannot drown the destination."""
    client = HashingEmbeddingClient(dim=1536)
    short = await embed_one(client, "kyoto | days: 3")
    long_tail = await embed_one(
        client, "kyoto | days: 3 | places: " + " ".join(f"landmark{i}" for i in range(40))
    )
    assert cosine_similarity(short, long_tail) > 0.75


def test_hash_does_not_depend_on_pythons_per_process_hash_seed() -> None:
    """The feature hash is blake2b, not hash(): vectors written by one process must be
    comparable with vectors computed by another (the writer and the retriever)."""
    code = (
        "import asyncio, hashlib, json;"
        "from backend.memory.embeddings import HashingEmbeddingClient;"
        "v = asyncio.run(HashingEmbeddingClient(64).embed(['kyoto temple | days: 3']))[0];"
        "print(hashlib.sha256(json.dumps(v).encode()).hexdigest())"
    )
    digests = set()
    for seed in ("1", "424242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            check=True,
            cwd=os.getcwd(),
        )
        digests.add(out.stdout.strip())
    assert len(digests) == 1


def test_build_embedding_client_mock_uses_configured_dimension() -> None:
    settings = default_settings(embedding_provider="mock", embedding_dim=48)
    client = build_embedding_client(settings)
    assert isinstance(client, HashingEmbeddingClient)
    assert client.dim == 48
    # The mock must not pose as the real model: vectors are stored under their model name.
    assert client.model == "mock-hashing-v1" != settings.embedding_model


def test_build_embedding_client_openai_is_provisional() -> None:
    with pytest.raises(NotImplementedError, match="TODO\\(provisional\\)"):
        build_embedding_client(default_settings(embedding_provider="openai"))
    with pytest.raises(NotImplementedError):
        OpenAIEmbeddingClient(api_key="", model="text-embedding-3-large", dim=1536)
