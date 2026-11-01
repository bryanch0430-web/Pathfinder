# Gap analysis and A-grade roadmap

**Scope.** This is a product and engineering comparison, not a code import. I read public
READMEs and feature lists from four representative open-source travel planners, then
mapped them against Pathfinder. Links are at the bottom. No source code from those
repositories was copied.

## 1. What comparable projects do well

| Project | Strongest ideas | What it proves |
|---|---|---|
| **Roam** ([GitHub](https://github.com/AarnavNoble/roam)) | OSM candidate pool, LightGBM LambdaRank, OR-Tools route with soft time windows, `degraded` flags, health/metrics, 449 tests, public share links, post-edit versioning | The hard part is not writing prose. It is **retrieval quality, route feasibility, and graceful degradation**. |
| **Zhilv Yuntu** ([GitHub](https://github.com/tutu-zzz/zhilv-yuntu)) | Chroma RAG over local guides, LLM query rewrite, cross-encoder rerank, noise pre-filter, Redis cache, Top1/MRR/noise/latency evaluation, cross-destination pollution checks | RAG is only credible when it has a **destination filter, a reranker, and an evaluation set**. Their reported rerank step moved Top1 86.7% → 93.3% and MRR 0.922 → 0.967. |
| **Agentic AI Trip Planner** ([GitHub](https://github.com/Ratnesh-181998/Agentic-AI-Trip-Planner-CrewAI)) | CrewAI multi-agent split, 5-tier LLM failover, history, favorites, flight/hotel/train/bus search, PDF export | Breadth of **trip surfaces** and visible fallback states. Its roadmap still lists auth and saved favorites, which confirms those are expected product features. |
| **Traveling** ([GitHub](https://github.com/ivyfan-toowell/Traveling)) | End-to-end planning + editing UI, itinerary persistence | The demo bar now includes **edit, save, reopen** — not one-shot generation. |

## 2. Where Pathfinder already leads

- **Typed turn pipeline with exactly one path** (plan / modify / ask / clarify), not an
  unconstrained loop of tool calls. Easier to test and explain.
- **Four concurrent evidence agents** with per-field provenance, freshness status, and an
  explicit `ok | stale | unavailable` model.
- **Bounded repair** and a confidence gate instead of silently inventing a plan.
- **Agentic UI**: Storybook-first design system, SSE agent chips, map playback with a
  traveller token, day clock, speed control, “About this stop” scope, version diff and
  green changed rows.
- **Data-gap honesty**: weather outside the forecast is labelled `Seasonal`/`stale`; an
  unavailable source is a visible gap, not a fake fact.
- **Local social proof**: trips explicitly marked useful become the anonymous trending
  source (destination, length, year, sight names only).
- **Evaluation tab** with datasets, routing, injection, and traces.

Most public projects are stronger in **retrieval** and **route optimization**; most are
weaker in provenance, typed state, and UI explainability. That is the gap to exploit.

## 3. Concrete gaps, scored

Scores are 1–5; value is for this course demo and for a credible product story.

| Gap | Value | Effort | Current Pathfinder | Recommendation |
|---|---:|---:|---|---|
| Curated **RAG** over destination guides with citations | 5 | 3 | Ten versioned docs (HK/Japan/USA); BM25+BGE hybrid shipped; BGE reranker opt-in/no gain on 12-query ablation | Expand corpus, add hybrid retrieval + cross-encoder/Jev ablation, report Top1/MRR/noise. Do **not** use it for prices/hours. |
| **Hybrid retrieval + reranker** | 4 | 3 | Keyword `q` filter on trending only | BM25 + embedding top 20 → rerank top 5. Use Jev only after an eval set exists; otherwise a cross-encoder/embedding reranker is enough. |
| **Route optimization** with soft time windows | 5 | 4 | Nearest-neighbour + open-path 2-opt shipped; synthetic 91.7→61.7 km (−32.7%) | Remaining: OR-Tools with opening-hour soft windows and a real travel matrix. |
| **Share a read-only trip link** | 4 | 2 | Shipped: slug, redacted public page, copy link | Add revocation and comment threads next. |
| **Export PDF / Markdown / calendar** | 3 | 2 | Markdown export shipped; ICS/PDF still roadmap | ICS is cheap next; PDF can wait. Export the same structured plan, not model prose. |
| **Auth + cross-device profile** | 3 | 4 | Shipped: optional email/password + HS256 JWT; guest mode retained and merged on first login | Add password reset/email verification later. |
| **Feedback → reranker training loop** | 3 | 4 | Stars save a useful trip | Do not train yet. Log feedback, build an offline eval, and promote only if NDCG/MRR beats the baseline. |
| **Fault-injection + latency/contract benchmarks** | 5 | 3 | 47 tests; no fault matrix or p50/p95 stage benchmark | Remaining: faked-upstream fault matrix and per-stage timings. |
| **Booking/price alerts** | 2 | 5 | Deliberately out of scope | Stay out; fake booking is a credibility risk. Link out only with a clear “reference” label. |
| **Mobile app** | 2 | 4 | Responsive web only | Not needed for the course; the responsive console is enough. |

## 4. Is RAG actually useful here?

**Yes, but only for the right kind of knowledge.**

Use RAG for:

- neighbourhood character (“stay near ... if you want ...”),
- timing and sequencing advice (“visit X early; it gets crowded”),
- accessibility/cultural notes,
- “what locals do” soft context.

Do **not** use RAG for:

- live prices, flight numbers, opening hours, availability, or safety advisories,
- anything that must carry a timestamp and a provider.

Pathfinder already models **hard facts** as evidence with freshness and source. RAG should
be a separate **soft-context layer** that returns `chunk`, `source_url`, `destination`,
`section`, and a citation. The planner may use chunks to write better prose and order
stops, but may never turn a chunk into a price, hour, or booking claim.

### Would Jev as a reranker help?

Only after a labelled retrieval set exists. Jev is currently a small decision model; using
it as a reranker without training data would add latency and a failure mode, not quality.
A defensible sequence is:

1. BM25 + embedding retrieval, top 20, destination-filtered.
2. A deterministic cross-encoder or the existing embedding score, top 5.
3. Add Jev as a **second-stage reranker** only if an eval set of at least 50–100
   query/chunk relevance labels shows a gain over step 2.
4. Report Top1, MRR, noise rate, latency, and cross-destination pollution — the metrics
   Zhilv reports. Without those numbers the RAG claim is a demo, not an evaluation.

This is the strongest A-grade story: **RAG with citations + measured rerank ablation**,
not “we added a vector database”.

## 5. Recommended next two weeks (highest grade per hour)

1. **Share/export** (2 days): Share link and Markdown export are done; add ICS calendar
   export and link revocation.
   Demonstrates privacy thinking and closes a visible product loop.
2. **RAG soft-context layer with citations** (4–5 days): 6-city corpus, destination
   metadata, hybrid retrieval, 30-query eval, Top1/MRR/noise. Keep hard facts in agents.
3. **Route optimizer experiment** (3–4 days): OR-Tools per day with soft windows and a
   travel matrix; report travel minutes/off-window minutes before vs after. If it wins,
   ship it; if not, document the negative result.
4. **Fault/latency benchmark** (1–2 days): fake each provider, assert status + gap, report
   p50/p95 per stage.
5. **Optional auth** only after the above. Anonymous profile already demonstrates memory.

## 6. Feature matrix

| Capability | Pathfinder | Roam | Zhilv | CrewAI planner |
|---|---|---|---|---|
| Multi-agent / typed orchestration | ✅ four evidence agents + one path | pipeline stages | linear pipeline | ✅ CrewAI |
| Live evidence + provenance | ✅ source + freshness + gaps | ✅ OSM + fallbacks | ✅ Amap + weather | ✅ web search |
| RAG with citations | 🟡 BM25+BGE hybrid, citations | ✅ FAISS context | ✅ Chroma + rerank | ❌ |
| Route optimization | ❌ greedy/estimate | ✅ OR-Tools | map enrich only | ❌ |
| Map playback / token | ✅ unique | static map | map markers | ❌ |
| Edit with versioning | ✅ | ✅ | ✅ | history |
| Anonymous memory / profile | ✅ (new) | auth + users | history | ❌ roadmap |
| Useful-trip social proof | ✅ (new) | share link | ❌ | favorites roadmap |
| Export | ✅ Markdown (ICS/PDF roadmap) | share | ✅ MD/PDF | ✅ PDF |
| Fault injection / benchmarks | partial (47 tests) | ✅ 449 tests | RAG eval | fallback alerts |
| Auth / accounts | ✅ optional email account | ✅ users | ❌ | ❌ roadmap |
| Booking | deliberately no | no | no | links only |

## Sources

- [AarnavNoble/roam](https://github.com/AarnavNoble/roam) — README sections on RAG,
  ranking, OR-Tools, `degraded` flags, fault injection, share links and versioning.
- [tutu-zzz/zhilv-yuntu](https://github.com/tutu-zzz/zhilv-yuntu) — README sections on
  Chroma RAG, query rewrite, qwen3-rerank, Redis cache and retrieval metrics.
- [Ratnesh-181998/Agentic-AI-Trip-Planner-CrewAI](https://github.com/Ratnesh-181998/Agentic-AI-Trip-Planner-CrewAI)
  — README sections on CrewAI, 5-tier fallback, history, favorites, transport search and
  PDF export.
- [ivyfan-toowell/Traveling](https://github.com/ivyfan-toowell/Traveling) — end-to-end
  planning/editing surface.
- [GitHub topic: itinerary-planning](https://repos.ecosyste.ms/hosts/GitHub/topics/itinerary-planning?order=desc&sort=pushed_at)
  — discovery index.
