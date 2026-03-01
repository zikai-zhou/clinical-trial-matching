# Experiment 08 — CF Noise Injection

## Hypothesis
Adding trial-irrelevant facts (hair color, hobbies, favorite foods) should not change CF flip rates. Systems that shift under noise are sensitive to chart-level features rather than specific facts.

## Method
Take AEGIS's minimum-flip CF charts (from Experiment 05) and add 3–4 clinically plausible but trial-irrelevant sentences. Re-run all 3 systems.

## Data
- `data/cf_noise_injection_60/all_results.json` — 60 noisy CFs × 3 systems = 180 decisions

## Findings
| System | Baseline | +Noise | Δ (aggregate) | Pair-level stability |
|---|---|---|---|---|
| AEGIS | 85% | 85% | 0 | 87% |
| LLM-d | 45% | 45% | 0 | 83% |
| TG | 8% | 13% | +5 pp (ns) | 88% (asymmetric: 5 toward eligible, 2 away) |

Aggregate rates hide pair-level volatility; all systems show 12–17% noise-induced decision instability. TG's suggestive verbosity bias (+5 pp toward eligible) is not significant at n=60.

## Run
```bash
python run.py
```

## Analysis
```bash
python analyze.py
```
