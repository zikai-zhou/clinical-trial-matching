# Experiment 02 — Pairwise preference

## Hypothesis
When shown two systems' decisions + rationales blinded to identity, a clinician-style GPT-5 judge prefers AEGIS over either baseline.

## Method
For each of the 235 pairs, each pair of systems' outputs (decision + destyled rationale) is submitted in random slot order (A/B) to a GPT-5 judge with a clinician prompt. Judge picks winner + confidence.

## Data
- `data/smt_pairwise_losses.csv` — rows where AEGIS lost; documented totals in notebook.

## Findings
| Contest | AEGIS wins | Opponent wins | Tie |
|---|---|---|---|
| AEGIS vs GPT-4.1 Direct | 195 | 9 | 31 |
| AEGIS vs TrialGPT | 194 | 7 | 34 |

AEGIS wins ~83% of pairwise contests. Mean judge confidence: 0.83.

## Prompts
- `prompts/accuracy_and_sharpness_clinician.prompt` — primary (clinician style)
- `prompts/accuracy_and_sharpness_mechanical.prompt` — engineering style (robustness variant)
- `prompts/accuracy_and_sharpness_neutral.prompt` — neutral (no policy framing) — produces ranking inversion
- `prompts/destyle_verbalization.prompt` — strips system-identifying language before judging

## Analysis
```bash
python analyze.py
```
Regenerates the 195/9/31 and 194/7/34 numbers + win rates.
