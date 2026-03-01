# Plan: Fix AEGIS Counterfactual Exclusion Blowback via Joint MaxSat

## Failure-mode breakdown (cell 1, gpt-4.1 modifier + gpt-4.1 validator)

| outcome | count | meaning |
|---|---|---|
| flipped (valid + rejudged eligible) | 123 | success — self-faithful |
| not_flipped (valid + rejudged ineligible) | **28** | atom-selection failure |
| invalid_cf (validator rejected) | 134 | modifier failure |
| flipped/valid rate | **81.5%** | |

Of the 28 not_flipped cases, the inc/exc rejudge breakdown is:

| inc_sat | exc_sat | count | %  | interpretation |
|---|---|---|---|---|
| True  | False | **18** | 64% | **Exclusion blowback** — fixed inclusion, accidentally triggered exclusion |
| False | True  | 7  | 25% | Inclusion still not fully flipped (modifier missed an edit) |
| False | False | 3  | 11% | Both still blocked |

Exclusion blowback is the dominant cause and is **fixable in maxsat formulation** without changing the modifier.

## Concrete example
- pair: sigir-201513 / NCT01978288
- original: "5-year-old boy" (age = 1825 days)
- maxsat target: `patient_age_value_recorded_now_in_days = 7.0`
- modifier wrote: "7-day-old boy"
- validator approved (valid=True)
- rejudged: inc_sat=True, **exc_sat=False** → exclusion triggered (likely "age < 18 yr" exclusion fired)

## Proposed Fix: Joint MaxSat

(plan retained from Plan agent — see below)

### Step 1: Add `maxsat_blockers_joint()` in `cf_maxsat.py`
Concatenate inc + exc programs into one Z3 Optimize. Namespace-disambiguate
declarations only if collisions exist (most patient atoms like
`patient_age_value_recorded_now_in_days` ARE shared by design — that's the
whole point of jointness).

### Step 2: Build unified soft-constraint set
Merge inc_av and exc_av keyed by atom. For atoms present on both sides, emit
a SINGLE soft constraint per atom keeping current value. Forces optimizer to
pick ONE consistent target.

### Step 3: Hard-assert both sides' top predicates
Hard constraint is `(inc_top AND exc_top)`. Existing programs already end
with `(assert top_*)` — keep both.

### Step 4: Extract per-side dropped sets from joint model
Tag whether atom originated from inc_av, exc_av, or both. `sat_values` keyed
by atom, globally consistent.

### Step 5: Rewrite `aegis_blockers` to call the joint solver
Atoms present on both sides land in BOTH `inc_blockers` and `exc_blockers`
with identical `target_value`.

### Step 6: Add `co_present` annotation
Per-blocker `co_present: bool`. GPT modifier prompt can flag highest-risk
rewrites. Also expose `out['co_present_atoms']`.

### Step 7: Symbolic post-solve verification
Plug model's target_values back into BOTH inc_prog_lines and exc_prog_lines
as hard constraints; run two independent `z3.Solver().check()` calls. If
either is not SAT, mark `joint_verified=False`.

### Step 8: Escalation path on verification failure
Fall back: increase soft-weight on co-present atoms; re-solve. If still
failing, fall back to current per-side behavior with `joint_failed=True`.

### Step 9: Backward compatibility
`use_joint_maxsat: bool = True` param. Default True for new runs.

### Step 10: Apply parent-stem expansion AFTER joint solve
`_expand_with_parent_stems` runs on the union so qualifier→stem propagation
uses the joint target value consistently.

## Expected impact

Eliminating the 18 exclusion-blowback failures would lift AEGIS from
123/151 = 81.5% to **141/151 = 93.4%** in cell 1.

The remaining 10 cases (7 inc-not-flipped + 3 both-not-flipped) are modifier
or atom-coverage issues that need separate analysis.

## Files to modify
- experiments/counterfactual/utils/cf_maxsat.py
- experiments/counterfactual/utils/cf_blockers.py
- experiments/counterfactual/utils/cf_generator.py (modifier prompt — consume `co_present`)
- experiments/counterfactual/utils/cf_validator.py (joint-flip verification)
