# Versioned soft-guide RAG corpus

The RAG layer is deliberately **not** a source of volatile facts. It covers
slow-changing travel advice: neighbourhood character, walking order, timing,
transport rhythm and etiquette. Prices, opening hours, availability and safety
advisories stay in the live evidence agents.

The production path is **BM25 + BGE-small embeddings** (hybrid RRF). A BGE
cross-encoder is available as an optional second stage, but is **off by default**
because it added ~250 ms with no measured gain on the current small corpus.

Models are local, not API-hosted: `BAAI/bge-small-en-v1.5` and
`BAAI/bge-reranker-base` are downloaded once into `.cache/huggingface/`.
Install with `backend/requirements-rag.txt`. If the packages/models are
unavailable, the API falls back to the lexical retriever and labels the mode.

## Corpus coverage

Ten original summaries, each in a Markdown file with frontmatter:

| ID | Destination | Country | Section | Source |
|---|---|---|---|---|
| `hk-lantau` | Hong Kong | HK | Lantau | [Wikivoyage: Lantau](https://en.wikivoyage.org/wiki/Lantau) |
| `hk-central` | Hong Kong | HK | Central / Sheung Wan | [Wikivoyage: Hong Kong Island](https://en.wikivoyage.org/wiki/Hong_Kong/Island) |
| `hk-kowloon` | Hong Kong | HK | Kowloon | [Wikivoyage: Kowloon](https://en.wikivoyage.org/wiki/Kowloon) |
| `japan-tokyo` | Tokyo | JP | Neighbourhoods and transit | [Wikivoyage: Tokyo](https://en.wikivoyage.org/wiki/Tokyo) |
| `japan-kyoto` | Kyoto | JP | Temples and neighbourhoods | [Wikivoyage: Kyoto](https://en.wikivoyage.org/wiki/Kyoto) |
| `japan-transport` | Japan | JP | Transport and etiquette | [Japan Travel](https://www.japan.travel/en/plan/transportation/) |
| `usa-new-york` | New York | US | Manhattan | [NYC Tourism](https://www.nyc.gov/html/nyc-tourism/) |
| `usa-san-francisco` | San Francisco | US | Neighbourhoods and hills | [SF Travel](https://www.sftravel.com/) |
| `usa-los-angeles` | Los Angeles | US | Getting around | [Discover LA](https://www.discoverlosangeles.com/) |
| `usa-practical` | United States | US | Practicalities | [USA.gov](https://www.usa.gov/) |

The text is original; the sources are cited for verification and attribution.
Do not copy source text into the corpus. Check each source's current licence
before redistributing derived text.

## Frontmatter contract

```yaml
---
id: hk-lantau
destination: Hong Kong
country_code: HK
section: Lantau
title: Lantau Island
url: https://en.wikivoyage.org/wiki/Lantau
license: Original summary; source cited
last_verified: 2026-10-30
aliases: Hong Kong, Lantau
---
```

- `last_verified` is the date a human last checked the advice.
- `stale` is computed at retrieval time; currently older than **365 days**.
- `content_hash` is computed from the body; `corpus_version` is a hash across all
  files, so a response can identify exactly which corpus answered it.
- `aliases` lets “New York City”, “USA” and “United States” resolve correctly.

## Update policy

| Content | Review cadence | Owner |
|---|---|---|
| Neighbourhoods, walking rhythm, etiquette | Quarterly | Content owner |
| Transport/pass practicalities | Twice a year | Content owner |
| Seasonal/event advice | Monthly, from official sources | Content owner |
| Opening hours, prices, availability | Never in RAG — live agents | Data agents |

When a source changes materially:

1. Edit the Markdown document.
2. Update `last_verified` only after checking the source.
3. Restart the backend (the corpus is loaded at import).
4. Run `PYTHONPATH=. python -m app.guide_eval`.
5. Review the stale badge in the UI.
6. Keep the previous corpus version in git so an answer can be audited.

A stale document is still returned, but marked **“Guide may be outdated”**. It is
never silently presented as a current fact.

## Evaluation

Run:

```bash
cd backend
PYTHONPATH=. python -m app.guide_eval
```

Current result:

```text
{'cases': 30, 'top1': 1.0, 'mrr': 1.0, 'cross_destination_noise': 0}
```

The 30 queries cover Hong Kong (6), Japan (10), and the USA (14). They check
destination filtering, expected section retrieval, and cross-destination noise.

### Paraphrase ablation (12 harder queries)

```bash
cd backend && PYTHONPATH=. python -m app.guide_ablation
```

```text
{'cases': 12,
 'lexical_top1': 0.9167, 'lexical_mrr': 0.9583, 'lexical_ms': 0.1,
 'bm25_top1': 1.0, 'bm25_mrr': 1.0, 'bm25_ms': 0.1,
 'dense_top1': 1.0, 'dense_mrr': 1.0, 'dense_ms': 12.1,
 'hybrid_top1': 1.0, 'hybrid_mrr': 1.0, 'hybrid_ms': 10.8,
 'rerank_top1': 1.0, 'rerank_mrr': 1.0, 'rerank_ms': 263.9}
```

Interpretation:

- Lexical retrieval misses one paraphrase.
- BM25 fixes it at no meaningful latency.
- Dense and hybrid match BM25; the small 10-document corpus gives them little
  extra room.
- The BGE reranker gives **no additional gain** and costs ~264 ms per query, so
  it is optional (`/api/handbook?...&rerank=true`) and not the default.
- A larger corpus and harder/noisier queries are required before claiming a
  reranker benefit. Jev is still not used as a reranker; it remains the System 1
  router.

## Hard boundary

The guide card and the API both state:

> Soft context only: never a price, opening hour or booking claim.

If a guide disagrees with a live provider, the live provider wins and the plan
shows its normal freshness/source data.
