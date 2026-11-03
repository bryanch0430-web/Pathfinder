"""Local hybrid guide retrieval: BM25 + BGE-small, then BGE cross-encoder.

No API key and no network at request time (models are downloaded once). If the
models cannot load, the caller falls back to the lexical retriever and says so.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections import Counter
from pathlib import Path

from app.application.soft_guides import CORPUS_VERSION, GUIDES, GuideChunk, _matches, _stale

logger = logging.getLogger(__name__)

# Keep the downloaded models inside the project so the demo is reproducible.
os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[3] / ".cache" / "huggingface"))

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "BAAI/bge-reranker-base"

_lock = threading.RLock()
_embedder = None
_reranker = None
_doc_matrix = None
_bm25 = None


def _terms(text: str) -> list[str]:
    import re

    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1 = k1
        self.b = b
        self.avgdl = sum(len(doc) for doc in docs) / max(1, len(docs))
        self.df: Counter[str] = Counter()
        for doc in docs:
            self.df.update(set(doc))

    def scores(self, query: list[str]) -> list[float]:
        n = len(self.docs)
        result = []
        for doc in self.docs:
            tf = Counter(doc)
            score = 0.0
            for term in query:
                if term not in tf:
                    continue
                idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                freq = tf[term]
                score += idf * (freq * (self.k1 + 1)) / (
                    freq + self.k1 * (1 - self.b + self.b * len(doc) / self.avgdl)
                )
            result.append(score)
        return result


def _load_embedder():
    global _embedder
    if _embedder is not None:
        return _embedder
    from transformers import AutoModel, AutoTokenizer
    import torch

    with _lock:
        if _embedder is None:
            tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL)
            model = AutoModel.from_pretrained(EMBED_MODEL)
            model.eval()
            _embedder = (tokenizer, model, torch)
    return _embedder


def _encode(texts: list[str]):
    tokenizer, model, torch = _load_embedder()
    batch = tokenizer(texts, padding=True, truncation=True, max_length=512, return_tensors="pt")
    with torch.no_grad():
        output = model(**batch)
    mask = batch["attention_mask"].unsqueeze(-1)
    pooled = (output.last_hidden_state * mask).sum(1) / mask.sum(1).clamp(min=1)
    return torch.nn.functional.normalize(pooled, p=2, dim=1)


def _index():
    global _doc_matrix, _bm25
    if _doc_matrix is not None:
        return _doc_matrix, _bm25
    with _lock:
        if _doc_matrix is None:
            _doc_matrix = _encode([f"{chunk.section}. {chunk.text}" for chunk in GUIDES])
            _bm25 = BM25([_terms(f"{chunk.section} {chunk.title} {chunk.text}") for chunk in GUIDES])
    return _doc_matrix, _bm25


def _load_reranker():
    global _reranker
    if _reranker is not None:
        return _reranker
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    import torch

    with _lock:
        if _reranker is None:
            tokenizer = AutoTokenizer.from_pretrained(RERANK_MODEL)
            model = AutoModelForSequenceClassification.from_pretrained(RERANK_MODEL)
            model.eval()
            _reranker = (tokenizer, model, torch)
    return _reranker


def _rerank(query: str, rows: list[tuple[float, GuideChunk]], limit: int) -> tuple[list[tuple[float, GuideChunk]], bool]:
    try:
        tokenizer, model, torch = _load_reranker()
        with torch.no_grad():
            batch = tokenizer(
                [query] * len(rows),
                [chunk.text for _, chunk in rows],
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            scores = model(**batch).logits.view(-1).tolist()
        ranked = sorted(
            ((float(score), chunk) for score, (_, chunk) in zip(scores, rows)),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return ranked[:limit], True
    except Exception:
        return rows[:limit], False


def retrieve_bm25(destination: str, query: str = "", limit: int = 3) -> list[dict]:
    candidates = [(index, chunk) for index, chunk in enumerate(GUIDES) if _matches(chunk, destination)]
    if not candidates:
        return []
    try:
        _, bm25 = _index()
        scores = bm25.scores(_terms(query or destination))
        ranked = sorted(candidates, key=lambda cur: scores[cur[0]], reverse=True)[:limit]
        return [
            {**chunk_payload(chunk), "score": round(float(scores[index]), 4), "retrieval": {"mode": "bm25"}}
            for index, chunk in ranked
        ]
    except Exception:
        from app.application.soft_guides import retrieve

        return retrieve(destination, query, limit)


def retrieve_dense(destination: str, query: str = "", limit: int = 3) -> list[dict]:
    candidates = [(index, chunk) for index, chunk in enumerate(GUIDES) if _matches(chunk, destination)]
    if not candidates:
        return []
    try:
        doc_matrix, _ = _index()
        query_vec = _encode([query or destination])[0]
        scores = (doc_matrix @ query_vec).tolist()
        ranked = sorted(candidates, key=lambda cur: scores[cur[0]], reverse=True)[:limit]
        return [
            {**chunk_payload(chunk), "score": round(float(scores[index]), 4), "retrieval": {"mode": "dense"}}
            for index, chunk in ranked
        ]
    except Exception:
        from app.application.soft_guides import retrieve

        return retrieve(destination, query, limit)


def retrieve_hybrid(destination: str, query: str = "", limit: int = 3, use_reranker: bool = False) -> tuple[list[dict], dict]:
    candidates = [(index, chunk) for index, chunk in enumerate(GUIDES) if _matches(chunk, destination)]
    if not candidates:
        return [], {"mode": "hybrid", "reranked": False, "reason": "no destination match"}

    try:
        doc_matrix, bm25 = _index()
        query_terms = _terms(query or destination)
        lexical = bm25.scores(query_terms)
        query_vec = _encode([query or destination])[0]
        dense = (doc_matrix @ query_vec).tolist()
        lexical_rank = {idx: rank for rank, (idx, _) in enumerate(
            sorted(candidates, key=lambda cur: lexical[cur[0]], reverse=True)
        )}
        dense_rank = {idx: rank for rank, (idx, _) in enumerate(
            sorted(candidates, key=lambda cur: dense[cur[0]], reverse=True)
        )}
        fused = []
        for index, chunk in candidates:
            # Reciprocal rank fusion, then a small lexical prior.
            score = 1 / (60 + lexical_rank[index]) + 1 / (60 + dense_rank[index])
            fused.append((score, chunk, lexical[index], dense[index]))
        fused.sort(key=lambda row: row[0], reverse=True)
        pool = [(score, chunk) for score, chunk, _, _ in fused[:20]]
        reranked, did_rerank = _rerank(query or destination, pool, limit) if use_reranker else (pool[:limit], False)
        rows = []
        for score, chunk in reranked:
            rows.append({
                **chunk_payload(chunk),
                "score": round(float(score), 5),
                "retrieval": {
                    "mode": "hybrid+rerank" if did_rerank else "hybrid",
                    "reranked": did_rerank,
                    "lexical_score": round(float(lexical[GUIDES.index(chunk)]), 4),
                    "dense_score": round(float(dense[GUIDES.index(chunk)]), 4),
                },
            })
        return rows, {"mode": "hybrid+rerank" if did_rerank else "hybrid", "reranked": did_rerank}
    except Exception as exc:
        from app.application.soft_guides import retrieve

        # Why it failed (a missing package, a path) is for the log; the caller is told only that it did.
        logger.info("hybrid retrieval is unavailable, using the lexical fallback: %s", exc)
        rows = retrieve(destination, query, limit)
        return rows, {"mode": "lexical-fallback", "reranked": False, "reason": "hybrid retrieval is unavailable"}


def chunk_payload(chunk: GuideChunk) -> dict:
    return {
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
    }
