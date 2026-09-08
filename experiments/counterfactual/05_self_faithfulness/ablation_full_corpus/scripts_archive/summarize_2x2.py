#!/usr/bin/env python3
"""Print the 2x2 (+C', D) ablation table.

Dimensions: modifier × validator × shah/tg-blocker-filter."""
import json, pathlib
SF = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored/experiments/counterfactual/05_self_faithfulness/out')

def summarize(path, valid_key='cf_valid', flipped_key='flipped'):
    if not path.exists(): return None
    recs = [json.loads(l) for l in path.open() if l.strip()]
    out = {}
    for s in ('aegis','v5','tg','shah'):
        valid = flipped = total = 0
        for r in recs:
            sd = (r.get('systems') or {}).get(s) or {}
            if sd.get('skipped'): continue
            total += 1
            if sd.get(valid_key): valid += 1
            if sd.get(flipped_key) and sd.get(valid_key): flipped += 1
        rate = (100*flipped/valid) if valid else 0
        out[s] = (total, valid, flipped, rate)
    return out

def merge_slices_if_needed(prefix):
    merged = SF/f'{prefix}.jsonl'
    if merged.exists() and merged.stat().st_size > 0: return
    slices = sorted(SF.glob(f'{prefix}.*of8.jsonl'))
    if slices:
        with merged.open('w') as o:
            for s in slices: o.write(s.read_text())

for prefix in ('self_faithfulness_repro_subset',
               'self_faithfulness_gpt5modifier_origvalidator_subset',
               'self_faithfulness_gpt5mod_filtered_subset'):
    merge_slices_if_needed(prefix)

A  = summarize(SF/'self_faithfulness_repro_subset.jsonl')
B  = summarize(SF/'self_faithfulness_repro_subset_with_v3_validator.jsonl',
               valid_key='cf_valid_under_v3_validator', flipped_key='flipped_under_v3_validator')
C  = summarize(SF/'self_faithfulness_gpt5modifier_origvalidator_subset.jsonl')
Cp = summarize(SF/'self_faithfulness_gpt5mod_filtered_subset.jsonl')
D  = summarize(SF/'self_faithfulness_gpt5modifier_with_v3_validator.jsonl',
               valid_key='cf_valid_under_v3_validator', flipped_key='flipped_under_v3_validator')

PUBLISHED = {'aegis':81.4,'v5':33.7,'tg':51.4,'shah':57.9}

print('='*135)
header = f'{"system":<6} | {"PUB":>6}  {"A (4.1m+4.1v)":>15} {"B (4.1m+v3v)":>15} {"C (5m+4.1v,full)":>18} {"Cp (5m+4.1v,filt)":>20} {"D (5m+v3v)":>15}'
print(header); print('-'*135)
def fmt(d, s):
    if d is None or s not in d: return '       n/a       '
    t,v,f,r = d[s]
    return f'{f}/{v} = {r:5.1f}%'
for s in ('aegis','v5','tg','shah'):
    pub = PUBLISHED[s]
    print(f'{s:<6} | {pub:>5.1f}%  {fmt(A,s):>15} {fmt(B,s):>15} {fmt(C,s):>18} {fmt(Cp,s):>20} {fmt(D,s):>15}')
print()
print('Sample size: 45 pairs (vs 178 published, seed=42)')
print('A : published reproduction (gpt-4.1 modifier + gpt-4.1 validator).')
print('B : A\'s CFs revalidated with the v3 gpt-5 simclin validator.')
print('C : gpt-5 modifier + gpt-4.1 validator; FULL shah/tg rationale to modifier.')
print('Cp: same as C but shah/tg rationale FILTERED to blocker rows only ([not met] / [excluded]).')
print('D : gpt-5 modifier + v3 gpt-5 simclin validator (C\'s CFs revalidated).')
print()
print('Comparisons:')
print('  A vs Pub       : run-to-run noise on subset')
print('  B vs A         : validator-only effect (same CFs, different judge)')
print('  C vs A         : modifier-only effect (different CFs, same judge)')
print('  Cp vs C        : rationale-filter effect on shah/tg modifier input')
print('  D vs C         : validator-only effect on gpt-5-mod CFs (mirrors B-vs-A but with C CFs)')
print('  D vs B         : modifier-only effect under v3 validator')
