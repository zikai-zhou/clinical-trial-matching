# Experiment 07 — CF Dose-Response

## Hypothesis
A system that reasons from specific facts should show a monotonic increase in flip rate as more load-bearing facts are flipped. A system that reasons holistically shouldn't.

## Method
For each of 60 pairs, generate CFs that flip K=1, 2, 3, or all of AEGIS's Z3-identified minimum-flip target facts. Run all 3 systems on each CF.

## Data
- `data/cf_dose_response_60/all_results.json` — 240 runs (60 pairs × 4 K-levels)

## Findings
| Dose | Avg facts | AEGIS | LLM-d | TG |
|---|---|---|---|---|
| 1 | 1.0 | 48% | 35% | 5% |
| 2 | 1.6 | **63%** | 35% | 8% |
| 3 | 1.8 | 63% | 37% | 12% |
| all | 1.9 | 58% | 35% | 7% |

- **AEGIS**: monotonic dose-response (48% → 63% plateau)
- **LLM-d**: **dose-insensitive** (flat at ~35%)
- **TG**: barely responsive (5–12%)

## Run
```bash
python run.py    # 240 trials
```

## Analysis
```bash
python analyze.py
```
