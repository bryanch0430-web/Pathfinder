# v5 analyst/router comparison

Generated: 2026-11-23T07:53:21.573953+00:00
Dataset: routing, n=84, one run per arm; missing/error counts as wrong; same frozen utterances; Wilson 95%; exact McNemar vs sonnet-0.0

| Arm | Model | Temp | Correct | Accuracy | Wilson 95% | p50 ms | Mean tokens |
|---|---|---:|---:|---:|---|---:|---:|
| flash-lite-0.0 | gemini-3.5-flash-lite | 0.0 | 82/84 | 97.6% | 91.7%–99.3% | 3985.1 | 3017.2 |

## Accuracy by intent

| Arm | plan | modify | ask | unclear |
|---|---:|---:|---:|---:|
| flash-lite-0.0 | 100.0% (21/21) | 100.0% (21/21) | 100.0% (21/21) | 90.5% (19/21) |

## Confidence gate

| Arm | cutoff | coverage | accuracy on kept | escalated |
|---|---:|---:|---:|---:|
| flash-lite-0.0 | 0.6 | 98% | 97.6% | 2 |
| flash-lite-0.0 | 0.7 | 98% | 97.6% | 2 |
| flash-lite-0.0 | 0.8 | 98% | 97.6% | 2 |
| flash-lite-0.0 | 0.9 | 93% | 98.7% | 6 |

## Paired comparison vs sonnet-0.0

| Arm | only baseline | only arm | McNemar p |
|---|---:|---:|---:|
