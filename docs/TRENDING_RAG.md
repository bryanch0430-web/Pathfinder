# Trending retrieval (RAG over kept journeys)

The trending rail is a retrieval problem, not a `COUNT(*)`. Once a platform has
thousands of travellers and saved trips, "what others planned" has to mean
"what people like this traveller planned" — which needs candidate generation and
ranking, not a full scan.

## 1. Write path

```
POST /api/journeys/{id}/rating  {useful: true}
  -> MemoryService.save_useful_trip
  -> embed(destination + summary + tags + days + party)   # 192-dim signed hashing
  -> kept_journeys row (trip_id, destination, rating, summary, embedding, plan JSON)
```

Only **useful-marked** trips are written. A trip that was generated but not rated
never enters the corpus.

## 2. Read path

```
GET /api/popular?q=...&destination=...&client_token=...
  -> query text = q + current destination + the caller's preference tags
  -> BM25 over each journey's document (destination, sights, tags, year, summary)
  -> dense cosine against the stored 192-dim embedding
  -> reciprocal rank fusion   1/(60 + lexical_rank) + 1/(60 + dense_rank)
  -> normalise to the best hit
  -> aggregate over every journey of each matched destination
  -> card score = relevance + 0.15·log(1+plans) + 0.15·|preference overlap|
  -> top `limit` cards
```

- **No query and no preferences** → popularity aggregate (`mode: popularity`).
- **A query or preferences** → hybrid retrieval (`mode: hybrid`); destinations are
  ranked by relevance, with popularity as a small prior.
- The index is cached in-process and rebuilt when the row count or newest row
  changes. 5,000 journeys build in well under a second; a query is ~1 ms of
  scoring on top of that.

## 3. Privacy

Only destination, trip length, year and sight names are returned. The dense
vector is the same local hashing embedder as trip memory — no text leaves the
machine and no vendor model is called. Preference tags are used for ranking, and
the response echoes **only the caller's own matched tags** as a `match` reason.
Another traveller's diet, pace or interests are never exposed.

## 4. Simulating a platform

The real database starts empty, so the rail looks dead. Seed a platform-sized
corpus:

```bash
cd backend
PYTHONPATH=. python scripts/seed_platform_demo.py --trips 800 --profiles 120
PYTHONPATH=. python scripts/seed_platform_demo.py --reset
```

- 12 destinations, validated `TripPlan` objects (days, items, hotels, costs).
- One `kept_journeys` row per journey with its embedding, rating and timestamp.
- Anonymous `taste_profiles` rows (`demo-user-0001` …) so personalisation can be
  demoed with `?client_token=demo-user-0001`.
- Every row is prefixed (`demo:` trips, `demo-user-` tokens) so `--reset` removes
  exactly what the script created.

## 5. Why the dense channel uses hashing, not BGE

The guide corpus (`guide_hybrid.py`) already demonstrates a BGE + cross-encoder
path; it is the heavier, citation-facing RAG. Trending needs a cheap, always-on
index over thousands of short documents, so it reuses the trip-memory embedder
and adds BM25 for exact place names. The two retrievers share the same shape
(candidates → fusion → rerank), so swapping in a BGE index later is a
`_encode` change, not a redesign.

## 6. Evaluation hooks

`/api/popular` returns a `trace` with `mode`, `candidates` and the note shown in
the card's "How this is ranked" disclosure. To evaluate retrieval, compare the
top destination for a fixed set of queries against the destination the seeded
traveller actually chose; report top-1 and MRR, the same way the guide corpus is
evaluated in `docs/EVALUATION.md`.
