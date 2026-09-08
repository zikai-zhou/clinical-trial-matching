# Experiment 01 — Retrieval F2

## Hypothesis
AEGIS achieves higher F2 (recall-primary, asymmetric cost) on clinical trial matching than LLM baselines.

## Method
Single GPT-5 clinician-style judge produces a policy-compliant verdict on each of 235 patient-trial pairs. F2 computed per system against judge verdicts as ground truth.

## Data
- `data/summary.json` — aggregate metrics for all 3 systems under the clinician judge prompt.

## Findings
| System | F2 | Precision | Recall |
|---|---|---|---|
| **AEGIS** | **0.627** | 0.281 | 0.840 |
| GPT-4.1 Direct | 0.556 | 0.236 | 0.786 |
| TrialGPT | 0.188 | 0.428 | 0.170 |

Numbers read directly from `data/summary.json` → `aggregate.accuracy.{smt,llm_direct,trialgpt}.rate`. (F2 = see judge prompt for weighting; stored as `.rate` under the accuracy axis of the aggregate.)

## Prompts
- `prompts/accuracy_and_sharpness_clinician.prompt` — primary judge prompt
- `prompts/verbalize_prescreen_unified_v3.prompt` — rationale verbalizer (converts each system's raw output to uniform prescreen style)

## Run
The main inference pass that produced these results is too large to rerun here; see `evaluation/accuracy_judge_runner.py` or the `sql_retrieval/meval/` pipeline for the full runner.
