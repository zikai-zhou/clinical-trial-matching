# Experiments

One folder per finding reported in the paper. Each folder is self-contained:

```
NN_experiment_name/
├── README.md       # hypothesis, method, how to run
├── run.py          # entrypoint (some experiments have multiple runners)
├── analyze.py      # regenerates paper tables/numbers from `data/`
├── prompts/        # .prompt files used
├── data/           # saved result JSONs
└── out/            # regenerated tables/figures (ephemeral; produced by analyze.py)
```

| # | Experiment | Paper section | Primary finding |
|---|---|---|---|
| 01 | Retrieval F2 | §4.2 | AEGIS 0.627 > LLM-d 0.556 > TG 0.188 |
| 02 | Pairwise preference | §4.2.1 | AEGIS ~83% wins under GPT-5 clinician judge |
| 03 | Judge rubric + verbalizer×judge | §4.2.2 | AEGIS wins all 12 verbalizer×judge cells |
| 04 | k=10 self-consistency AFR | §4.3 | AEGIS 0.047 lowest run-to-run disagreement |
| 05 | CF self-faithfulness (diagonal) | §4.4.2 | AEGIS 72%, TG 75%, LLM-d 55% |
| 06 | CF cross-system 3×3 matrix | §4.4.2 | **TG criterion-anchoring, p<10⁻⁷** |
| 07 | CF dose-response | §4.4.3 | LLM-d dose-insensitive |
| 08 | CF noise injection | §4.4.4 | Systems 83–88% stable to irrelevant noise |
| 09 | CF explanation consistency | §4.4.5 | LLM-d grounded when flips (4.85/5), disengaged when not (2.67/5) |
| 10 | Parser progression | Appendix C | v0 62% → v2 72% → v3 (Z3 MaxSAT) 85% |
| 11 | Case studies | §6 | 3 pairs showing reasoning-pattern differences |

## Regenerating paper tables

```bash
# From repo root
for d in experiments/*/; do
  if [ -f "$d/analyze.py" ]; then
    python "$d/analyze.py"
  fi
done
```

Or via `paper/reproduce/make_all.py` which points into these same scripts.

## Pair pools used

- **235 pairs**: full SIGIR 2016 evaluation set (used by 01, 02, 03, 04).
- **60 pairs**: subset where all 3 systems agreed "ineligible" at the time of the main inference pass (used by 05–10).

Pair lists are at `/tmp/cf_candidates_60.json` (ineligible pool) and `/tmp/cf_eligible_candidates_60.json` (eligible pool, used for experiments that required eligible originals — not reported in final paper).

## Dependencies

All experiments share:
- SatIR pipeline (`smt_matcher/` package)
- GPT-4.1 via Azure (env: `OPENAI_ENDPOINT`, `OPENAI_API_KEY`)
- Python 3.11.9 + `z3-solver==4.12.2.0`, `statsmodels`, `dspy-ai`
- Dataset: SIGIR 2016 clinical trials track (`/tmp/satir_full_dataset/`)
