# Full-Corpus 3-Cell Ablation

This directory contains the driver and outputs for the **full-corpus**
self-faithfulness ablation across three (modifier × validator) configurations.

## Cells

| cell | modifier | validator | shah/tg rationale |
|------|----------|-----------|-------------------|
| **cell1_4.1m_4.1v_filt** | gpt-4.1 | gpt-4.1 itemized | blocker-filtered |
| **cell2_4.1m_v3v_filt**  | gpt-4.1 | gpt-5 v3 simclin | blocker-filtered |
| **cell3_5m_v3v_filt**    | gpt-5   | gpt-5 v3 simclin | blocker-filtered |

For all cells the v3 validator's validity criterion is
`coherent AND flips_target_atom AND keeps_other_facts` (apples-to-apples with
gpt-4.1 validator's `all_flipped AND no_overcorrection`).

## Systems evaluated (6)

- `aegis` — SMT matcher (cmsrc subprocess)
- `v5` — single-shot LLM (V5 prompt)
- `v5_cot` — V5 + chain-of-thought + structured `decisive_blockers` (NEW baseline)
- `v5_blockers` — two-step (blockers, supports) variant
- `tg` — TrialGPT per-criterion judge
- `shah` — Shahlab Koopman, **binary-forced** (drops the ternary
  `gd ∈ {0,1,2}` middle option). Uses
  `matchers/systems/shahlab/prompts/koopman_binary.prompt` with an
  `evidence_status` field distinguishing deterministic blockers from
  chart-silent deferrals.

Each system runs on **its own full ineligible set** (the pairs where its
published verdict = ineligible), not on the 5-way diagonal intersection.
v5_cot has no published verdicts; it runs on the union of all systems'
ineligible pairs and self-filters at first round.

## Files

- `run_full_ablation.py` — main driver (set `CELL`, `SLICE`, `SYSTEMS` env vars).
- `launch_all.sh` — launches 3 cells × N slices (default 16) in nohup background.
- `out/<cell_tag>/records.<i>of<N>.jsonl` — per-pair records. Each record has
  per-system fields: prompt, raw response, cf_chart, cf_validation, rejudged
  verdict + rationale.
- `logs/cell<C>.slice<i>of<N>.log` — stdout/stderr per slice.
- `scripts_archive/` — earlier one-off scripts used during 2x2 development
  (preserved from `/tmp/` for traceability).

## Re-running

```bash
bash launch_all.sh           # 3 cells × 16 slices (default)
bash launch_all.sh 1 8       # only cell 1, 8 slices
```

Resume is automatic: completed (pair, system) cells are loaded from existing
records.<i>of<N>.jsonl files.

## Per-cell traceability

Every record contains:
- the first-round matcher prompt + raw response (or, for aegis, the cmsrc
  subprocess command + full.json)
- the cited blockers / preserve atoms / other atoms (for aegis), or the
  blocker-filtered rationale (for shah/tg/v5_cot/v5_blockers)
- the modifier's generated counterfactual chart
- the validator's full output + prompt
- the rejudged verdict + rationale

After all slices complete, build the mbench drilldown (per-system /
per-outcome directory tree) with:

```bash
python experiments/counterfactual/05_self_faithfulness/build_mbench.py \
    --input out/<cell_tag>/records.merged.jsonl \
    --output mbench_<cell_tag>/
```

(Slices can be merged first with `cat out/<cell_tag>/records.*of16.jsonl >
out/<cell_tag>/records.merged.jsonl`.)
