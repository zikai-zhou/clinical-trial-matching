# Which matcher is which

Three different things in this repository decide patient--trial eligibility.
They are not interchangeable, and only one produces the paper's numbers.

## 1. `verdict/headline.py` — the paper's system

**This is VERDICT as published.** Vendored from `overnight/repro_headline.py`;
only the I/O paths were changed.

```bash
verdict headline          # or: python verdict/headline.py
```

It is not a bare SMT matcher. It is v6 mined atoms plus three doctrine layers:

- **silence-null, both sides** — drop FALSE-on-silence on inclusion *and*
  exclusion
- **58 compiled patches** — `NULL -> FALSE` coercion on patched atoms only,
  with `(assert atom=true)` injected
- **population gate** — 65 pairs the LM gate declines to forward

Gold is the majority of 5 LM judges (>=3/5). Inputs ship under
`data/headline/` (21 MB); the v6 artifact is reduced to the four fields the
script reads and gives results identical to the full 270 MB originals.

Output, on the shipped artifacts:

| row | F1 | P | R |
|---|---|---|---|
| AEGIS+silence-null | 0.835 | 0.769 | 0.915 |
| **AEGIS default (compiled)** | **0.873** | 0.884 | 0.862 |
| AEGIS opt-in (∪V5) | 0.868 | 0.784 | 0.972 |
| AEGIS+verdict-gate | 0.873 | 0.901 | 0.846 |
| V5 LM-only | 0.875 | 0.803 | 0.960 |

**Known discrepancy.** The paper reports **0.863 / 0.837 / 0.891** for the
default row; this run gives **0.873 / 0.884 / 0.862**. Two of the five rows
(silence-null, verdict-gate) reproduce bit-exactly, three shift by 1--2 F1
points. Script and inputs all predate the paper's stored result, so the cause
is environmental -- most likely the solver version, since which satisfying
assignment Z3 returns can change with it. Every qualitative claim is
unaffected: AEGIS ties V5 on F1, AEGIS has higher precision, V5 higher recall.
Unresolved; do not present the numbers as bit-reproducible.

## 2. `matchers/variants.py` — a reimplementation, not the paper

Six variants (`verdict`, `smt-only`, `atoms`, `lm-only`, `hybrid`,
`trialgpt`) behind `verdict match` and `verdict.match()`. Useful for
inspecting one pair and its audit trail, and for comparing approaches through
the registry.

**It does not reproduce the paper.** Measured against the paper's own
`verdicts.jsonl`, the best variant agrees on **88.7%** of pairs. It reads a
different mining snapshot (`53_v2_full`, April) and has none of the three
doctrine layers above -- so, structurally, it can never overturn an
SMT-accept, which is where a third of the disagreements come from.

## 3. `smt_matcher/` — SatIR's matcher

The retrieval-side matcher, 630 lines. Distinct from `cmsrc`'s 1,812-line
matcher, which produced the mined atoms. Used by the SatIR pipeline, not by
the paper's headline.

## The mined snapshots

`experiments/53_v2_full/cmsrc_out*` are mining runs for the distillation and
counterfactual work. The headline uses `53h_v6_full`, a different run. Reading
the wrong one costs ~5 points of agreement, and nothing warns you.

## What is reproducible today

| | |
|---|---|
| Headline table, structure and counts (856 / 553 / 539 / 58 / 65) | yes, `verdict headline` |
| Headline numbers, exactly as printed in the paper | **no** -- see the discrepancy above |
| The paper's per-pair verdicts | yes, they ship as `matchers/systems/aegis/verdicts.jsonl` (F1 0.861 vs the paper's 0.863) |
| Re-running the SMT matcher from scratch | needs `cmsrc` and Azure credentials |
