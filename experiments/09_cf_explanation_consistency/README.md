# Experiment 09 — LLM-d Explanation Consistency

## Hypothesis
When LLM-d's decision flips under a CF, is its new explanation actually grounded in the edits? Fabrication would indicate post-hoc rationalization.

## Method
For each of 60 AEGIS-minimum-flip CFs, LLM-d re-judges the chart. A GPT-4.1 auditor rates the alignment of LLM-d's post-flip explanation with the CF edits on a 1–5 scale.

## Data
- `data/exp_explanation_consistency.json` — 60 pairs with ratings

## Findings
| LLM-d behavior | n | Mean consistency | Low (≤2) |
|---|---|---|---|
| Flipped | 27 | **4.85** | 0 |
| Did not flip | 33 | 2.67 | 17 (52%) |

When LLM-d flips, 89% of explanations are grounded (score 5). When it doesn't flip, 52% cite unrelated features. Consistent with "holistic reader" rather than fact-level reasoner.

## Run
```bash
python run.py
```

## Analysis
```bash
python analyze.py
```
