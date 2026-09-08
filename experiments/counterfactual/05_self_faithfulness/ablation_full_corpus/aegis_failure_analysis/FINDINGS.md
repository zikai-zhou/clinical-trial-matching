# AEGIS Self-Faithfulness: Failure Analysis & Improvement

## Baseline (cell 1: gpt-4.1 modifier + gpt-4.1 validator + blocker-filtered rationale)

AEGIS rate: **123 / 151 = 81.5%**
Of the 285 ineligible pairs:
- 134 invalid_cf (validator rejected the CF)
- 28 not_flipped (CF valid but matcher still ineligible)
- 123 flipped (success)

## Failure decomposition of the 28 not_flipped pairs

Reinspecting `rejudged.inc_sat_like` × `rejudged.exc_sat_like`:

| inc_sat | exc_sat | count | %  | interpretation |
|---|---|---|---|---|
| True  | False | **18** | 64% | **Exclusion blowback** — fixed inclusion, accidentally triggered exclusion |
| False | True  | 7  | 25% | Inclusion still not fully flipped (modifier missed an edit) |
| False | False | 3  | 11% | Both still blocked |

## Concrete example: sigir-201513 / NCT01978288
- Original chart: "5-year-old boy" (age = 1825 days)
- maxsat target: `patient_age_value_recorded_now_in_days = 7.0` (satisfies "≥ 7 days old")
- Modifier wrote: "7-day-old boy"
- Validator approved: valid=True, all_flipped=True
- Rejudged: inc_sat=True, **exc_sat=False** → exclusion triggered (likely "age < 18 yr" exclusion)

## Root cause

Existing `aegis_blockers` runs `maxsat_blockers` independently per side. The inclusion-side solve picks target values to make inclusion SAT, but does NOT account for whether those values trigger exclusion criteria. For atoms shared across sides (e.g., `patient_age_value_recorded_now_in_days`), this can cause "exclusion blowback".

## Fix: Joint MaxSat

Introduced `cf_maxsat.maxsat_blockers_joint(inc_lines, exc_lines, inc_av, exc_av)` and `cf_blockers.aegis_blockers_joint(...)`. Implementation:

1. **Merge programs**: concatenate inc + exc programs, dedup variable declarations, namespace `:named` labels with `I_`/`E_` prefixes to avoid Z3 "named expression already defined" errors.
2. **Unified soft constraints**: one soft per atom (keyed by atom name); atoms in both sides emit a single soft.
3. **Pre-check & full-check**: joint program SAT? joint + all softs SAT?
4. **Z3 Optimize** with weight-1 softs → minimum-cardinality flip set.
5. **Symbolic verification**: plug satisfying-values back into each side independently; both must check SAT.

## Targeted validation on the 28 failed pairs

Generated joint-maxsat blockers + ran full CF pipeline (gpt-4.1 modifier + gpt-4.1 validator + cmsrc rejudge) on the 28 not_flipped pairs:

| metric | value |
|---|---|
| valid CF under joint | 22/28 |
| flipped (eligible after rejudge) | **13/22 = 59.1%** |
| 6 cases became invalid_cf | the joint targets were harder for the modifier to encode |

**Cell-1 aegis lift (additive): 123 → 136 flipped / 151 valid = 90.1%**
**(With new-validator strictness, deducting 6 newly invalid: 136/145 = 93.8%)**

## Symbolic verification stats

Of 28 failed pairs, **17 verified under joint maxsat** (target values plug back into both sides SAT independently). The other 11 failed verification:
- Most have no co-present atoms — the failure mode is chart-level (modifier introduces NEW exclusion-triggering content during rewrite), which joint maxsat alone can't fix.

## Open follow-ups

1. **Modifier prompt hardening**: instruct modifier NOT to introduce new clinical content beyond targeted edits. Tag co_present atoms as "highest risk: pick a value range that doesn't trigger common exclusions."
2. **Forward-simulated re-mine**: before validating, re-extract patient_var_values from CF chart, and check the new av-set doesn't trigger any exclusion clause.
3. **Stem-to-qualifier propagation** (Approach 2 from earlier session): for a flipped stem atom, force dependent qualifier atoms to also flip; this addresses one class of inc_F failures.

## Files modified

- `experiments/counterfactual/utils/cf_maxsat.py` — added `maxsat_blockers_joint`, `_merge_programs`
- `experiments/counterfactual/utils/cf_blockers.py` — added `aegis_blockers_joint`
- `experiments/counterfactual/05_self_faithfulness/ablation_full_corpus/run_full_ablation.py` — added `AEGIS_JOINT=1` env flag and `OUT_SUFFIX` env flag
- `experiments/counterfactual/05_self_faithfulness/ablation_full_corpus/test_joint_maxsat.py` — sanity test
- `experiments/counterfactual/05_self_faithfulness/ablation_full_corpus/rerun_28_with_joint.py` — end-to-end validation on 28 pairs

## Full-corpus re-run with joint maxsat

### First run had a Z3 bool-cast bug

`decls_by_name.get(atom) or decls_by_name.get(quote_smt(atom).strip('|'))` —
the `or` triggers `__bool__` on a Z3 FuncDeclRef, which raises `Z3Exception:
Symbolic expressions cannot be cast to concrete Boolean values`. Caught by my
outer try/except, this silently emptied `sat_values`, so every numeric target
fell back to `None` (silence) and the modifier had nothing concrete to encode.

After fixing (split into two `if decl is None: ...` checks), the comparison is:

### Bug-free numbers

| cell | per-side | joint v2 | delta |
|---|---|---|---|
| cell1 (4.1m+4.1v) | 123/151 = 81.5% | 128/159 = 80.5% | **−1.0 pp** |
| cell2 (4.1m+v3v)  | 145/179 = 81.0% | 145/175 = **82.9%** | **+1.9 pp** ✓ |
| cell3 (5m+v3v)    | 166/199 = 83.4% | 152/189 = 80.4% | **−3.0 pp** |

**Mixed result.** Joint maxsat wins under (gpt-4.1 modifier + v3 simclin
validator), loses (mildly) under other configurations. The targeted 28-pair test
showed gains (13 newly flipped), but those gains are washed out by regressions
on previously-working cases.

### Transition matrix (cell 1, 290 shared pairs, joint v2 BUG-FIXED)

```
orig \ joint   |  flipped  not_flipped  invalid_cf  skipped
---------------+-----------------------------------------
flipped        |   102        10           11         0
not_flipped    |     7        16            5         0
invalid_cf     |    19         5          109         1     ← 19 new wins!
skipped        |     0         0            0         5
```

Net: 19+7 = **+26 new wins**, 10+11 = **−21 broken wins** → **+5 absolute flipped**.

Cell 3 has similar transitions: +22 new wins, −33 broken (net −11). Joint
maxsat is net-positive on cells 1 and 2 in absolute flips, but the
denominator change (more invalid_cf) drags the rate slightly negative on
cells 1 and 3.

### Why the rate is roughly a wash even with bug fixed

Even with correct sat_values, joint maxsat tradeoffs:
- +25 to +30 absolute wins (cells 1, 3) — new flips from previously-failing pairs
- −20 to −33 absolute losses — joint constraints create harder-to-encode CFs
- Rate change is small because gains and losses roughly cancel

### Real fix lies elsewhere

The 64% exclusion-blowback failure mode is **chart-level, not SMT-level**:
when the modifier rewrites "5-year-old boy" to "7-day-old boy", the new chart
text causes the matcher's LLM to extract NEW exclusion atoms (e.g.,
`patient_is_neonate`) that weren't in the original SMT program at all. Joint
maxsat can't see these post-rewrite atoms.

The right next attempts (NOT yet tried):

1. **Forward-simulated re-mine**: after the modifier produces a CF, re-run the
   patient_var_values extractor on the CF chart, and check whether the new
   av-set fires any exclusion clauses. If yes, send the CF back for revision.

2. **Stricter modifier prompt**: tell the modifier explicitly "do not
   introduce new clinical concepts beyond the targeted edits; rephrase
   minimally."

3. **Stem-to-qualifier propagation** (Approach 2): for flipped stem atoms,
   propagate to dependent qualifiers. May fix some of the inc_F_exc_T cases.

## Decision

Keep `aegis_blockers_joint` available behind `AEGIS_JOINT=1` flag for future
research, but **default remains per-side `aegis_blockers`**. The 81–83% rate
across all 3 cells is reported as the headline number.
