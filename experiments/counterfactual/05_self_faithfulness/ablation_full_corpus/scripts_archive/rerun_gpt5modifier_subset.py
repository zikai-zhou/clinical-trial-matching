#!/usr/bin/env python3
"""Re-run the ORIGINAL gpt-4.1 pipeline on a 25% subset to test reproducibility.

Uses the exact same functions as run.py — no methodology change. Writes to
self_faithfulness_gpt5modifier_origvalidator_subset.jsonl so we can compare against the cached
published values without disturbing them.
"""
import json, os, pathlib, sys
HERE = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')
sys.path.insert(0, str(HERE/'experiments/counterfactual/utils'))
sys.path.insert(0, str(HERE/'experiments/counterfactual/05_self_faithfulness'))
from concurrent.futures import ThreadPoolExecutor, as_completed
import cf_dataset as ds
import run as orig

PAIRS = [p.strip() for p in open('/tmp/repro_subset.txt') if p.strip()]
SYSTEMS = ['aegis','v5','tg','shah']
# SLICE env var: "X/N" — this process handles pairs[X::N] (zero-indexed).
slice_arg = os.environ.get('SLICE','0/1')
slice_idx, slice_n = map(int, slice_arg.split('/'))
PAIRS = PAIRS[slice_idx::slice_n]
SLICE_TAG = slice_arg.replace('/','of')
OUT = HERE/f'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_gpt5modifier_origvalidator_subset.{SLICE_TAG}.jsonl'

if not (os.environ.get('OPENAI_ENDPOINT') and os.environ.get('OPENAI_API_KEY')):
    sys.exit('OPENAI_ENDPOINT + OPENAI_API_KEY required')

print(f'subset: {len(PAIRS)} pairs × {len(SYSTEMS)} systems')
charts = ds.load_charts(); trials = ds.load_trial_text()
aegis = ds.load_aegis_v9_arbiter(); v5 = ds.load_v5()
v5b = ds.load_v5_blockers(); tg = ds.load_tg(); shah = ds.load_shahlab()
mine = ds.load_v9_mine(); arb = ds.load_arbiter_cache()

cache = set()
if OUT.exists():
    for ln in OUT.open():
        try: cache.add(json.loads(ln)['pair'])
        except: pass
todo = [p for p in PAIRS if p not in cache]
print(f'cached: {len(cache)}  todo: {len(todo)}')

print(f'slice {SLICE_TAG}: {len(todo)} pairs (synchronous)', flush=True)
with OUT.open('a') as f:
    for n, p in enumerate(todo, 1):
        try:
            rec = orig.process_pair(p, SYSTEMS, charts, trials,
                                    mine, arb, aegis, v5, tg, shah, v5b)
        except Exception as e:
            print(f'  [{SLICE_TAG}][ERR] {p}: {type(e).__name__}: {str(e)[:200]}', flush=True); continue
        f.write(json.dumps(rec)+'\n'); f.flush()
        sys_flips = {s: int((rec.get('systems',{}).get(s,{}) or {}).get('flipped',False)) for s in SYSTEMS}
        print(f'  [{SLICE_TAG}][{n:2}/{len(todo)}] {p}  flips={sys_flips}', flush=True)

# Summary
print('\n=== subset reproduction (gpt-4.1 modifier + gpt-4.1 validator) ===')
recs = [json.loads(l) for l in OUT.open() if l.strip()]
for s in SYSTEMS:
    n_total = 0; n_flipped = 0; n_valid = 0; n_invalid = 0
    for r in recs:
        sd = r.get('systems',{}).get(s) or {}
        if sd.get('skipped'): continue
        n_total += 1
        if sd.get('cf_valid'): n_valid += 1
        else: n_invalid += 1
        if sd.get('flipped') and sd.get('cf_valid'): n_flipped += 1
    rate = (100*n_flipped/n_valid) if n_valid else 0
    print(f'  {s:8} total={n_total:3}  valid={n_valid:3}  flipped={n_flipped:3}  rate={rate:5.1f}%')
print(f'\noutput: {OUT}')
