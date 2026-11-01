"""Paraphrase-set ablation: lexical vs BM25 vs dense vs hybrid vs rerank.

Run: PYTHONPATH=. python -m app.guide_ablation
"""

from __future__ import annotations

import time

from app.application.guide_hybrid import retrieve_bm25, retrieve_dense, retrieve_hybrid
from app.application.soft_guides import retrieve as retrieve_lexical

CASES = [
    ("Hong Kong", "giant bronze Buddha and a stilt fishing village", "hk-lantau"),
    ("Hong Kong", "independent shops and the escalator walk", "hk-central"),
    ("Hong Kong", "after-dark market and a view across the harbour", "hk-kowloon"),
    ("Hong Kong", "tour buses arrive late morning", "hk-lantau"),
    ("Tokyo", "avoid changing trains too often in the capital", "japan-tokyo"),
    ("Kyoto", "where to group temples so I do not climb in the heat", "japan-kyoto"),
    ("Kyoto", "a quiet pause between temple visits", "japan-kyoto"),
    ("Japan", "send a suitcase ahead while travelling by rail", "japan-transport"),
    ("New York", "museums without long underground rides", "usa-new-york"),
    ("San Francisco", "steep streets and bridge fog", "usa-san-francisco"),
    ("Los Angeles", "group beaches and studios without traffic", "usa-los-angeles"),
    ("United States", "online entry authorization before I fly", "usa-practical"),
]


def _metrics(fn) -> tuple[float, float, float]:
    # Warm the lazy model outside the measured loop.
    fn(CASES[0][0], CASES[0][1], 3)
    started = time.perf_counter()
    top1 = 0
    reciprocal = 0.0
    for destination, query, expected in CASES:
        rows = fn(destination, query, 3)
        ids = [row["id"] for row in rows]
        if ids and ids[0] == expected:
            top1 += 1
        if expected in ids:
            reciprocal += 1.0 / (ids.index(expected) + 1)
    elapsed = (time.perf_counter() - started) * 1000 / len(CASES)
    return round(top1 / len(CASES), 4), round(reciprocal / len(CASES), 4), round(elapsed, 1)


def run() -> dict[str, float]:
    lexical = _metrics(retrieve_lexical)
    bm25 = _metrics(retrieve_bm25)
    dense = _metrics(retrieve_dense)
    hybrid = _metrics(lambda d, q, n: retrieve_hybrid(d, q, n, use_reranker=False)[0])
    rerank = _metrics(lambda d, q, n: retrieve_hybrid(d, q, n, use_reranker=True)[0])
    return {
        "cases": len(CASES),
        "lexical_top1": lexical[0], "lexical_mrr": lexical[1], "lexical_ms": lexical[2],
        "bm25_top1": bm25[0], "bm25_mrr": bm25[1], "bm25_ms": bm25[2],
        "dense_top1": dense[0], "dense_mrr": dense[1], "dense_ms": dense[2],
        "hybrid_top1": hybrid[0], "hybrid_mrr": hybrid[1], "hybrid_ms": hybrid[2],
        "rerank_top1": rerank[0], "rerank_mrr": rerank[1], "rerank_ms": rerank[2],
    }


if __name__ == "__main__":
    print(run())
