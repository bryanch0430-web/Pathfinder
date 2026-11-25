# Prompt-scaffold ablation (full skill vs minimal router)

Model: `deepseek-flash`, temperature: 0.0, n=24.

| Condition | Correct | Accuracy | p50 | Mean tokens |
|---|---:|---:|---:|---:|
| full skill | 5/24 | 20.8% | 367.5 ms | 0.0 |
| minimal prompt | 5/24 | 20.8% | 368.6 ms | 0.0 |

The minimal condition preserves the same model, temperature, schema and payload but removes the domain guidance.
