# Experiment 05 — CF Self-Faithfulness (the diagonal)

## Hypothesis
Does each system's decision flip when we rewrite the chart to fix its own cited reasons?

## Method
For each of 60 pairs (all 3 systems originally agreed "ineligible"):
- Extract each system's cited blockers from its own rationale format.
- Ask GPT-4.1 to rewrite the chart to flip those cited facts.
- Re-run the same system on the rewritten chart. Record whether its decision flipped.

### Target extraction per system
- **AEGIS (v2 heuristic)**: walk unsat-core REQ bodies with per-operator heuristic rules (no Z3 optimization). Output: `{variable: target_value}` list.
- **TG**: lookup rows with label `not included` / `excluded`. Output: criterion text list.
- **LLM-d**: GPT-4.1 parses LLM-d's prose explanation. Output: cited fact list.

## Data
- `data/aegis_v2/all_results.json` — AEGIS self-flip results
- `data/tg/all_results.json` — TG self-flip results
- `data/llmd/all_results.json` — LLM-d self-flip results

Each has per-pair result.json with CF chart + miner re-extraction fidelity.

## Findings
| System | Self-flip | 95% CI | Rationale format |
|---|---|---|---|
| AEGIS | 72% (43/60) | [59, 82] | Structured (vars + unsat core) |
| TrialGPT | 75% (45/60) | [63, 84] | Structured (per-criterion labels) |
| GPT-4.1 Direct | 55% (33/60) | [43, 67] | Unstructured (prose) |

McNemar: AEGIS vs TG p=0.85 (tied); AEGIS vs LLM-d p=0.08; TG vs LLM-d p=0.05.

Structured-rationale systems (AEGIS, TG) are comparable at 72-75%. Prose rationale (LLM-d) is lossier at 55%.

**Note**: TG's 75% self-flip is faithfulness to a *wrong* rationale — see Experiment 01 (TG F2=0.188, catastrophic recall).

## Run
```bash
python run_aegis.py     # AEGIS CF probe
python run_tg.py        # TG CF probe
python run_llmd.py      # LLM-d CF probe
```
Env vars:
- `CF_CANDIDATES` — pair list (default `/tmp/cf_candidates_60.json`)
- `CF_OUT` — output directory
- `USE_Z3_OPTIMIZE_PARSER` — set to 1 for v3 MaxSAT (see Experiment 10)
