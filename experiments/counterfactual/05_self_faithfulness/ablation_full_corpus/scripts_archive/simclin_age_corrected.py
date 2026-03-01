#!/usr/bin/env python3
"""Re-score the 61 age-corrected CFs with the same simclin prompt to recompute
the headline 'flip | valid' metric (currently 90.51% on old CFs)."""
import json, os, pathlib, re, urllib.request, sys
sys.path.insert(0, '<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored/experiments/counterfactual/05_self_faithfulness/validation')
import run_simclin_unified as sc
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')

# Load existing simclin results to know which pairs already have scores
gpt5mod = {r['pair']:r for r in [json.loads(l) for l in (ROOT/'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_smt_gpt5modifier.jsonl').open() if l.strip()]}

# Load age-corrected CFs
new_cfs = {}
for ln in open('/tmp/age_corrected_cfs_full.jsonl'):
    o = json.loads(ln); new_cfs[o['pair']] = o['cf_chart']

# Load charts + trials
charts = sc.load_charts()
trials = sc.load_trials()

# Load old SMT info to get the cited blocker text per pair
sf = {r['pair']:r for r in [json.loads(l) for l in (ROOT/'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl').open() if l.strip()]}

PROMPT = sc.PROMPT
def work(pair):
    new_cf = new_cfs[pair]
    pid, parent = pair.split('__', 1)
    chart = charts.get(pid, '')
    inc, exc = trials.get(parent, ('',''))
    info = sf.get(pair, {}).get('systems',{}).get('aegis', {})
    cited = sc.cited_for('aegis', info, pair)
    prompt = PROMPT.format(
        original_chart=chart[:3500],
        cf_chart=new_cf[:3500],
        inclusion=inc[:2500],
        exclusion=exc[:2000],
        cited_block=cited,
        original_verdict='ineligible',
    )
    resp = sc.llm_json(prompt, 'gpt-5')
    if not resp or 'error' in resp:
        return {'pair': pair, 'status':'error'}
    return {
        'pair': pair,
        'clin_coherent': resp.get('coherent'),
        'clin_flips_target_atom': resp.get('flips_target_atom'),
        'clin_keeps_other_facts': resp.get('keeps_other_facts'),
        'clin_oracle_should_flip': resp.get('oracle_should_flip'),
        'clin_explanation': (resp.get('explanation') or '')[:200],
        'status':'ok',
    }

out_path = pathlib.Path('/tmp/simclin_age_corrected.jsonl')
existing = {}
if out_path.exists():
    for ln in out_path.open():
        try: o=json.loads(ln); existing[o['pair']]=o
        except: pass

to_do = [p for p in new_cfs if p not in existing]
print(f'scoring {len(to_do)} new + {len(existing)} cached = {len(new_cfs)} total')

with out_path.open('a') as f, ThreadPoolExecutor(max_workers=8) as pool:
    futs = {pool.submit(work, p): p for p in to_do}
    n = 0
    for fut in as_completed(futs):
        r = fut.result()
        f.write(json.dumps(r)+'\n'); f.flush()
        n += 1
        if n % 5 == 0: print(f'  {n}/{len(to_do)}', flush=True)

# Reload and compute
results = []
for ln in out_path.open():
    try: results.append(json.loads(ln))
    except: pass

# Old (full corpus): n=176, flip|valid = 90.51%
# New age-corrected subset: compute flip|valid for the 61 + carry old for rest
def is_valid(r):
    return r.get('clin_coherent') is True and r.get('clin_keeps_other_facts') is True
def is_flip(r):
    return r.get('clin_coherent') is True and r.get('clin_flips_target_atom') is True and r.get('clin_keeps_other_facts') is True

new_results = {r['pair']: r for r in results}

# Compute combined corpus: replace gpt5mod entries for the 61 affected pairs
combined = []
for pair, oldr in gpt5mod.items():
    if pair in new_results:
        # use new
        new = new_results[pair]
        combined.append({'pair':pair,
                         'simclin_coherent': new.get('clin_coherent'),
                         'simclin_flips_cited': new.get('clin_flips_target_atom'),
                         'simclin_keeps_other': new.get('clin_keeps_other_facts'),})
    else:
        combined.append({'pair':pair,
                         'simclin_coherent': oldr.get('simclin_coherent'),
                         'simclin_flips_cited': oldr.get('simclin_flips_cited'),
                         'simclin_keeps_other': oldr.get('simclin_keeps_other'),})

def all3(r):
    return r['simclin_coherent'] is True and r['simclin_flips_cited'] is True and r['simclin_keeps_other'] is True
def valid2(r):
    return r['simclin_coherent'] is True and r['simclin_keeps_other'] is True

valid_count = sum(1 for r in combined if valid2(r))
flip_count = sum(1 for r in combined if all3(r))
print(f'\n=== Corpus-wide simclin metric (age-corrected) ===')
print(f'  total pairs:                {len(combined)}')
print(f'  valid mods:                 {valid_count}')
print(f'  flipped & valid:            {flip_count}')
print(f'  flip rate | valid:          {100*flip_count/max(1,valid_count):.2f}%')
# For comparison: original 90.51% on 158 valid out of 176
print(f'\n  PRIOR (buggy CFs):          143/158 = 90.51%')
print(f'  Δ: {100*flip_count/max(1,valid_count) - 90.51:+.2f}pp')
