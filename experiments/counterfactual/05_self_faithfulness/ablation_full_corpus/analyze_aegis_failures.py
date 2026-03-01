#!/usr/bin/env python3
"""AEGIS failure-case analysis.

A "failure" = aegis record where any of these happened:
  - cf_valid=False (validator rejected the CF)
  - cf_valid=True AND rejudged=ineligible (CF was applied but did not flip the matcher)

For each failure, dump enough context to classify:
  - Atom selection: did maxsat select the right blocker atoms?
  - Value assignment: did sat_values give plausible target values?
  - Modifier interpretation: did the modifier produce a CF that actually
    changes the targeted atoms?
  - Validator over-rejection: is the v3 validator too strict?
"""
import json, pathlib, sys, csv

HERE = pathlib.Path(__file__).resolve().parent
OUT  = HERE/'out'
CELLS = ['cell1_4.1m_4.1v_filt', 'cell2_4.1m_v3v_filt', 'cell3_5m_v3v_filt']

def is_valid(v):
    if 'valid' in v: return bool(v.get('valid'))
    return bool(v.get('coherent') and v.get('flips_target_atom') and v.get('keeps_other_facts'))

def categorize(sd):
    if sd.get('error'): return 'errored'
    if sd.get('skipped'): return 'skipped'
    v = sd.get('cf_validation') or {}
    if not is_valid(v): return 'invalid_cf'
    rej = sd.get('rejudged',{}).get('eligibility','')
    return 'flipped' if rej == 'eligible' else 'not_flipped'

def short(s, n=120):
    s = str(s).strip().replace('\n',' ')
    return s[:n] + ('…' if len(s) > n else '')

def dump_failures(cell_tag, max_per_class=10):
    fp = OUT/cell_tag/'records.merged.jsonl'
    # Build merged-by-pair (handle retry duplicates)
    by_pair = {}
    for ln in fp.open():
        if not ln.strip(): continue
        r = json.loads(ln)
        p = r['pair']
        if p not in by_pair: by_pair[p] = {'pair': p, 'systems': {}}
        for s, sd in (r.get('systems') or {}).items():
            existing = by_pair[p]['systems'].get(s)
            if existing and isinstance(existing, dict) and not existing.get('error'):
                continue
            by_pair[p]['systems'][s] = sd

    classes = {'invalid_cf': [], 'not_flipped': [], 'flipped': []}
    for p, r in by_pair.items():
        sd = (r.get('systems') or {}).get('aegis')
        if not sd: continue
        c = categorize(sd)
        if c in classes: classes[c].append((p, sd))

    print(f'\n{"="*80}\n{cell_tag}: aegis failure breakdown')
    for c, items in classes.items():
        print(f'  {c:14}: {len(items):4}')
    print()

    out_dir = HERE/'aegis_failure_analysis'/cell_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    for c in ('invalid_cf', 'not_flipped'):
        items = classes[c]
        # Save full list
        with (out_dir/f'{c}_full.json').open('w') as f:
            json.dump([{'pair': p,
                        'targets': sd.get('targets', []),
                        'preserve_count': len(sd.get('preserve', [])),
                        'cf_validation': sd.get('cf_validation', {}),
                        'rejudged_elig': sd.get('rejudged',{}).get('eligibility'),
                       } for p, sd in items], f, indent=2)
        # Sample first N for inspection
        sample_dir = out_dir/f'{c}_samples'
        sample_dir.mkdir(exist_ok=True)
        for p, sd in items[:max_per_class]:
            d = sample_dir/p
            d.mkdir(exist_ok=True)
            (d/'targets.json').write_text(json.dumps(sd.get('targets',[]), indent=2))
            (d/'preserve.json').write_text(json.dumps(sd.get('preserve',[]), indent=2))
            (d/'other_atoms.json').write_text(json.dumps(sd.get('other_atoms',[]), indent=2))
            (d/'cf_chart.txt').write_text(sd.get('cf_chart','') or '(none)')
            (d/'cf_validation.json').write_text(json.dumps(sd.get('cf_validation',{}), indent=2))
            rej = sd.get('rejudged', {})
            (d/'rejudged.json').write_text(json.dumps({
                'eligibility': rej.get('eligibility'),
                'inclusion_sat_like': rej.get('inclusion_sat_like'),
                'exclusion_sat_like': rej.get('exclusion_sat_like'),
            }, indent=2))
    print(f'  wrote samples to {out_dir}')

    # Atom selection diagnostic: count target types
    target_kinds = {'numeric': 0, 'qualifier': 0, 'stem': 0, 'unknown': 0}
    null_target_value = 0
    for p, sd in classes['invalid_cf'] + classes['not_flipped'] + classes['flipped']:
        for t in sd.get('targets', []):
            v = t.get('target_value')
            atom = str(t.get('atom',''))
            if v is None: null_target_value += 1
            if any(x in atom.lower() for x in ('age','count','level','value','dose','duration','years')):
                target_kinds['numeric'] += 1
            elif '.' in atom:
                target_kinds['qualifier'] += 1
            elif atom:
                target_kinds['stem'] += 1
            else:
                target_kinds['unknown'] += 1

    print(f'\n  target kinds (all aegis records combined): {target_kinds}')
    print(f'  targets with NULL target_value: {null_target_value}')

for tag in CELLS:
    dump_failures(tag, max_per_class=10)
