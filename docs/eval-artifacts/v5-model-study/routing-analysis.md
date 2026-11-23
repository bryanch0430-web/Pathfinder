# Routing / turn-router analysis

| Arm | Correct | Accuracy | p50 ms |
|---|---:|---:|---:|
| sonnet-0.0 | 83/84 | 98.8% | 6302.8 |
| sonnet-0.2 | 83/84 | 98.8% | 6212.6 |
| flash-lite-0.0 | 83/84 | 98.8% | 4108.2 |
| jev | 79/84 | 94.0% | 624.6 |

## Jev as a pre-gate

| Hybrid | Correct | Accuracy | Jev coverage | LLM fallbacks | Mean latency (sum of stages) |
|---|---:|---:|---:|---:|---:|
| jev+sonnet@0.8 | 83/84 | 98.8% | 66% | 29 | 3329 ms |
| jev+flash@0.8 | 84/84 | 100.0% | 66% | 29 | 1998 ms |

The hybrid is a sequential pre-gate: Jev answers when confidence ≥ the cut-off; otherwise the analyst is called.
Latency is therefore the sum of the two stages for escalated cases, not a concurrent race.
