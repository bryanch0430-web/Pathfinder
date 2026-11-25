# Modify vs full replan — v5 evidence brief

**Scope: v5 only.** Every number here comes from a v5 artifact or a fresh
v5.2.11 runtime session. v4/main/autotest artifacts are excluded.

### v5-only provenance

| Evidence | Version / date | Why it is v5 |
|---|---|---|
| Fresh runtime trials | **v5.2.11**, 25 Nov 2026 | `/api/ready` returned `5.2.11`; canonical v5 purpose names (`analyst`, `planner`, `editor`) appear in the trace |
| `v5_runtime_trials.csv` | v5.2.11 | derived from the two JSONs above |
| `editor_direct_sonnet.csv` | v5 model study, 22 Nov 2026 | under `docs/eval-artifacts/v5-model-study/`; arm uses the v5 editor prompt and frozen v5 plans |
| `edit_vs_replan_cases.csv` | v5 model study, 23 Nov 2026 | v5 artifact, but **invalid for quality** (see §2) |

Explicitly **not used**: `docs/eval-artifacts/autotest/*` (legacy harness),
the main/v1 release tables, and the user session log from the earlier UI
demonstration.

## Bottom line

The v5 evidence supports a **descriptive cost advantage and correct targeted
editing** for the local-edit path. It does **not** prove that an edit has the
same quality as a full replan, and it does not prove that an edit is cheaper
than replanning the *same request*: v5 has no paired replan arm.

This distinction matters for the report. A defensible claim is:

> In v5.2.11, applying the local edit took 12.21 s and 15,521 tokens, versus
> 39.4 s and 20,252 tokens to generate the initial itinerary in the same
> session (3.2× faster, 23.4% fewer tokens), while removing all three requested
> attractions and preserving all three untouched items. This is an observed
> runtime comparison, not a controlled edit-versus-replan experiment.

## 1. Fresh v5.2.11 runtime datapoints

Source: `data/v5-runtime-session-2026-11-25.json` (proposal mode) and
`data/v5-runtime-applied-session-2026-11-25.json` (applied mode).
Both runs: 2-day Taipei, 1 traveller, 12,000 TWD, 4 Dec 2026; request
“Remove all attractions from Day 2.”

| Trial | Mode | Plan (s) | Edit (s) | Plan tokens | Edit tokens | Calls plan/edit | Targets removed | Untouched kept |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | `review=True` proposal | 36.11 | 7.86 | 19,641 | 14,843 | 2 / 2 | 3/3 | 3/3 |
| B | `review=False` applied | **39.40** | **12.21** | **20,252** | **15,521** | 2 / 2 | 3/3 | 3/3 |

Trial B is the primary cost/quality datapoint. Trial A is a sensitivity check
and a proposal, not a persisted edit.

### Token breakdown, applied trial

| Part | Prompt | Completion | Total |
|---|---:|---:|---:|
| Plan — analyst | 4,186 | 771 | 4,957 |
| Plan — planner | 11,237 | 4,058 | 15,295 |
| **Plan total** | **15,423** | **4,829** | **20,252** |
| Edit — analyst | 5,475 | 1,042 | 6,517 |
| Edit — editor | 8,454 | 550 | 9,004 |
| **Edit total** | **13,929** | **1,592** | **15,521** |

- Wall-clock: 39.40 → 12.21 s = **69.0% lower**, **3.23× faster**.
- Total tokens: 20,252 → 15,521 = **23.4% lower**.
- Completion tokens: 4,829 → 1,592 = **67.0% lower**.
- Prompt tokens: 15,423 → 13,929 = **9.7% lower**.
- Model calls: 2 → 2. The advantage here is latency and output tokens, **not**
  fewer calls.
- Models differ: planner/analyst = `claude-sonnet-5.5`; editor =
  `deepseek-flash`. Part of the cost difference is therefore model choice, not
  only the edit path. Report this as a confound.

## 2. Quality datapoints

### Valid v5 evidence

| Measure | Result | Source |
|---|---:|---|
| Applied edit: requested Day-2 attractions removed | **3/3** | fresh v5.2.11 applied run |
| Applied edit: untouched items preserved | **3/3** | fresh v5.2.11 applied run |
| Editor-direct correctness (frozen plans, target checks) | **8/8** | `docs/eval-artifacts/v5-model-study/editor-direct-latest.json` (Sonnet arm) |
| Editor-direct p50 latency | 5.63 s | same (range 3.99–24.79 s) |
| Editor-direct mean tokens | 11,550 | same |

The editor-direct study is valid quality evidence because it checks the
**target** after applying the edit on a frozen plan: removal, move-to-day-2, or
new start time.

### Invalid evidence — do not cite as quality

The `edit_vs_replan` suite reports **5/5 cases and 100% stops preserved**, but
it is **not valid for edit quality**:

- `backend/app/eval/runner.py` runs the edit with `review=True` (line 304), so
  the result is a **proposal**; the saved plan is never changed.
- The preservation metric therefore sees the unchanged plan and reports 100%.
- The metric also counts the item the traveller asked to remove, so a correct
  removal would *lower* preservation.
- Its own case outputs expose this: a “remove Shibuya Sky” case reports
  “4 stops before, 4 after, 100% kept”; a removal cannot leave the stop count
  unchanged.

File: `data/edit_vs_replan_cases.csv` (kept with `quality_valid=false`).
Source artifact: `docs/eval-artifacts/v5-model-study/edit_vs_replan-sonnet-20261123-085453.json`.

### Not measured

**Paired edit-versus-replan quality.** The v5 study itself says: “The suite has
no replan arm; this is not a paired edit-vs-replan result”
(`docs/eval-artifacts/v5-model-study/TEMPERATURE-RESULTS.md`). No v5 run scores
the same request after an edit and after a forced replan on the same initial
plan.

## 3. Mechanical reason the edit path preserves the plan

This supports *why* preservation is possible, not the size of the effect:

- `backend/app/plan/editor.py:propose_edit` copies the current plan, applies
  typed operations (`move`, `retime`, `remove`, `add`, `replace`, `swap_hotel`,
  `swap_ticket`, `set_ticket`, `set_hotel`, `set_field`, `note`), then
  recalculates derived fields and refits changed stops.
- Locked/confirmed items are protected in `_apply`; a removal or replacement of
  a locked item is refused.
- `actual_changes`/`diff_plans` describe the delta; a rebuild is only requested
  for a **shape** change (dates, days, destination, origin, multi-city stops)
  or an explicit editor `scope: whole`.
- The orchestrator no longer silently replans when the edit answer is unusable:
  it asks instead (`backend/app/turn/orchestrator.py`).
- Tests: `backend/tests/test_editor.py` (12 tests) includes locked-item
  preservation, same-time flight edits, named-hotel edits, and the
  “whole scope with concrete ops” guard.

## 4. What can — and cannot — be claimed

| Claim | Status | Basis |
|---|---|---|
| Local edit uses less wall-clock time and fewer tokens than generating the initial plan | **Supported descriptively** | Fresh v5.2.11 session; n=1 applied + 1 proposal; unpaired, different requests/models |
| Local edit applies the requested change | **Supported** | 3/3 targets in the applied run; 8/8 target checks in editor-direct |
| Local edit preserves untouched plan items | **Supported for tested cases** | 3/3 untouched in applied run; 5/5 cases in v5 edit suite are not valid evidence |
| Local edit has equal/similar quality to a full replan | **Not established** | No paired replan quality arm in v5 |
| Local edit is cheaper than a full replan for the same request | **Not established** | No paired replan cost arm; observed comparison is initial-plan vs edit |
| The difference is caused by the edit path alone | **Not established** | Editor model (DeepSeek) differs from planner (Sonnet); request and context differ |

If the report needs a stronger claim, run the paired experiment in §6.

## 5. Threats to validity

1. **No paired replan arm.** The comparison is an initial plan vs a later edit,
   not two arms of the same request.
2. **Model confound.** Planner = `claude-sonnet-5.5`; editor = `deepseek-flash`.
   The v5 model study shows editor model choice alone changes latency/tokens.
3. **Small n.** One applied and one proposal session; no confidence interval or
   significance test is justified.
4. **Request difference.** The plan request and the edit request are not
   semantically identical, so token counts are not a controlled treatment
   effect.
5. **Single-run latency.** Provider/network variance is not measured; no
   repeated runs, no p95.
6. **Evaluation artifact flaw.** The 5-case edit suite does not apply edits and
   is excluded from quality claims.
7. **No workload/load test.** Everything is single-user, serial; no concurrency
   or throughput claim.

## 6. Recommended paired experiment (closes the gap)

Pre-register this before running:

- Seed the **same plan** for both arms (or block by seed plan).
- Same request, same context, same model and temperature; force one arm through
  `propose_edit` and one through a controlled full rebuild.
- Randomise arm order; N ≥ 30 paired cases (more for quality).
- Outcomes: request satisfaction (target removed/added/timed correctly),
  untouched-item preservation, plan validity/constraints, latency, prompt/
  completion tokens, model calls.
- Report paired differences (edit − replan) with median/IQR and a paired test;
  do not compare unrelated plan/edit tasks.
- Keep proposal vs applied separate; quality must be measured on the applied
  plan.

## 7. Figures

Generated by `make_figures.py`; vector SVG, no raster dependencies.

| File | Caption |
|---|---|
| `figures/fig-v5-modify-cost.svg` | “v5.2.11 initial plan vs local edit: 39.4 s / 20,252 tokens vs 12.21 s / 15,521 tokens in one applied session. Requests differ and models differ; descriptive, not a paired replan comparison.” |
| `figures/fig-v5-modify-quality.svg` | “v5 edit quality evidence: applied targets removed 3/3, untouched items kept 3/3, editor-direct 8/8. The paired replan quality comparison is not measured.” |
| `figures/fig-v5-editor-latency.svg` | “v5 editor latency across 8 frozen edit cases (Sonnet): median 5.63 s, range 3.99–24.79 s; isolated editor calls, not full turns.” |

Rendered PNG copies for tools that do not embed SVG are beside each `.svg`
(`fig-v5-modify-cost.png`, `fig-v5-modify-quality.png`,
`fig-v5-editor-latency.png`).

Regenerate:

```powershell
python docs/report/modify-vs-replan/make_figures.py
```

## 8. Files

```
docs/report/modify-vs-replan/
├── README.md                          # this brief
├── make_figures.py                    # stdlib SVG generator
├── data/
│   ├── v5-runtime-session-2026-11-25.json           # proposal trial
│   ├── v5-runtime-applied-session-2026-11-25.json   # applied trial (primary)
│   ├── v5_runtime_trials.csv
│   ├── v5_generation_breakdown.csv
│   ├── v5_runtime_cost.csv
│   ├── v5_quality_evidence.csv
│   ├── edit_vs_replan_cases.csv       # invalid for quality; kept for audit
│   └── editor_direct_sonnet.csv       # valid target-checked editor data
└── figures/
    ├── fig-v5-modify-cost.svg / .png
    ├── fig-v5-modify-quality.svg / .png
    └── fig-v5-editor-latency.svg / .png
```

**Provenance hashes** are in `data/SHA256SUMS.txt`; generated with `sha256sum`
on the saved files.

## 9. One-paragraph handoff for Cursor

> v5-only evidence for the modify path is a fresh v5.2.11 applied session
> (39.40 s and 20,252 tokens to plan; 12.21 s and 15,521 tokens to apply
> “Remove all attractions from Day 2”; 3/3 requested attractions removed, 3/3
> untouched items preserved), a proposal-mode sensitivity trial (36.11/7.86 s;
> 19,641/14,843 tokens; same target/preservation counts), and the v5
> editor-direct study (8/8 correct, median 5.63 s, mean 11,550 tokens). This
> supports a descriptive cost advantage and correct targeted editing, not equal
> quality to a full replan: the v5 evaluation has no paired replan arm, and the
> 5-case edit-vs-replan “100% preservation” result is invalid because edits are
> proposals that are never applied. Do not write “proves” or “quality is the
> same”; write “observed”, “descriptive”, and list the missing paired experiment.
