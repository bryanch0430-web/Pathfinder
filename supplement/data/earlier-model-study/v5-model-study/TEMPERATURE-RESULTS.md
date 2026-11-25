# Temperature and evidence checks — 23 Nov 2026

## Valid runs

### Routing temperature (Sonnet 5.5, current prompt, n=84)

Source: `routing-20261123-091715.json`.

| Arm | Correct | Accuracy | p50 | Mean tokens |
|---|---:|---:|---:|---:|
| sonnet-0.0 | 83/84 | 98.8% | 6,464 ms | 5,780 |
| sonnet-0.2 | 83/84 | 98.8% | 6,249 ms | 5,686 |

Paired McNemar: zero discordant pairs, p=1.0. Temperature 0 and 0.2 are indistinguishable on this set.

### Verifier temperature (DeepSeek Flash, n=20)

Source: `verifier-temperature.json`.

| Temperature | Correct | False passes | False alarms | p50 |
|---:|---:|---:|---:|---:|
| 0.0 | 18/20 | 2 | 0 | 1,803 ms |
| 0.2 | 18/20 | 2 | 0 | 1,785 ms |

Both temperatures produce the same two false passes (v06, v14). Temperature 0 is retained for reproducibility, not because it improves accuracy.

### Edit path (Sonnet, 5 cases)

Source: `datasets-20261123-085453.json`.

- 5/5 passed; 100% stop preservation
- 2.0 model calls per edit
- 13,726 tokens per edit
- The suite has **no replan arm**; this is not a paired edit-vs-replan result.

## Invalid / incomplete runs

### Editor temperature

`editor-temperature-latest.json` is invalid: the Poe endpoint returned HTTP 402
("You've used up your points!") for most calls. The five passing T=0 rows and one
passing T=0.2 row are not comparable.

### Skills ablation

Both the Sonnet run and the DeepSeek retry are invalid. The Sonnet run hit the same
HTTP 402 quota; the DeepSeek retry fell back to `unclear` with zero tokens. Do not
use these results.

## Pricing snapshot

Source: <https://huggingface.co/spaces/Presidentlin/llm-pricing-calculator>.

| Model | Input $/M | Output $/M |
|---|---:|---:|
| Claude Sonnet 5.5 | 2.00 | 10.00 |
| Gemini 3.5 Flash-Lite | 0.30 | 2.50 |
| DeepSeek V4-Flash | 0.14 | 0.28 |

For the measured 3,017-token routing turn with a 90/10 input/output assumption:

- Sonnet: $0.00845 per turn; $8.45 per 1,000 turns
- Flash-Lite: $0.00157 per turn; $1.57 per 1,000 turns
- DeepSeek Flash: $0.00047 per turn; $0.47 per 1,000 turns

These are estimates, not invoices. The artifact records all-input and all-output
bounds; exact prompt/completion split was not persisted in the routing artifact.
