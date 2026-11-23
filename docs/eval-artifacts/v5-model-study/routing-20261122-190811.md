# v5 analyst/router comparison

Generated: 2026-11-22T19:14:50.810903+00:00
Dataset: routing, n=84, one run per arm; missing/error counts as wrong; same frozen utterances; Wilson 95%; exact McNemar vs sonnet-0.0

| Arm | Model | Temp | Correct | Accuracy | Wilson 95% | p50 ms | Mean tokens |
|---|---|---:|---:|---:|---|---:|---:|
| deepseek41-0.0 | deepseek-v4.1-flash-1017 | 0.0 | 83/84 | 98.8% | 93.6%–99.8% | 8549.0 | 3319.5 |

## Accuracy by intent

| Arm | plan | modify | ask | unclear |
|---|---:|---:|---:|---:|
| deepseek41-0.0 | 100.0% (21/21) | 100.0% (21/21) | 100.0% (21/21) | 95.2% (20/21) |

## Confidence gate

| Arm | cutoff | coverage | accuracy on kept | escalated |
|---|---:|---:|---:|---:|
| deepseek41-0.0 | 0.6 | 99% | 98.8% | 1 |
| deepseek41-0.0 | 0.7 | 99% | 98.8% | 1 |
| deepseek41-0.0 | 0.8 | 98% | 100.0% | 2 |
| deepseek41-0.0 | 0.9 | 88% | 100.0% | 10 |

## Paired comparison vs sonnet-0.0

| Arm | only baseline | only arm | McNemar p |
|---|---:|---:|---:|
