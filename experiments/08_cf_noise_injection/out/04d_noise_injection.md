# §4.4.4 — Noise injection

## Aggregate

| system | baseline | +noise | Δ |
|---|---|---|---|
| smt | 51/60 = 85.0% | 51/60 = 85.0% | +0.0% |
| llm_d | 27/60 = 45.0% | 27/60 = 45.0% | +0.0% |
| tg | 5/60 = 8.3% | 8/60 = 13.3% | +5.0% |

## Pair-level stability

| system | n | same decision | rate | flip toward eligible | flip toward ineligible |
|---|---|---|---|---|---|
| smt | 60 | 52 | 86.7% | 4 | 4 |
| llm_d | 60 | 50 | 83.3% | 5 | 5 |
| tg | 60 | 53 | 88.3% | 5 | 2 |
