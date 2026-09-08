#!/usr/bin/env python3
"""Sanity-check the joint-maxsat fix on the 28 cell-1 not_flipped pairs.

For each pair:
  1. Re-run aegis_blockers (current per-side solver) → record blockers + targets
  2. Re-run aegis_blockers_joint (NEW joint solver) → record blockers + targets
  3. Compare:
     - Are the joint targets internally consistent for co-present atoms?
     - Does the joint result symbolically verify (verify_side: True/True)?
"""
import json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'experiments/counterfactual/utils'))

import cf_dataset as ds
import cf_blockers as bl

charts  = ds.load_charts()
mine    = ds.load_v9_mine()
arbiter = ds.load_arbiter_cache()

# Find the 28 not_flipped pairs from cell 1 output
records_fp = HERE/'out'/'cell1_4.1m_4.1v_filt'/'records.merged.jsonl'
target_pairs = []
for ln in records_fp.open():
    r = json.loads(ln)
    sd = (r.get('systems') or {}).get('aegis')
    if not sd: continue
    if sd.get('error') or sd.get('skipped'): continue
    v = sd.get('cf_validation') or {}
    if not v.get('valid'): continue
    if sd.get('rejudged',{}).get('eligibility') == 'eligible': continue
    target_pairs.append((r['pair'], sd.get('deciding_variant'), sd))

print(f'found {len(target_pairs)} not_flipped pairs from cell 1')
results = []
for pair, deciding, sd in target_pairs:
    vs = mine.get(pair, {})
    if not vs or deciding not in {full_tid for full_tid, _ in vs.values()}:
        print(f'  skip {pair}: no v9 mine for deciding variant')
        continue

    # Resolve full_json for the deciding variant
    full_json = None
    for key, (full_tid, fj) in vs.items():
        if full_tid == deciding:
            full_json = fj; break
    if not full_json:
        # Just pick the first variant
        for key, (full_tid, fj) in vs.items():
            full_json = fj; deciding = full_tid; break

    # Run both solvers
    try:
        old = bl.aegis_blockers(pair, deciding, full_json, arbiter)
        new = bl.aegis_blockers_joint(pair, deciding, full_json, arbiter)
    except Exception as e:
        print(f'  ERR {pair}: {e}')
        continue

    old_targets = {b['atom']: b.get('target_value') for b in (old.get('inc_blockers') or []) + (old.get('exc_blockers') or [])}
    new_targets = {b['atom']: b.get('target_value') for b in (new.get('inc_blockers') or []) + (new.get('exc_blockers') or [])}
    # Detect inconsistency between sides under OLD (same atom, different targets)
    old_inc = {b['atom']: b.get('target_value') for b in (old.get('inc_blockers') or [])}
    old_exc = {b['atom']: b.get('target_value') for b in (old.get('exc_blockers') or [])}
    inconsistent_old = {a: (old_inc[a], old_exc[a]) for a in (set(old_inc) & set(old_exc)) if old_inc[a] != old_exc[a]}

    co_present = new.get('co_present_atoms', [])
    joint_verified = new.get('joint_verified')
    results.append({
        'pair': pair,
        'old_n_blockers': len(old_targets),
        'new_n_blockers': len(new_targets),
        'co_present_count': len(co_present),
        'inconsistent_old_targets': inconsistent_old,
        'joint_verified': joint_verified,
        'new_co_present_atoms': co_present[:5],
    })

# Summary
print()
print('='*100)
print(f'{"pair":40} {"old#":>5} {"new#":>5} {"copre":>6} {"verified":>10} {"inconsistent_old":>20}')
print('-'*100)
ver_t = ver_f = 0; with_overlap = 0; with_inconsistency = 0
for r in results:
    inc_str = ('YES' if r['inconsistent_old_targets'] else '')
    print(f'{r["pair"]:40} {r["old_n_blockers"]:>5} {r["new_n_blockers"]:>5} {r["co_present_count"]:>6} {str(r["joint_verified"]):>10} {inc_str:>20}')
    if r['joint_verified']: ver_t += 1
    else: ver_f += 1
    if r['co_present_count']: with_overlap += 1
    if r['inconsistent_old_targets']: with_inconsistency += 1

print()
print(f'joint_verified: {ver_t} TRUE, {ver_f} FALSE')
print(f'with co-present atoms (overlap): {with_overlap}/{len(results)}')
print(f'with inconsistent OLD targets (same atom, different inc vs exc target): {with_inconsistency}/{len(results)}')

out_fp = HERE/'aegis_failure_analysis'/'joint_maxsat_test_results.json'
out_fp.write_text(json.dumps(results, indent=2, default=str))
print(f'\nwrote {out_fp}')
