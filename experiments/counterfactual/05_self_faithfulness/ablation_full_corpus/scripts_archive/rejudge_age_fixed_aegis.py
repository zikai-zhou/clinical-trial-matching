#!/usr/bin/env python3
"""Re-judge age-fixed CFs with the full AEGIS pipeline; report new flip rate."""
import json, os, pathlib, sys
sys.path.insert(0, '/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored/experiments/counterfactual')
from utils import cf_judge as jg
ROOT = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored')
sf = {r['pair']:r for r in [json.loads(l) for l in (ROOT/'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl').open() if l.strip()]}
new_cfs = {}
for ln in open('/tmp/age_fixed_cfs.jsonl'):
    o=json.loads(ln); new_cfs[o['pair']]=o['cf_chart']

# Use same prompts as production
PR_ROOT = ROOT/'matchers/systems/aegis/cmsrc/prompts'
PR_MAP  = ROOT/'matchers/systems/aegis/cmsrc/prompt_map.json'

results = []
from concurrent.futures import ThreadPoolExecutor, as_completed
def proc(pair):
    a = sf[pair]['systems']['aegis']
    deciding_variant = a.get('deciding_variant')
    cf = new_cfs[pair]
    try:
        out = jg.judge_aegis(pair, deciding_variant, cf, PR_ROOT, PR_MAP)
    except Exception as e:
        out = {'eligibility':'error','rationale':str(e)[:200]}
    old_flip = a.get('flipped_end_to_end')
    new_elig = out.get('eligibility')
    new_flip = (new_elig == 'eligible')  # original was ineligible by construction
    return pair, old_flip, new_flip, new_elig, out.get('rationale','')[:150]

with ThreadPoolExecutor(max_workers=3) as ex:
    for fut in as_completed({ex.submit(proc, p): p for p in new_cfs}):
        r = fut.result()
        results.append(r)
        print(f'  {r[0]:42}  old_flip={r[1]!s:5}  new_flip={r[2]!s:5}  new_elig={r[3]}', flush=True)

print('\n=== Summary ===')
old_flips = sum(1 for r in results if r[1])
new_flips = sum(1 for r in results if r[2])
print(f'Old flip rate (10 pairs): {old_flips}/10 = {old_flips/10*100:.0f}%')
print(f'New flip rate (10 pairs): {new_flips}/10 = {new_flips/10*100:.0f}%')
print(f'Change: {new_flips-old_flips:+d} pairs')

# Save
with open('/tmp/age_fixed_rejudge.jsonl','w') as f:
    for r in results:
        f.write(json.dumps({'pair':r[0], 'old_flip':r[1], 'new_flip':r[2], 'new_elig':r[3]})+'\n')
