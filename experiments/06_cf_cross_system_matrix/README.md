# Experiment 06 — Cross-System 3×3 CF Matrix (TG Anchoring)

## Hypothesis
Different systems reason over different load-bearing features. Applying system X's CF to system Y exposes whether Y's decisions depend on features in X's domain.

## Method
For each of 60 pairs, take each system's self-blocker CF chart (from Experiment 05) and run all 3 systems on it. Populate a 3×3 matrix of flip rates.

## Data
- `data/llmd_tg_on_aegis_cf/all_results.json` — LLM-d and TG judged on AEGIS's CF
- `data/satir_on_llmd_cf_60.json` — AEGIS on LLM-d's CF
- `data/satir_on_tg_cf_60.json` — AEGIS on TG's CF
- Row 2 (LLM-d CF) row 3 (TG CF) for LLM-d/TG self-flip are already in Experiment 05

## Findings
| CF source ↓ / Judged → | AEGIS | LLM-d | TG |
|---|---|---|---|
| **AEGIS CF** (Z3 min-flip, avg 1.9 facts) | 85% | 45% | **8%** |
| **LLM-d CF** (prose) | 50% | 55% | 18% |
| **TG CF** (per-criterion) | 58% | 82% | **75%** |

**TG anchoring (p<10⁻¹¹)**: TG flips 75% on its own criterion-vocabulary CFs, but only 8–18% on fact-level edits from other systems. This is the strongest comparative result in the paper.

## Prompts
Uses the same CF rewriter prompts as Experiment 05 (indirectly — CFs are reused).

## Run
```bash
python run.py                    # LLM-d, TG on AEGIS CF
python run_satir_on_others.py    # AEGIS on LLM-d/TG CFs
```

## Analysis
```bash
python analyze.py                # generates 3×3 matrix
python analyze_significance.py   # McNemar + Wilson CIs
```
