# Data behind the report

Every number in the report comes from a file in this folder or in [`../../docs/eval-artifacts/`](../../docs/eval-artifacts/).
Scored results are JSON files; raw turns are Pathfinder session databases (SQLite; tables `sessions`, `messages`,
`plans`, `eval_runs`, `kv`), which open in any SQLite browser.

## Where each result comes from

| Report claim (Table 4 row, or section) | Folder | Primary file | Headline |
|---|---|---|---|
| Routing (84) and the 0.8 confidence cutoff | [`docs/eval-artifacts/v5-model-study`](../../docs/eval-artifacts/v5-model-study/) | `routing-merged.json`, `RESULTS.md` | Sonnet 83/84 at temperature 0 and 0.2 (84/84 on a second endpoint); Jev 79/84 (51 without the plan); at 0.8 the analyst acts on 96%, Jev on 66% |
| Planning-turn trace (Table 2) | [`plan-suites/`](plan-suites/) | `hk_r3.db`, case `hk01` | Three model calls, 20,131 tokens, 48.2 s; both constraints passed with quoted evidence |
| Latency target (Table 3) | [`plan-suites/`](plan-suites/) | `eval_runs.latency_ms` in `hk_r1–3.db`, `jp_tp_r1–3.db` | Median 73 s; 45 of 51 planning turns within two minutes; slowest 199 s |
| Plan suites (17 × 3 runs) | [`plan-suites/`](plan-suites/) | `summary.json` → `plans`; `hk_r1–3.db`, `jp_tp_r1–3.db` | Hong Kong 7, 7, 8 of 8; Japan 5, 5, 6 of 6; TravelPlanner-style 3/3 in every run; every stop sourced |
| Boundary suite (22) | [`boundary-suite/`](boundary-suite/) | `boundary_suite-20261124-164051.json` | 18/22 (representative 2/3, challenging 7/9, failure 9/10) |
| Injection (72) | [`injection-indirect/`](injection-indirect/) and [`docs/EVALUATION.md`](../../docs/EVALUATION.md) | `indirect_injection-20261124-154119.json` | 48 indirect probes: no leak, no route change, task done 48/48; 24 direct probes contained |
| TravelPlanner, 12-query sample | [`travelplanner-sample-12/`](travelplanner-sample-12/) | `travelplanner_sample-20261124-163221.json` | 12 plans; the 6 corpus-city plans fully sourced; 6 other cities left empty with gaps |
| TravelPlanner, 30 live queries | [`travelplanner-live-30/`](travelplanner-live-30/) | `tp30-results.json`, `tp30-report.md` | 0/30 met every check; 29 plans saved and fully sourced; multi-city 0/17; within budget 8/29 |
| Components (planner, editor, verifier, answers, memory) | [`docs/eval-artifacts/v5-model-study`](../../docs/eval-artifacts/v5-model-study/) | `RESULTS.md`, `verifier-current.json`, `editor-direct-latest.json`, `ask-adjudicated.json`, `memory-latest.json` | Planner 14/17; editor 8/8; verifier 18/20; answers 11/11; DeepSeek recalls more preferences but invents one |
| Skill ablation | [`skill-ablation/`](skill-ablation/) | `skills_ablation-20261124-155819.json` | Analyst 83 against 71 of 84; planner 15 against 16 of 17; editor 8/8 both; verifier 15/17 both |
| Single call (17 × 2) | [`single-call-baseline/`](single-call-baseline/) | `baseline_single_llm-20261124-162649.json` | Pathfinder 30/34, one call with the evidence 27/34, request only 6/34 |
| Edit against replan (5) | [`edit-vs-replan/`](edit-vs-replan/) | `paired_edit_replan-20261124-160045.json` | Median 11.3 s against 72.5 s; judged 2/5 against 0/5 |
| Reasoning effort (GPT-5.5) | [`reasoning-effort/`](reasoning-effort/) | `reasoning_ablation-20261124-160808.json` | none 84/84, low 83/84, medium 83/84 |
| Temperature, output caps, prices | [`docs/eval-artifacts/v5-model-study`](../../docs/eval-artifacts/v5-model-study/) | `TEMPERATURE-RESULTS.md`, `cap-stress-latest.json`, `pricing-20261123.md` | Supplement Tables S7 and S10 |
| Failure situations (Section 4), Figure 3, Table S11 | [`live-trips/`](live-trips/) | `*.plan.json`, `*.turn-log.json` | Five future trips planned with live tools |

[`summary.json`](summary.json) collects the scores of the submitted-build runs; it is written by
[`harness/analyze_results.py`](harness/analyze_results.py), which calls no model. Its keys `routing`,
`repair_ablation` and `components` are empty: those passes were not loaded, which does not mean a score of zero.

## How the submitted-build runs were made

- **When and which code.** The night of 24–25 November 2026 (Hong Kong time), on commit `8f1e0d5`; the skill ablation
  ran on `f6552fa`. The commits between them only changed how a running turn survives a page reload, and no application
  code (`backend/app`) changed between `8f1e0d5` and the submitted tag `submission-v5.2.11`.
- **Settings.** Claude Sonnet 5.5 for every part that writes text, temperature 0.2, reasoning effort none, two repair
  rounds; DeepSeek Flash as the verifier at temperature 0; Jev off. Every live web, maps and search host was blocked
  ([`harness/offline.py`](harness/offline.py)), so evidence came from the curated 12-city sandbox corpus and only the
  model calls went out.
- **Live runs.** The five live trips and the 30 TravelPlanner queries used live tools instead; the TravelPlanner run was
  made on an earlier build (see its README).
- **Validity screen.** A run whose trace shows no model tokens, or a provider quota error, scores the fallback reply
  rather than the system and is excluded; see [`excluded/`](excluded/).

## Reading the earlier model study (`docs/eval-artifacts`)

- `RESULTS.md` and `PART_MODEL_DECISIONS.md` record the recommendation of 23 November (Gemini 3.5 Flash-Lite for the
  analyst, planner and editor). That is not what shipped: the report explains why Claude Sonnet 5.5 was kept.
- The confidence figures in the report are for the shipped Sonnet arm, computed from `routing-merged.json`. The AUROC
  and calibration error printed in `RESULTS.md` belong to the Flash-Lite arm.
- The hybrid Jev-then-analyst router in `RESULTS.md` was an option that was not shipped.
- The zero-token files in `autotest/` are the runs removed by the validity screen (listed in
  [`excluded/README.md`](excluded/README.md)).

## Folder layout

| Folder | Contents |
|---|---|
| `plan-suites/`, `boundary-suite/`, `injection-indirect/`, `single-call-baseline/`, `edit-vs-replan/`, `skill-ablation/`, `reasoning-effort/`, `travelplanner-sample-12/` | One evaluation suite each: the scored JSON, the session database and the console log |
| `travelplanner-live-30/` | The 30 live TravelPlanner queries: runner, report, results and one database per query |
| `live-trips/` | The five live trips: exported plans with their conversations, and turn logs |
| `run-logs/` | Console logs of batches B, C and D, and the start record of batch A |
| `excluded/` | Runs kept for transparency that feed no number in the report |
| `harness/` | The scripts that produced the submitted-build runs |

**Redaction.** A Google Maps API key in place-photo links (live trips and TravelPlanner databases) was replaced with
`REDACTED_GOOGLE_API_KEY`. The test inputs contain only synthetic personal data (the test card number `4111…` and a
sample identity number), which the system masks.
