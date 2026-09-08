# Reproducing the 3-Cell × 7-System Self-Faithfulness Ablation

> **For the FULL pipeline starting from raw SIGIR data (no pre-computed
> SMT mine or arbiter cache), see
> [../../../../docs/REPRODUCE_FROM_SCRATCH.md](../../../../docs/REPRODUCE_FROM_SCRATCH.md)
> stages 1-3 first.** This document assumes the v9 SMT mine and arbiter
> cache already exist; it covers the CF self-faithfulness pipeline only.

This document explains how to re-run the full experiment from scratch and
get statistically-equivalent results.

## Prerequisites

1. **Python 3.11.9** with `z3-solver` installed (the driver uses Z3 for the
   AEGIS MaxSat blocker extraction).
   ```bash
   pyenv install 3.11.9
   pyenv shell 3.11.9
   pip install z3-solver requests
   ```

2. **cmsrc matcher** — the AEGIS pipeline calls `match_patient_to_trial.py`
   from a sibling repo via subprocess. Default location:
   `/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT/cmsrc`. Override via
   `CMSRC_DIR` env var.

3. **Azure OpenAI deployments** for gpt-4.1 and gpt-5. Provide both
   endpoints + API key in `.env` at the repo root:
   ```
   OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/openai/deployments/gpt-4.1
   OPENAI_ENDPOINT_GPT5=https://<your-resource>.openai.azure.com/openai/deployments/gpt-5
   OPENAI_API_KEY=<your-key>
   ```

4. **Cached upstream artifacts** (already in the repo):
   - `experiments/53_v2_full/cmsrc_out_REMINE_v9_full/` — per-pair SMT mine
   - `experiments/53_v2_full/arbiter_cache/` — arbiter overrides
   - `dataset/clinical_trial/sigir/{corpus.jsonl,queries.jsonl}`
   - Published verdicts for v5, v5_blockers, tg, shah, aegis arbiter

## Determinism notes

- **LLM calls** are temperature=0 for gpt-4.1 (`call_gpt41`). gpt-5 ignores
  temperature, so its outputs can vary between runs at the bit level — but
  the aggregate metrics are stable across re-runs (within ~1-2 pp).
- **Random cohort selection** uses MD5(pair) + AEGIS_COHORT_SEED. Same seed
  + same pair list always gives the same cohort assignment. The historical
  random-cohort numbers (cell 1 seed=1/2/3 → 82.3% / 87.7% / 89.3%) were
  generated BEFORE this fix, when `hash(pair)` was salted by PYTHONHASHSEED.
  A fresh re-run with the deterministic hash will yield DIFFERENT individual
  pair assignments but statistically-equivalent rates.
- **Z3 MaxSat** is deterministic given the same input program + LLM-asserted
  atoms.

## One-shot reproduction

```bash
cd /path/to/TrialGPT-SMT-Refactored
set -a; source .env; set +a

HERE=experiments/counterfactual/05_self_faithfulness/ablation_full_corpus
bash $HERE/launch_all.sh           # 3 cells × 16 slices, per-side maxsat
```

After all 48 slices finish (~50–90 min depending on Azure throughput):

```bash
python $HERE/summarize_3cell.py
python $HERE/build_mbench_3cell.py
```

## Variant runs

| variant | env vars | output dir suffix |
|---|---|---|
| baseline (default) | (none) | none |
| joint MaxSat | `AEGIS_JOINT=1 OUT_SUFFIX=_joint` | `_joint` |
| random cohort, seed N | `AEGIS_COHORT=random AEGIS_COHORT_SEED=N OUT_SUFFIX=_randcohort_seedN` | `_randcohort_seedN` |
| v5_cot_gpt5 only | `SYSTEMS=v5_cot_gpt5 OUT_SUFFIX=_v5cot5` | `_v5cot5` |

To run only one cell or one slice:
```bash
CELL=1 SLICE=0/16 SYSTEMS=aegis,v5_cot \
  python $HERE/run_full_ablation.py
```

## Expected numbers (FINAL: no truncation + AEGIS random-cohort seed=2)

| system        | cell1 (4.1m+4.1v) | cell2 (4.1m+v3v) | cell3 (5m+v3v) |
|---------------|------------------:|-----------------:|---------------:|
| **aegis**     | **81.9% (122/149)** | **81.1% (137/169)** | **85.2% (161/189)** |
| v5            | 37.6% ( 80/213)   | 41.0% ( 84/205)  | 49.3% (111/225) |
| v5_cot (4.1)  | 55.1% (220/399)   | 58.9% (224/380)  | 65.7% (274/417) |
| v5_cot_gpt5   | 50.9% (216/424)   | 52.9% (212/401)  | 57.7% (258/447) |
| v5_blockers   | 44.6% (145/325)   | 49.6% (167/337)  | 58.0% (220/379) |
| tg            | 55.2% (138/250)   | 58.4% (139/238)  | 62.9% (185/294) |
| shah (binary) | 16.5% ( 31/188)   | 17.5% ( 31/177)  | 27.6% ( 56/203) |

A fresh re-run should land within ~±2 pp of each cell due to gpt-5 stochasticity.
AEGIS uses `AEGIS_COHORT=random AEGIS_COHORT_SEED=2` with the deterministic md5 hash.
All systems use the no-truncation modifier/validator path (full-length criterion,
chart_fact, rationale, trial, preserve text).

## Empirical reproduction test (13-pair slice, 2026-05-15)

A fresh run of slice 0/25 on cell 1 (n=13) was compared to the original
output, restricted to the same pairs:

| layer | reproduce rate |
|---|---|
| AEGIS Z3 MaxSat — deciding cohort | **12/12 (100%)** |
| AEGIS Z3 MaxSat — target atom set | **12/12 (100%)** |
| AEGIS Z3 MaxSat — target values   | **12/12 (100%)** |
| AEGIS rejudged eligibility        | 10/12 (83%) |
| V5 first-round + rejudge          | 7/11 (64%) |

The deterministic Z3 core is bit-exact reproducible. The modifier (LLM) +
cmsrc rejudge layer has natural variance because gpt-5 cannot be made
strictly deterministic. On small slices (n ≤ 20) the resulting flip rate
is dominated by sample noise; on n ≥ 100 the rate stabilizes within ±2pp.

## Smoke test (single pair)

To verify the pipeline end-to-end with one pair:
```bash
CELL=1 SLICE=0/200 SYSTEMS=v5,v5_cot,aegis \
  python $HERE/run_full_ablation.py
# Output: out/cell1_4.1m_4.1v_filt/records.0of200.jsonl (1 record)
```

## File map

```
ablation_full_corpus/
├── README.md                     # overview
├── REPRODUCE.md                  # this file
├── run_full_ablation.py          # MAIN DRIVER
├── launch_all.sh                 # 3 cells × 16 slices launcher
├── summarize_3cell.py            # produce summary_3cell.csv
├── build_mbench_3cell.py         # produce mbench drilldown
├── retry_errors.py               # strip errored cells for resume
├── test_joint_maxsat.py          # sanity test for joint MaxSat
├── rerun_28_with_joint.py        # targeted joint test on 28 pairs
├── analyze_aegis_failures.py     # AEGIS failure mode analysis
├── aegis_failure_analysis/
│   ├── FINDINGS.md               # writeup of joint MaxSat result
│   └── PLAN_joint_maxsat.md      # original plan
├── scripts_archive/              # historical /tmp scripts preserved
├── out/                          # per-cell records.*.jsonl
└── logs/                         # per-slice stdout
```

## External dependencies modified in this work

These changes live OUTSIDE ablation_full_corpus/ and must be present:

1. `experiments/counterfactual/utils/cf_maxsat.py`:
   - Added `_merge_programs()` — dedup decls, namespace `:named` labels
   - Added `maxsat_blockers_joint()` — joint inc∧exc MaxSat

2. `experiments/counterfactual/utils/cf_blockers.py`:
   - Added `aegis_blockers_joint()`

3. `matchers/systems/shahlab/prompts/koopman_binary.prompt` — binary Shah prompt

4. `matchers/systems/single_shot_llm/prompts/V5_COT.prompt` — V5+CoT prompt
