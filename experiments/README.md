# Experiments

Research code for the paper. **Self-contained and independent of the tool**:
nothing here is imported by `verdict`, `satir`, `pipeline`, or either CLI, and
nothing here is needed to install or run them.

Run these directly, from the repository root:

```bash
python experiments/accuracy/scripts/<script>.py
bash   experiments/counterfactual/05_self_faithfulness/ablation_full_corpus/launch_all.sh
```

## What is here

| | |
|---|---|
| `accuracy/` | accuracy, F1, and inspection over the 552-pair set |
| `counterfactual/` | self-faithfulness: modifier, validator, reverse-CF, ablations |
| `clinician_validation/` | audit instrument, sampling frames, re-review |
| `03_judge_rubric/`, `02_pairwise_preference/` | judge prompts and preference runs |
| `typed_policy/`, `12_policy_invariance/` | policy alignment and invariance |
| numbered SatIR dirs | retrieval-side experiments |

## Inputs these expect

Most read large artifacts that are **not** in the repository — mined pair
outputs, judge outputs, caches. Those live outside git (see
[../docs/DATA.md](../docs/DATA.md)) and several scripts take a `--root` or read
`$VERDICT_ROOT`. A script that cannot find its inputs will say so.

## Two cautions

**Some scripts write in place.** Before running one against artifacts you care
about, copy the tree — at least one script in this family writes its output
over its own stored record. Prefer a scratch copy.

**These are research scripts, not library code.** They are kept for the record,
including dead ends. They are not held to the standards applied to the tool:
no tests, no stability guarantee, and some reference paths from the machines
they were written on. The scripts that produced numbers in the paper are
listed in [../docs/REPRODUCE_TABLES.md](../docs/REPRODUCE_TABLES.md).
