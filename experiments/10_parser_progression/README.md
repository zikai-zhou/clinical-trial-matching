# Experiment 10 — Parser Progression (Appendix C)

## Hypothesis
AEGIS's structured rationale admits formal post-processing (Z3 MaxSAT) that pure-LLM rationales cannot. This experiment shows the incremental effect of better parsers.

## Method
Rerun AEGIS's self-blocker CF on 60 pairs using three different methods to convert unsat-core REQs into CF targets:

1. **v0**: GPT-4.1 reads SMT-LIB text directly and emits targets.
2. **v2** (fair): Heuristic s-expression parser; walks each REQ body per-operator; no optimization. Matches the "direct extraction" style used by LLM-d/TG.
3. **v3**: Z3 MaxSAT over the unsat core + full program; finds the provably-minimum-flip target set.

## Data
- `data/v0_gpt_parser/all_results.json` — 62% flip
- `data/v2_heuristic/all_results.json` — 72% flip (fair)
- `data/v3_z3_maxsat/all_results.json` — 85% flip

## Findings
| Version | Parser | Self-flip | Avg targets/pair |
|---|---|---|---|
| v0 | GPT-4.1 as SMT parser | 62% | 2.7 |
| v2 | Heuristic s-expr parser | 72% | 15.9 |
| **v3** | **Z3 MaxSAT min-flip** | **85%** | **1.9** |

- v2 → v0 gap (10 pp): SMT-LIB is hard for LLMs to read.
- v3 → v2 gap (13 pp): global constraint solving beats local heuristics.

v3 is not a fair comparison with LLM-d/TG (they don't have Z3). Its purpose is to demonstrate what formal post-processing of structured rationale achieves.

## Run
```bash
# v2 (heuristic)
USE_Z3_OPTIMIZE_PARSER=0 python ../05_cf_self_faithfulness/run_aegis.py
# v3 (Z3 MaxSAT)
USE_Z3_OPTIMIZE_PARSER=1 python ../05_cf_self_faithfulness/run_aegis.py
```

## Analysis
```bash
python analyze.py
```
