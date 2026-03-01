# Experiment 04 — k=10 Run-to-Run Self-Consistency

## Hypothesis
At temperature 0, a deterministic-solver system (AEGIS) is more run-to-run consistent than pure-LLM systems.

## Method
Run each system 10 times on the same 235 pairs at temperature 0.0, top-p 0.001, seed 42. For each pair, compute:
- Aggregate Flip Rate (AFR) = fraction of the 45 pairwise run comparisons that disagree
- Shannon entropy (bits) of the decision distribution

## Data
- `data/h1_fliprate_235pairs_10repeats__smt__CORRECTED.json` — AEGIS per-pair labels
- `data/h1_fliprate_235pairs_10repeats__llm_direct-trialgpt.json` — LLM-d + TG per-pair labels
- `data/fliprate_k10_detailed.md` — documented aggregates

## Findings
| System | Mean AFR | 95% CI | Entropy (bits) | Pairs w/ flip |
|---|---|---|---|---|
| **AEGIS** | **0.047** | [0.030, 0.065] | **0.090** | **27/235** |
| GPT-4.1 Direct | 0.067 | [0.048, 0.090] | 0.131 | 40/235 |
| TrialGPT | 0.053 | [0.037, 0.070] | 0.110 | 42/235 |

AEGIS has the lowest run-to-run disagreement on all three metrics.

## Run
```bash
python run.py  # runs 10 repeats × 235 pairs × 3 systems
```

## Analysis
```bash
python analyze.py
```
