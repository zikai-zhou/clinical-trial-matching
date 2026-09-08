# Reproducing CF self-faithfulness experiments

Two experimental conditions exist, differing only in **which LLM rewrites
the patient chart given a system's cited blockers**:

| Modifier | Folder | Header rate (paper) |
|---|---|---|
| gpt-4.1 (original) | `mbench_modifier_gpt4_1/` | aegis 81.4% |
| gpt-5 (May-13 update) | `mbench_modifier_gpt5/` | aegis pending full rejudge |

Everything else (patient chart, trial criteria, system matchers, validator)
is held constant across the two conditions.

## Pipeline (4 stages per pair × per system)

```
01_first_round/      original matcher verdict + cited blockers
02_blocker_extraction/  what the modifier was asked to flip
03_cf_generation/    chart edit + validator audit
04_rejudged/         second judge: same matcher rematches the CF
```

Each pair lands in exactly one outcome bucket:
- `flipped/`        — CF valid + 04_rejudged says eligible
- `not_flipped/`    — CF valid + 04_rejudged says ineligible
- `invalid_cf/`     — validator rejected the modifier's output
- `skipped/`        — first round had no UNSAT / no blockers

Self-faithfulness rate = `|flipped| / (|flipped| + |not_flipped|)`.

## Data lineage

### Modifier = gpt-4.1

Single self-contained run:

```
out/self_faithfulness.jsonl
  └── produced by: experiments/counterfactual/05_self_faithfulness/run.py
  └── modifier: experiments/counterfactual/utils/cf_generator.py (gpt-4.1)
  └── validator: experiments/counterfactual/utils/cf_validator.py
  └── 1st judge: each system's own matcher (loaded via cf_dataset.py)
  └── 2nd judge: same matcher rematched via cf_judge.py / cmsrc subprocess
```

mbench layout produced by `build_mbench.py`.

### Modifier = gpt-5

Pipeline split across several runs in May-13:

```
modifier_gpt5_scripts/run_gpt5_modifier_smt.py        SMT/aegis CFs
modifier_gpt5_scripts/run_gpt5_modifier_baselines.py  v5, v5_blockers, tg, shah, v5_gpt5 CFs
modifier_gpt5_scripts/rejudge_baselines_gpt5modifier.py   2nd judge for baselines
modifier_gpt5_scripts/rejudge_aegis_gpt5modifier_full.py  2nd judge for aegis (cmsrc subprocess)
```

Outputs:

```
out/self_faithfulness_smt_gpt5modifier.jsonl                aegis CFs + simclin
out/self_faithfulness_baselines_gpt5modifier.jsonl          baseline CFs
out/self_faithfulness_baselines_gpt5modifier_rejudged.jsonl baseline 2nd judge
out/self_faithfulness_aegis_gpt5modifier_rejudged.jsonl     aegis 2nd judge  ← NEW
```

mbench layout produced by `build_mbench_gpt5.py`.

## End-to-end rerun

```bash
# Env vars: OPENAI_ENDPOINT (gpt-4.1 deployment), OPENAI_ENDPOINT_GPT5,
# OPENAI_API_KEY. Set via .env.

# ============ gpt-4.1 condition ============
python experiments/counterfactual/05_self_faithfulness/run.py \
    --systems aegis,v5,tg,shah \
    --output out/self_faithfulness.jsonl
python experiments/counterfactual/05_self_faithfulness/build_mbench.py

# ============ gpt-5 condition ============
python modifier_gpt5_scripts/run_gpt5_modifier_smt.py \
    --out out/self_faithfulness_smt_gpt5modifier.jsonl
python modifier_gpt5_scripts/run_gpt5_modifier_baselines.py \
    --out out/self_faithfulness_baselines_gpt5modifier.jsonl
python modifier_gpt5_scripts/rejudge_baselines_gpt5modifier.py \
    --in  out/self_faithfulness_baselines_gpt5modifier.jsonl \
    --out out/self_faithfulness_baselines_gpt5modifier_rejudged.jsonl
python modifier_gpt5_scripts/rejudge_aegis_gpt5modifier_full.py
python experiments/counterfactual/05_self_faithfulness/build_mbench_gpt5.py
```

## Per-pair file contents (mirror gpt-4.1 layout exactly)

```
<pair>/
├── 00_summary.md
├── 01_first_round/
│   ├── chart.txt              original patient chart
│   ├── trial.txt              trial criteria text
│   ├── rationale.txt          first matcher's verdict + rationale
│   └── (aegis only) variants/ per-cohort artifacts, unsat cores
├── 02_blocker_extraction/
│   ├── blockers.json          cited blockers handed to modifier
│   └── blockers.md            human-readable rendering
├── 03_cf_generation/
│   ├── chart_original.txt
│   ├── chart_cf.txt           ← modifier output
│   └── validator_audit.{json,md}
└── 04_rejudged/
    ├── rationale.txt          2nd matcher's verdict
    └── (aegis only) atoms / SMT2 / unsat-core artifacts from re-mine
```

## Age-target fix (May-14)

The gpt-5 modifier was found to drop the age qualifier (set `target_value=None`)
on 61/176 pairs when the threshold cache held the actual constraint. Fix in
`/tmp/fix_smt_numeric_targets.py` + corrected CFs in
`/tmp/age_corrected_cfs_full.jsonl`. The full aegis rejudge uses the corrected
CFs for these 61 pairs and original CFs for the other 115. See
`age_correction_notes.md` for the affected pair list and Δflip counts.
