# Experiment 03 — Judge rubric + verbalizer×judge robustness

## Hypothesis
AEGIS's advantage on the GPT-5 judge is robust across verbalizer phrasing and judge prompt style. Additionally, LLM-d wins the individual sharpness axes despite losing overall — the "quality-decision inversion" showing its rationale is stylistically strong but decoupled from the decision.

## Method
- **Rubric**: judge scores each decision 1-5 overall + 4 sharpness axes (decisiveness, evidence_specificity, conciseness, actionability).
- **Robustness**: 5 verbalizer variants × 4 judge prompts = 20 cells. 12 tested.

## Data
- `data/summary_clinician.json` — primary results (N=235 under clinician judge)
- `data/VERBALIZER_JUDGE_MATRIX.md` — the 12-cell win-rate matrix
- `data/VERBALIZER_FINAL_ABLATION.md` — per-axis rubric breakdown

## Findings
| | AEGIS | LLM-d | TG |
|---|---|---|---|
| Judge accuracy | **0.804** | 0.757 | 0.617 |
| Mean rating (1–5) | **4.22** | 4.03 | 3.47 |
| Decisiveness | 4.03 | **4.25** | 3.83 |
| Evidence specificity | 3.22 | **3.72** | 3.50 |
| Conciseness | 4.75 | **4.82** | 4.65 |
| Actionability | 3.20 | **3.48** | 3.12 |

LLM-d wins every sharpness axis but loses overall — quality-decision inversion.

Verbalizer×judge matrix: AEGIS wins all 12 tested cells. Neutral judge compresses wins to ~60% (over-exclusion bias).

## Prompts
- All 4 judge variants in `prompts/`
- All 5 verbalizer variants in `prompts/`

## Analysis
```bash
python analyze.py
```
