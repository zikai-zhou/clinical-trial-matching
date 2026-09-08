#!/usr/bin/env python3
"""Re-run gpt-5 modifier on age-affected pairs with concrete target ages,
re-judge with aegis, and compare flip-rate impact."""
import json, os, pathlib, re, sys
sys.path.insert(0, '/tmp')
import importlib.util
spec = importlib.util.spec_from_file_location('cf_mod', '/tmp/cf_modifier_gpt5.py')
cf_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(cf_mod)

from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored')

# Load existing CFs + targets
sf_main = {r['pair']: r for r in [json.loads(l) for l in (ROOT/'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl').open() if l.strip()]}

# Compute concrete age targets from threshold cache
def load_thresh(nct):
    base = re.sub(r'[a-z]+$', '', nct)
    p = ROOT/'experiments/53_v2_full/threshold_cache'/f'{base}.json'
    if not p.exists(): return {}
    return json.load(p.open()).get('atoms', {})

def resolve(atom, thresh):
    lo=hi=None; eq=None
    for tk,ta in thresh.items():
        if ta.get('var') != atom: continue
        op,th = ta.get('interpreted_op'), ta.get('interpreted_threshold')
        if op in ('ge','gt'):
            new = th if op=='ge' else th+1
            lo = new if lo is None else max(lo, new)
        elif op in ('le','lt'):
            new = th if op=='le' else th-1
            hi = new if hi is None else min(hi, new)
        elif op=='eq':
            eq = th
    if eq is not None: return eq
    if lo is not None and hi is not None: return int((lo+hi)/2)
    if lo is not None: return lo+1  # buffer above lower bound
    if hi is not None: return hi-1
    return None

# Affected pairs (10 from the audit + their original CF state)
AFFECTED = [
    ('sigir-20144__NCT00305201', 'NCT00305201'),
    ('sigir-20158__NCT00832026', 'NCT00832026'),
    ('sigir-201414__NCT02064959', 'NCT02064959'),
    ('sigir-201415__NCT00288938', 'NCT00288938'),
    ('sigir-201527__NCT01757119', 'NCT01757119'),
    ('sigir-20155__NCT00339157', 'NCT00339157'),
    ('sigir-201429__NCT00000430', 'NCT00000430'),
    ('sigir-201425__NCT00178711', 'NCT00178711'),
    ('sigir-201520__NCT01463475', 'NCT01463475'),
    ('sigir-201428__NCT00386022', 'NCT00386022'),
]

def patch_targets(pair, nct):
    a = (sf_main.get(pair, {}).get('systems') or {}).get('aegis', {})
    targets = [dict(t) for t in a.get('targets', [])]
    thresh = load_thresh(nct)
    patched = 0
    for t in targets:
        cv = t.get('current_value'); tv = t.get('target_value'); atom = t.get('atom','')
        if isinstance(cv,(int,float)) and tv is None and '__THRESH__' not in atom and 'age' in atom.lower():
            new_target = resolve(atom, thresh)
            if new_target is not None:
                t['target_value'] = new_target
                patched += 1
    return targets, patched

FE = pathlib.Path('/Users/xyrus/Desktop/llm-smt/clinical-trial-annotation-frontend/private')
audit = json.load((FE/'clinician_review.json').open())
CHARTS = {f"{t.get('patient_id')}__{t.get('trial_id')}": t.get('original_chart','') for t in audit['topics'] if t.get('sheet')=='cf_rewrite_review'}

def process(item):
    pair, nct = item
    a = (sf_main.get(pair, {}).get('systems') or {}).get('aegis', {})
    chart = CHARTS.get(pair, '')
    if not chart:
        return pair, None, 'no chart'
    targets, n_patch = patch_targets(pair, nct)
    if n_patch == 0:
        return pair, None, 'no age fix needed'
    try:
        new_cf = cf_mod.generate_cf_gpt5_targets(chart, targets, preserve=a.get('preserve'), other_atoms=a.get('other_atoms'))
    except Exception as e:
        return pair, None, str(e)[:120]
    return pair, new_cf, f'patched {n_patch} target(s)'

print(f'Re-running modifier on {len(AFFECTED)} age-affected pairs...')
results = {}
with ThreadPoolExecutor(max_workers=5) as ex:
    for fut in as_completed({ex.submit(process, it):it for it in AFFECTED}):
        pair, cf, status = fut.result()
        results[pair] = cf
        print(f'  {pair}: {status} [{(len(cf) if cf else 0)} chars]')

# Save new CFs
out = pathlib.Path('/tmp/age_fixed_cfs.jsonl')
with out.open('w') as f:
    for pair, cf in results.items():
        if cf:
            f.write(json.dumps({'pair':pair, 'cf_chart':cf}) + '\n')
print(f'\nwrote {sum(1 for v in results.values() if v)} new CFs -> {out}')
