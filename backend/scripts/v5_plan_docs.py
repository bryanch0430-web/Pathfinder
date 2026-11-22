"""Write the frozen TEST_PLAN, PART_MODEL_DECISIONS, TRACE_INDEX and LIMITATIONS docs."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "docs" / "eval-artifacts" / "v5-model-study"
FIX = ROOT / "backend" / "eval" / "fixtures"


def load(name: str):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


routing = load("routing-merged.json")
datasets = load("datasets-latest.json")
editor = load("editor-direct-latest.json")
verifier = load("verifier-latest.json")
ask = load("ask-adjudicated.json")
memory = load("memory-latest.json")
cap = load("cap-stress-latest.json")
hybrid = load("hybrid-router.json")

planner_rows = []
for filename, label in (("hongkong.json", "Hong Kong"), ("japan.json", "Japan")):
    for row in json.loads((FIX / filename).read_text(encoding="utf-8")):
        planner_rows.append({"id": row["id"], "dataset": label, "input": row["input"], "gold": f"{row.get('days', '')} days, {row.get('budget', '')} {row.get('currency', '')}"})

route_rows = routing["cases"]["sonnet-0.0"]
editor_rows = editor["arms"]["sonnet"]["rows"]
verifier_rows = verifier["arms"]["sonnet"]["rows"]
ask_rows = ask["arms"]["sonnet"]["rows"]
memory_rows = memory["arms"]["sonnet"]["rows"]

lines = [
    "# v5 part-model study — frozen test plan",
    "",
    "Status: **executed** (2026-11-23, Asia/Hong_Kong). Results: `RESULTS.md`; workbook: `v5_model_study.xlsx` / `.xls`.",
    "",
    "## 1. Purpose",
    "",
    "The final report must explain the workflow, autonomous decisions, model/version, prompts, tools, data and state, and justify each model choice with a fair comparison. This study isolates the model/configuration decision for each part of v5 while keeping prompts, tools, cases and sandbox evidence fixed.",
    "",
    "## 2. Research questions",
    "",
    "1. Which model gives the best routing accuracy at acceptable latency, and does temperature matter?",
    "2. Does the LLM router beat Jev, and does a Jev pre-gate plus LLM fallback dominate both?",
    "3. Which model produces the most valid, constraint-satisfying plans?",
    "4. Which model makes the requested edit while preserving the rest of the plan?",
    "5. Which verifier catches injected constraint defects without false alarms?",
    "6. Which ask model answers from the plan with sources and without invention?",
    "7. Which memory model extracts preferences with the best recall and no false memories?",
    "8. What do the effective reply caps, reasoning effort and threshold actually change?",
    "",
    "## 3. Protocol",
    "",
    "- **Fixed:** frozen prompts/Skills, code commit, sandbox evidence (`PATHFINDER_SANDBOX=1`), case inputs, gold rules, concurrency, timeouts.",
    "- **Varied:** part model, temperature, purpose cap, reasoning effort (only where the provider supports it), router threshold.",
    "- **Models:** `claude-sonnet-5.5`, `gemini-3.5-flash-lite`, `deepseek-v4-flash`, `jev-latest`.",
    "- **Excluded after smoke:** GPT-5 nano/mini (Poe returned `400 upstream gateway failure`); Gemini 3.8 Flash (empty answer). DeepSeek's full 84-case routing arm timed out under concurrency 4; it is excluded from routing and used in the other parts.",
    "- **Sandbox:** real pipeline with sandbox providers; model calls are live.",
    "- **Runs:** one run per arm. Temperature 0 arms are deterministic; Sonnet at 0.2 is an additional one-run sensitivity check.",
    "- **Date:** 2026-11-23; provider: Poe OpenAI-compatible endpoint.",
    "",
    "### Statistics",
    "",
    "- Accuracy/pass rate with Wilson 95% interval.",
    "- Routing paired comparison with exact McNemar; small-n parts are descriptive.",
    "- Confidence gate: coverage = answered / all; accuracy-on-kept; escalated count.",
    "- Latency p50/p95 from the measured call/turn; tokens from the trace.",
    "- No p-value is claimed for editor/verifier/ask/memory (small n).",
    "",
    "### Pre-declared decision rule",
    "",
    "Eliminate a model whose 95% interval is more than 10 percentage points below the best. Among models within 5 points, prefer lower p95 latency, then fewer tokens. For equal accuracy, keep the simpler/established model. This mirrors the FrugalGPT-style rule used in the earlier study and the Jev benchmark's accuracy/latency/cost reporting.",
    "",
    "## 4. Test matrix",
    "",
    "| Part | Cases | Arms | Primary metric |",
    "|---|---:|---|---|",
    "| Analyst / router | 84 | Sonnet 0.0, Sonnet 0.2, Flash-Lite 0.0, Jev | intent accuracy, gate, latency |",
    "| Turn router | 84 × 2 | LLM vs Jev; derived hybrid at 0.8 | accuracy, coverage, latency |",
    "| Planner | 17 | Sonnet, Flash-Lite (17 planned; 14 ran: 8 HK + 6 Japan) | pass, constraints, grounding, latency |",
    "| Editor | 10 planned; 4 ran | Sonnet, Flash-Lite, DeepSeek | semantic edit + preservation |",
    "| Verifier | 20 planned; 10 ran | Sonnet, Flash-Lite, DeepSeek | audit accuracy, false pass |",
    "| Ask | 12 | Sonnet, Flash-Lite, DeepSeek | rubric accuracy; scope separate |",
    "| Memory | 8 | Sonnet, Flash-Lite, DeepSeek | preference recall; false memory |",
    "| Cap stress | 5 parts × 2 caps | Flash-Lite | pass/fail at low vs effective cap |",
    "",
    "The planner reduced from 17 to 14 because TravelPlanner-style cases were not part of the approved run environment; the report must say 14, not 17.",
    "",
    "## 5. Full case catalogue",
    "",
    "### Routing / analyst (84)",
    "",
    "| ID | Input | Gold |",
    "|---|---|---|",
]
for row in route_rows:
    lines.append(f"| {row['id']} | {row['input'].replace('|', '/')} | {row['expected']} |")
lines += [
    "",
    "The routing split includes 21 plan, 21 modify, 21 ask and 21 unclear utterances. IDs and inputs are the frozen `backend/eval/fixtures/routing.jsonl` rows.",
    "",
    "### Planner (14 run)",
    "",
    "| ID | Dataset | Input | Gold |",
    "|---|---|---|---|",
]
for row in planner_rows:
    lines.append(f"| {row['id']} | {row['dataset']} | {row['input'].replace('|', '/')[:180]} | {row['gold']} |")
lines += [
    "",
    "### Editor (4)",
    "",
    "| ID | Input | Check |",
    "|---|---|---|",
]
for row in editor_rows:
    lines.append(f"| {row['id']} | {row['input']} | {row['check']} |")
lines += [
    "",
    "### Verifier (10)",
    "",
    "| ID | Constraint | Injected defect | Gold |",
    "|---|---|---|---|",
]
for row in verifier_rows:
    lines.append(f"| {row['id']} | {row['constraint']} | {row['mutation']} | {row['gold']} |")
lines += [
    "",
    "### Ask (12; 11 in-scope + 1 scope)",
    "",
    "| ID | Question | Gold rule |",
    "|---|---|---|",
]
for row in ask_rows:
    lines.append(f"| {row['id']} | {row['question']} | {'scope case' if row['id'] == 'q11' else 'rubric: fact + no invention'} |")
lines += [
    "",
    "### Memory (8)",
    "",
    "| ID | Gold preferences |",
    "|---|---|",
]
for row in memory_rows:
    lines.append(f"| {row['id']} | {'; '.join(row['gold']) or 'none — must not invent'} |")
lines += [
    "",
    "### Cap stress",
    "",
    "| Part | Low cap | Effective cap |",
    "|---|---:|---:|",
]
for part, rows in cap.items():
    lines.append(f"| {part} | {rows['low']['cap']} | {rows['effective']['cap']} |")
lines += [
    "",
    "## 6. What was measured and what was not",
    "",
    "- Measured: per-case predictions, pass/fail, latency, tokens, confidence, gate, false pass, recall, cap outcome.",
    "- Not measured: repeated sampling for most arms; human inter-rater agreement; cost in currency; full 17 planner cases; full 10 editor cases; reasoning on a GPT-5 endpoint; prompts in the workbook (they live in `backend/app/skills/` and are hashed by commit).",
    "- The verifier source-constraint case was fixed during the study: `plan_text(with_sources=True)` now gives the auditor source lines. The first verifier run (before this fix) is not used.",
    "- Ask q01/q05 needed a rubric re-read because `06:00` vs `6:00` and `isn't confirmed` were literal-match false negatives; the adjudicated table is the one reported.",
    "",
    "## 7. Artefacts",
    "",
    "- `RESULTS.md` — all result tables and failures.",
    "- `PART_MODEL_DECISIONS.md` — one decision section per part.",
    "- `TRACE_INDEX.md` — claim → code path → trace/evidence.",
    "- `LIMITATIONS.md` — threats to validity.",
    "- `v5_model_study.xlsx` — formatted workbook; `v5_model_study.xls` — SpreadsheetML compatibility copy.",
    "- Raw JSON: `routing-merged.json`, `hybrid-router.json`, `datasets-latest.json`, `editor-direct-latest.json`, `verifier-latest.json`, `ask-adjudicated.json`, `memory-latest.json`, `cap-stress-latest.json`.",
]
(OUT / "TEST_PLAN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---- decisions --------------------------------------------------------
decision = """# v5 part-model decisions

This is the report-ready decision record. Evidence tables are in `RESULTS.md`; the full protocol is in `TEST_PLAN.md`.

## Analyst / understanding

- **Chosen:** Gemini 3.5 Flash-Lite, temperature 0, cap 4,096, no reasoning, threshold 0.8 (0.9 if a wrong route is more expensive than an escalation).
- **Evidence:** 83/84 = 98.8% (Wilson 93.6–99.8), p50 4.1 s, 2.9k tokens. Sonnet 5.5 was also 83/84 but 6.3 s and 4.6k tokens. Sonnet 0 vs 0.2 was identical (83/84), so temperature 0 is the reproducible choice.
- **Why not Sonnet:** no accuracy gain on this set; slower and more tokens.
- **Why not Jev alone:** 79/84 = 94.0%; it is excellent on plan (21/21) but weaker on modify/ask (both 100% for the LLM), and its 0.8 gate keeps only 66%.
- **Rejected:** Gemini 3.8 Flash (empty answer in smoke), GPT-5 nano/mini (Poe 400).
- **Code:** `app/understand/pipeline.py:understand`, `app/core/llm.py:model_for/temperature_for/max_tokens_for`.

## Turn router

- **Chosen:** LLM router (Flash-Lite) as the default. Keep Jev as an optional latency path.
- **Evidence:** LLM 98.8% at 4.1 s; Jev 94.0% at 0.6 s. At a 0.8 gate, Jev keeps 66% at 100% and escalates 29 cases. A **derived** Jev→Flash fallback scored 84/84 (100%, Wilson 95.7–100) at a mean sequential latency of about 2.0 s; Jev→Sonnet scored 83/84 at about 3.3 s.
- **Trade-off:** the hybrid is the best measured option, but it adds a second call path and is not a shipped setting; it is a recommendation for the next version. Pure Jev is the fastest but not the most accurate.
- **Threshold:** product 0.8 is a reasonable default; Flash at 0.9 kept 89% at 100%, so 0.9 is the quality-first variant.
- **Code:** `app/understand/jev.py:jev_route`, `app/understand/pipeline.py`, `settings.router_threshold`.

## Planner

- **Chosen:** Gemini 3.5 Flash-Lite, temperature 0.2, cap 12,000, no reasoning.
- **Evidence:** 12/14 = 85.7% vs Sonnet 11/14 = 78.6%; Hong Kong constraints 85.7% vs 68.8%; Japan 6/6 for both. HK 176 s vs 417 s; Japan 73 s vs 213 s.
- **Trade-off:** n=14, one run; Sonnet had a stronger prior full run, so the planner choice is the least certain. The 12,000 cap is deliberate: planner JSON exceeded a 6,000 cap earlier.
- **Why not lower cap:** the plan JSON is the longest output; the cap must cover the day-by-day JSON, not prose.
- **Code:** `app/plan/builder.py:build_plan`, `app/plan/agents.py`, `app/core/llm.py`.

## Editor

- **Chosen:** DeepSeek V4 Flash, temperature 0, cap 2,000.
- **Evidence:** 4/4, p50 5.3 s, 3.1k mean tokens; Sonnet 4/4, 6.7 s, 20.3k; Flash 3/4, 3.8 s.
- **Trade-off:** n=4; DeepSeek matches Sonnet at much lower token cost and is faster; Flash missed the move edit. This test covers remove, move, time and remove, not add/research edits.
- **Why not Flash:** the miss was a real semantic failure, not a scoring artefact.
- **Code:** `app/plan/editor.py:propose_edit`, `_apply`.

## Verifier

- **Chosen:** DeepSeek V4 Flash, temperature 0, cap 1,500.
- **Evidence:** 9/10, 1 false pass, p50 4.8 s; Sonnet 9/10, 1 false pass, 5.0 s; Flash 8/10, 2 false passes, 3.4 s.
- **Trade-off:** all arms missed v06 (10-minute transfer against a 30-minute constraint). Model choice cannot fix arithmetic/temporal audit alone; deterministic checks must stay.
- **Why not Flash:** two false passes vs one; verifier errors are asymmetric (a false pass is worse).
- **Code:** `app/plan/validator.py:audit_constraints`; `plan_text(with_sources=True)` now exposes source lines.

## Ask / quick answers

- **Chosen:** DeepSeek V4 Flash, temperature 0, cap 1,400.
- **Evidence:** 11/11 in-scope (100%), p50 16.1 s; Sonnet 11/11, 20.2 s; Flash 10/11, 14.4 s.
- **Trade-off:** all three are slow (15–20 s) because the ask path re-reads the plan; model choice does not fix that. The off-topic q11 was clarified by all three and is reported as a scope issue, not counted in accuracy.
- **Why not Sonnet:** same in-scope accuracy, lower latency for DeepSeek.
- **Code:** ask path in `app/turn/orchestrator.py`; prompts in `app/skills/`.

## Memory

- **Chosen:** DeepSeek V4 Flash with a write-time no-invention check; Sonnet 5.5 if precision is the priority.
- **Evidence:** DeepSeek recall 91.7% vs 79.8% for Sonnet and Flash; but DeepSeek produced preferences for m06, the “nothing to remember” case. Sonnet/Flash produced none.
- **Trade-off:** best recall vs one false memory in eight. Memory write must validate each preference against the message/plan; otherwise choose Sonnet.
- **Code:** `app/application/memory.py:derive_memory`, `remember_trip`.

## Temperature, caps, reasoning

- **Temperature:** 0 for routing, editor, verifier, ask and memory (reproducible structured output). Planner keeps 0.2 as the production setting; it was not swept, so this is a limitation, not a proven optimum.
- **Caps:** 4,096 analyst; 12,000 planner; 2,000 editor; 1,500 verifier; 1,400 ask; 700 memory. These are effective defaults, not arbitrary: plan JSON is the only large output; the verifier/memory low-cap controls failed as expected. Ask/planner low-cap controls were inconclusive and are not claimed as causal.
- **Reasoning:** none on these Poe models. The client only sends `reasoning_effort` to GPT-5/o-series; those models failed on the available endpoint, so no reasoning arm exists. This is a provider limitation, not evidence that reasoning does not help. Revisit with a direct OpenAI key.
- **Threshold:** 0.8 default; 0.9 for Flash when avoiding a confident wrong route is more important than coverage.

## Overall recommendation

For a **quality-first** profile: Jev pre-gate at 0.8 + Flash-Lite fallback for routing; Flash-Lite planner; DeepSeek editor/verifier/ask; DeepSeek memory with validation.
For a **simpler** profile: Flash-Lite for analyst/planner; Sonnet for editor/verifier/ask/memory (the current one-writer default), accepting the higher token cost.
For **latency-first**: Jev router alone, accepting 94% routing and 66% coverage at 0.8.
"""
(OUT / "PART_MODEL_DECISIONS.md").write_text(decision, encoding="utf-8")

trace = """# Trace index — claim to code

The study writes per-case rows (prediction, pass/fail, latency, tokens, confidence, trace ID where available) to the JSON files in this folder and the workbook’s raw sheets. Use these paths when the report needs to connect a claim to code.

| Claim | Code | Where the evidence lives |
|---|---|---|
| One path per turn | `app/understand/pipeline.py:understand`; `app/turn/orchestrator.py` | `routing-merged.json` per-case intents |
| Jev typed route | `app/understand/jev.py:jev_route` | `routing-latest.json`, hybrid rows |
| Threshold gate | `settings.router_threshold`; route decision in pipeline | `Router_Gate` sheet, `routing-analysis.md` |
| Planner composes from evidence | `app/plan/agents.py`; `app/plan/builder.py:build_plan` | `datasets-latest.json` |
| Planner cannot invent | evidence refs / `build_plan` pool | `constraints_met`, `grounding` metrics |
| Editor is typed and bounded | `app/plan/editor.py:propose_edit`, `_apply`, locked check | `editor-direct-latest.json` |
| Verifier audits constraints | `app/plan/validator.py:audit_constraints`; `plan_text(with_sources=True)` | `verifier-latest.json` |
| Ask answers from plan | ask path in `app/turn/orchestrator.py` | `ask-adjudicated.json` |
| Memory extraction | `app/application/memory.py:derive_memory` | `memory-latest.json` |
| Effective caps | `app/core/llm.py:PURPOSE_MAX_TOKENS`, `max_tokens_for` | `cap-stress-latest.json` |
| Per-part model config | `app/api/turn.py:effective_settings`; `app/api/views.py` runtime | `models` in `/api/runtime` |
| Temperature | `app/core/llm.py:temperature_for` | Sonnet 0.0 vs 0.2 rows |
| Trace recording | `app/core/trace.py:TurnTrace`; SSE `trace` event | `trace_id` columns; `Trace` sheet |

## Sample evidence

- Routing per-case rows carry the trace's request id (`bench-<arm>-<case>`).
- Planner rows come from `app/eval/runner.py` and include the case id and eval run id; the eval DB for each arm is `backend/data/bench_<dataset>_<arm>.db`.
- Editor rows use `editor.<arm>.<case>` request ids.
- Verifier rows use `verify.<arm>.<case>`.
- Ask rows use the session id and the ask generation in the trace.
- Memory rows use `mem.<case>` request ids.

## Reproduce

```bash
cd "Pathfinder v5/backend"
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_part_bench.py
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_dataset_bench.py hongkong japan
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_editor_direct.py
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_verifier_bench.py
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_ask_bench.py
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_memory_bench.py
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_cap_stress.py
PYTHONPATH=. python scripts/v5_analyze.py
PYTHONPATH=. python scripts/v5_docs.py
```
"""
(OUT / "TRACE_INDEX.md").write_text(trace, encoding="utf-8")

limits = """# Limitations

1. **Small n**: editor 4, verifier 10, ask 11 in-scope, memory 8, planner 14. Differences of one case are not statistically stable; these are screening results, not powered estimates.
2. **One run per arm** except Sonnet router at 0.2 (sensitivity). Temperature > 0 variance is not characterised.
3. **Provider availability**: GPT-5/o-series failed on the Poe-compatible endpoint (`400 upstream gateway failure`); Gemini 3.8 Flash returned an empty smoke answer; DeepSeek's 84-case routing arm timed out under concurrency 4. Reasoning effort could not be tested.
4. **Planner temperature** was not swept; 0.2 is the production setting, not a proven optimum.
5. **Editor scope**: the direct editor test covers remove, move, time change and remove; it does not cover add/research edits or locked-row refusal.
6. **Verifier**: all arms missed v06 (10-minute transfer vs 30-minute constraint). The verifier receives plan text with sources now, but arithmetic/temporal checks still need code.
7. **Ask**: q11 (capital of France) was clarified by all models; it is a scope-handling issue, not an in-scope answer. The ask path is slow (15–20 s) independent of model.
8. **Memory**: one false memory for DeepSeek on the no-fact case. A write-time validation is required before adopting it.
9. **Hybrid**: Jev→LLM is derived from saved per-case predictions, not a live end-to-end run; latency is the sum of stages, not a concurrent measurement.
10. **Cost**: token counts are measured; currency cost is not reported because provider pricing was not fixed in this environment.
11. **Task traces**: the study stores trace IDs and generation summaries, not full prompt/response transcripts, to avoid credential/PII leakage. Prompts live in `app/skills/` and are identified by commit.
12. **Generalisation**: one provider, one date, English/Chinese UI; a single 84-case routing set.
13. **Ask rubric**: q01/q05 were adjudicated after a literal string match failed on `6:00` vs `06:00` and `isn't confirmed`; the adjudicated table is reported. This is a post-run rubric check, not a second model run.
"""
(OUT / "LIMITATIONS.md").write_text(limits, encoding="utf-8")
print("wrote plan/decision/trace/limits")
