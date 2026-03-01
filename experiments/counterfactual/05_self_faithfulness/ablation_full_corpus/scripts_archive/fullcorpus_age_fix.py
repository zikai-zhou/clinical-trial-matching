#!/usr/bin/env python3
"""Corpus-wide fix: re-run gpt-5 SMT modifier on all 61 age-affected pairs
with concrete in-range age targets, then re-judge with AEGIS.

Stages:
  1. Identify affected pairs (currently 61/178)
  2. Patch targets (set age target to in-range concrete value)
  3. Re-run modifier → new cf_chart
  4. Re-judge with AEGIS → new flip status
  5. Emit corrected self_faithfulness_corrected.jsonl

Output: experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_age_corrected.jsonl
"""
import json, os, pathlib, re, sys
sys.path.insert(0, '/tmp')
import importlib.util
spec = importlib.util.spec_from_file_location('cf_mod', '/tmp/cf_modifier_gpt5.py')
cf_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(cf_mod)
sys.path.insert(0, '<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored/experiments/counterfactual')
from utils import cf_judge as jg
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')

# Load existing sf + chart corpus
sf = {r['pair']:r for r in [json.loads(l) for l in (ROOT/'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl').open() if l.strip()]}
CHARTS = {}
for ln in (ROOT/'dataset/clinical_trial/sigir/queries.jsonl').open():
    try: o=json.loads(ln); CHARTS[o['_id']]=o.get('text','')
    except: continue

def load_thresh(nct):
    base = re.sub(r'[a-z]+$', '', nct)
    p = ROOT/'experiments/53_v2_full/threshold_cache'/f'{base}.json'
    if not p.exists(): return {}
    return json.load(p.open()).get('atoms', {})

def resolve_age(atom, thresh):
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
        elif op=='eq': eq=th
    if eq is not None: return eq
    if lo is not None and hi is not None: return int((lo+hi)/2)
    if lo is not None: return lo+1
    if hi is not None: return hi-1
    return None

# Identify affected pairs
affected = []
for pair, r in sf.items():
    nct = pair.split('__')[1]
    a = r.get('systems',{}).get('aegis',{})
    thresh = load_thresh(nct)
    age_target_idx = None
    new_age_target = None
    for i, tg in enumerate(a.get('targets',[])):
        cv=tg.get('current_value'); tv=tg.get('target_value'); atom=tg.get('atom','')
        if isinstance(cv,(int,float)) and tv is None and '__THRESH__' not in atom and 'age' in atom.lower():
            new_age = resolve_age(atom, thresh)
            if new_age is not None:
                age_target_idx = i; new_age_target = new_age
                break
    if age_target_idx is not None:
        affected.append((pair, age_target_idx, new_age_target))

print(f'{len(affected)} pairs to re-run')

# ---- Stage 1: regenerate CFs ----
new_cfs_path = pathlib.Path('/tmp/age_corrected_cfs_full.jsonl')
existing = {}
if new_cfs_path.exists():
    for ln in new_cfs_path.open():
        try: o=json.loads(ln); existing[o['pair']]=o
        except: continue
print(f'  cached: {len(existing)} CFs')

def regen(item):
    pair, idx, new_age = item
    if pair in existing: return pair, existing[pair]['cf_chart'], 'cached'
    a = sf[pair].get('systems',{}).get('aegis',{})
    targets = [dict(t) for t in a.get('targets',[])]
    targets[idx]['target_value'] = new_age
    pid = pair.split('__')[0]
    chart = CHARTS.get(pid,'')
    if not chart: return pair, None, 'no chart'
    try:
        cf = cf_mod.generate_cf_gpt5_targets(chart, targets, preserve=a.get('preserve'), other_atoms=a.get('other_atoms'))
        return pair, cf, 'ok'
    except Exception as e:
        return pair, None, f'err: {str(e)[:80]}'

print('\n=== Regenerating CFs ===')
to_do = [a for a in affected if a[0] not in existing]
print(f'  {len(to_do)} new + {len(existing)} cached = {len(affected)} total')
done = 0
with new_cfs_path.open('a') as fout:
    with ThreadPoolExecutor(max_workers=8) as ex:
        for fut in as_completed({ex.submit(regen, it): it for it in to_do}):
            pair, cf, status = fut.result()
            if cf: fout.write(json.dumps({'pair':pair,'cf_chart':cf})+'\n'); fout.flush()
            done += 1
            if done % 5 == 0: print(f'  {done}/{len(to_do)} {status}', flush=True)

# Reload all CFs
all_cfs = {}
for ln in new_cfs_path.open():
    try: o=json.loads(ln); all_cfs[o['pair']]=o['cf_chart']
    except: continue
print(f'\nTotal new CFs: {len(all_cfs)}/{len(affected)}')
