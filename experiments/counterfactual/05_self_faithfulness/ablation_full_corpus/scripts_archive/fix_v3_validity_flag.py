#!/usr/bin/env python3
"""Recompute cf_valid_under_v3_validator with the correct criterion:
   coherent AND flips_target_atom AND keeps_other_facts
(was previously missing flips_target_atom — apples-to-apples requires it
to match gpt-4.1 validator's 'all_flipped AND no_overcorrection')."""
import json, pathlib

SF = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored/experiments/counterfactual/05_self_faithfulness/out')

for filename in ('self_faithfulness_repro_subset_with_v3_validator.jsonl',
                 'self_faithfulness_Dfull_5mod_v3val_fullrationale.jsonl',
                 'self_faithfulness_D_5mod_v3val_filteredrationale.jsonl'):
    fp = SF / filename
    if not fp.exists():
        print(f'  skip {filename} (not present yet)'); continue
    recs = [json.loads(l) for l in fp.open() if l.strip()]
    n_fixed = 0
    for r in recs:
        for system, sd in (r.get('systems') or {}).items():
            if not sd: continue
            v = sd.get('v3_validator') or {}
            if 'coherent' not in v: continue
            new_valid = (v.get('coherent') is True
                         and v.get('flips_target_atom') is True
                         and v.get('keeps_other_facts') is True)
            if sd.get('cf_valid_under_v3_validator') != new_valid:
                n_fixed += 1
                sd['cf_valid_under_v3_validator'] = new_valid
                sd['flipped_under_v3_validator'] = new_valid and sd.get('flipped', False)
    with fp.open('w') as f:
        for r in recs: f.write(json.dumps(r)+'\n')
    print(f'  {filename}: corrected {n_fixed} cells')
