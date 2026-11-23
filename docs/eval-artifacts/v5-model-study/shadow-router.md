# Dual-router shadow analysis

**Status:** retrospective shadow over saved per-case routing predictions. No live provider calls and no runtime change.

- Dataset: `routing` · n = **84**
- Primary analyst: **flash-lite-0.0** (Gemini 3.5 Flash-Lite)
- Agreement: **78/84 = 92.9%**
- Accuracy when agreed: **100.0%**
- Disagreements: **6** · both wrong: **0**

## Policies

| Policy | Accepted | Clarified | Coverage | Correct | Accuracy on accepted | Wilson 95% |
|---|---:|---:|---:|---:|---:|---:|
| analyst only (Flash-Lite) | 84 | 0 | 100.0% | 83 | 98.8% | 93.6%–99.8% |
| Jev only | 84 | 0 | 100.0% | 79 | 94.0% | 86.8%–97.4% |
| agreement gate (accept if both agree; otherwise clarify) | 78 | 6 | 92.9% | 78 | 100.0% | 95.3%–100.0% |
| agreement + analyst confidence ≥ 0.8 | 72 | 12 | 85.7% | 72 | 100.0% | 94.9%–100.0% |
| both confidences ≥ 0.8 (analyst route) | 52 | 32 | 61.9% | 51 | 98.1% | 89.9%–99.7% |

## Confidence calibration (descriptive)

| Router | ECE | Brier | AUROC | Mean confidence |
|---|---:|---:|---:|---:|
| Flash-Lite analyst | 0.088 | 0.049 | 0.904 | 0.900 |
| Jev | 0.116 | 0.067 | 0.875 | 0.825 |
| Sonnet analyst | 0.114 | 0.030 | 0.843 | 0.874 |

ECE uses five equal-width bins; AUROC is confidence as a detector of correctness. Small n and one run per case: descriptive only.

## Latency

| Arm | Mean ms |
|---|---:|
| Flash-Lite analyst | 3970 |
| Jev | 659 |
| Parallel max (no overhead) | 3970 (p50 4108) |

This is the maximum of saved per-arm latencies, not a measured concurrent run.

## Disagreements

| id | expected | analyst | Jev | analyst correct | Jev correct |
|---|---|---|---|---:|---:|
| r06 | ask | ask (0.95) | unclear (0.63) | yes | no |
| r12 | unclear | unclear (0.5) | ask (0.73) | yes | no |
| r13 | unclear | unclear (0.4) | plan (0.51) | yes | no |
| r19 | unclear | unclear (0.95) | plan (0.54) | yes | no |
| r64 | ask | ask (0.95) | unclear (0.69) | yes | no |
| r68 | unclear | plan (0.85) | unclear (0.81) | no | yes |

## Interpretation

Agreement was perfect on this 84-case retrospective set: all 78 agreed predictions were right, and the six disagreements contained the analyst's only error. The gate would have clarified 7.1% of cases and accepted 92.9% at 100% accuracy.

One run per case, one model/provider/date, n=84, and no live concurrent shadow. The agreement gate was more accurate than analyst-only on this sample but at a coverage cost; this is a hypothesis, not a general result.

The agreement gate looks promising but does not establish that it generalises. The next step is a **live shadow mode** that logs both routes on real turns without changing the decision, followed by calibration on a held-out set.
