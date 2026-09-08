# Reproducing the AEGIS accuracy results

> **See [docs/REPRODUCE_FROM_SCRATCH.md](../../docs/REPRODUCE_FROM_SCRATCH.md) for the
> full pipeline starting from raw inputs (SIGIR + prompts).** This document
> covers the accuracy-only steps in detail.


This document records the full pipeline to regenerate every number in `results.md`.
All scripts are under `experiments/accuracy/scripts/`. All API calls use Azure OpenAI (`OPENAI_ENDPOINT`, `OPENAI_API_KEY`, `OPENAI_ENDPOINT_GPT5` in `.env`).

## Data sources (read-only)

- `experiments/53_v2_full/cmsrc_out/` — v6 mining outputs (chart, atoms-with-evidence, SMT programs per pair × cohort variant)
- `/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT/dataset/clinical_trial/sigir/corpus.jsonl` — canonical SIGIR trial corpus (3621 trials)
- `experiments/03_judge_rubric/prompts/` — 5 judge framings (clinician_v2, clinician_paraphrase, engineering_canonical, mechanical, rhetorical)
- `verbalizer/prompts/verbalize_smt_rationale.prompt` — AEGIS verbalize prompt (evidence-rich)

## Step 1: Compile AEGIS strict-inc verdicts

```
python experiments/accuracy/scripts/compute_aegis_strict_inc.py
# → experiments/accuracy/scripts/aegis_strict_inc.jsonl  (539 verdicts)
```

Strict-inc policy: `eligible iff (inc_sat is True) AND (exc_sat is not False)` — silence on inclusion fails, silence on exclusion is benign. Aggregated as `any-cohort-variant-eligible → eligible`.

## Step 2: Generate baselines

V5 (LM-only TWO_STEP):

```
python experiments/accuracy/scripts/lm_prompt_sweep.py --variant V5_TWO_STEP --workers 10
# → experiments/accuracy/scripts/lm_only_V5_TWO_STEP.jsonl
```

Canonical TG-Matching (Jin/Yang prompt + canonical aggregation):

```
python experiments/accuracy/scripts/run_trialgpt_matching.py --workers 10
# → experiments/accuracy/scripts/trialgpt_matching.jsonl
```

Stanford som-shahlab koopman:

```
python experiments/accuracy/scripts/run_stanford_baseline.py --workers 10
# → experiments/accuracy/scripts/stanford_som_shahlab.jsonl
```

All three baselines pull trial text from full SIGIR corpus (no truncation).

## Step 3: Verbalize AEGIS

```
python experiments/accuracy/scripts/run_aegis_verbalize.py --workers 16
# → experiments/accuracy/scripts/aegis_verbalized.jsonl
```

Uses the headline strict-inc label + multi-variant UNSAT-core selection + chart-quote-required prompt.

## Step 4: Build TG-for-judges (canonical aggregation + per-criterion rationale)

```
python -c "
import json, pathlib
matching = {json.loads(l)['pair']: json.loads(l).get('eligibility') for l in open('experiments/accuracy/scripts/trialgpt_matching.jsonl')}
out = []
for line in open('experiments/accuracy/scripts/trialgpt_corrected.jsonl'):
    r = json.loads(line)
    if r['pair'] in matching:
        out.append({'pair':r['pair'], 'eligibility':matching[r['pair']],
                    'rationale':r.get('rationale',''), 'n_variants':r.get('n_variants')})
pathlib.Path('experiments/accuracy/scripts/trialgpt_for_judges.jsonl').write_text('\n'.join(json.dumps(x) for x in out))
"
```

## Step 5: 5-judge gold ensemble

```
python experiments/accuracy/scripts/run_5judges_verbalized.py --workers 16
# → experiments/accuracy/scripts/judges5_verbalized.jsonl  (2695 records)
python experiments/accuracy/scripts/build_refined_gold_from_5judges.py
# → experiments/accuracy/scripts/gold_refined_5judges_verbalized.json  (~530-pair gold)
```

## Step 6: Meta-fusion (3 modes)

```
python experiments/accuracy/scripts/run_meta_fusion.py --mode balanced  --workers 12
python experiments/accuracy/scripts/run_meta_fusion.py --mode precision --workers 12
python experiments/accuracy/scripts/run_meta_fusion.py --mode recall    --workers 12
# → experiments/accuracy/scripts/meta_fusion_{balanced,precision,recall}.jsonl
```

## Step 7: Bootstrap CIs

```
python experiments/accuracy/scripts/bootstrap_ci.py
# → paper/results/bootstrap_ci.json
```

## Step 9: Independent GPT-5 audit of AEGIS errors

```
python experiments/accuracy/scripts/audit_aegis_FN.py
python experiments/accuracy/scripts/audit_aegis_FP.py
# → paper/results/aegis_FN_audit.jsonl, aegis_FP_audit.jsonl
python experiments/accuracy/scripts/audit_corrected_metrics.py
# → paper/results/audit_corrected_metrics.json
```

## Step 10: Error stratification

```
python experiments/accuracy/scripts/stratify_aegis_errors.py
# → paper/results/aegis_error_stratification.md
```

## Step 11: Inspection folders

```
python experiments/accuracy/scripts/build_aegis_v5_mbench.py
python experiments/accuracy/scripts/build_aegis_hard_failure_inspection.py
# → inspection/aegis_v5_mbench/, inspection/aegis_hard_failures/
```

## Engineering caveats fixed (must be applied for correct numbers)

1. **No truncation**: all baselines and the gold ensemble use full SIGIR corpus text. Earlier truncation caps (1200 inc / 600 exc / 1500 chars) systematically biased the gold and V5/Stanford verdicts. Fixes are in `run_5judges_verbalized.py`, `lm_prompt_sweep.py`, `run_stanford_baseline.py`, `run_trialgpt_matching.py`.
2. **Verbalizer correctness**: `run_aegis_verbalize.py` uses headline strict-inc label (not lenient `inc_sat is not False AND exc_sat is not True` — that was polarity-flipped) and computes multi-variant aggregate (any-eligible → eligible). The verbalize prompt explicitly teaches UNSAT-core direction (inc-side = patient fails inclusion; exc-side = exclusion fires).
3. **TG canonical aggregation**: judges see `trialgpt_for_judges.jsonl` (TG-Matching aggregation, F1=0.804) not `trialgpt_corrected.jsonl` (cmsrc strict, F1=0.395).
4. **8000-token GPT-5 budget**: judges may need to think; `gpt5_call(prompt, max_tokens=8000)` avoids empty responses on long inputs.

## Headline numbers to reproduce

On the original LLM-derived gold (n=531):

| System | F1 | Acc | P | R |
|---|---|---|---|---|
| AEGIS+V5+TG MAJ | 0.906 | 0.900 | 0.920 | 0.891 |
| V5 TWO_STEP | 0.893 | 0.883 | 0.881 | 0.905 |
| AEGIS strict-inc | 0.881 | 0.872 | 0.878 | 0.884 |

On the audit-corrected gold (15 flips):

| System | F1 | Acc | P | R |
|---|---|---|---|---|
| **AEGIS+V5+TG MAJ** | **0.917** | **0.913** | 0.917 | 0.917 |
| AEGIS strict-inc | 0.906 | 0.900 | 0.889 | 0.924 |
| V5 TWO_STEP | 0.879 | 0.870 | 0.853 | 0.906 |

Bootstrap (N=1000) on audit-corrected gold:
- AEGIS+V5+TG MAJ vs V5: ΔF1 = +0.038 [+0.018, +0.059] *** (statistically significant)
