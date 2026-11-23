# v5 analyst/router comparison

Generated: 2026-11-22T18:18:35.449584+00:00
Dataset: routing, n=84, one run per arm; missing/error counts as wrong; same frozen utterances; Wilson 95%; exact McNemar vs sonnet-0.0

| Arm | Model | Temp | Correct | Accuracy | Wilson 95% | p50 ms | Mean tokens |
|---|---|---:|---:|---:|---|---:|---:|
| jev | jev-latest | — | 79/84 | 94.0% | 86.8%–97.4% | 624.6 | None |

## Accuracy by intent

| Arm | plan | modify | ask | unclear |
|---|---:|---:|---:|---:|
| jev | 100.0% (21/21) | 100.0% (21/21) | 90.5% (19/21) | 85.7% (18/21) |

## Confidence gate

| Arm | cutoff | coverage | accuracy on kept | escalated |
|---|---:|---:|---:|---:|
| jev | 0.6 | 89% | 96.0% | 9 |
| jev | 0.7 | 75% | 98.4% | 21 |
| jev | 0.8 | 66% | 100.0% | 29 |
| jev | 0.9 | 43% | 100.0% | 48 |

## Paired comparison vs sonnet-0.0

| Arm | only baseline | only arm | McNemar p |
|---|---:|---:|---:|
