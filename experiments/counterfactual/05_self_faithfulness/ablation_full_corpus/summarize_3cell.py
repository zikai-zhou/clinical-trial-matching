#!/usr/bin/env python3
"""Summarize the 3-cell × 6-system ablation.

Output: a single table per cell with (system, ran, valid, flipped, rate, errs).
Also writes per-(cell, system) breakdown CSV.
"""
import json, pathlib, csv, sys

HERE = pathlib.Path(__file__).resolve().parent
OUT  = HERE/'out'
CELLS = ['cell1_4.1m_4.1v_filt', 'cell2_4.1m_v3v_filt', 'cell3_5m_v3v_filt']
SYSTEMS = ['aegis', 'v5', 'v5_cot', 'v5_blockers', 'tg', 'shah']

def is_valid(v):
    if 'valid' in v: return bool(v.get('valid'))
    return bool(v.get('coherent') and v.get('flips_target_atom') and v.get('keeps_other_facts'))

def merge_cell(tag):
    fp = OUT/tag/'records.merged.jsonl'
    parts = sorted((OUT/tag).glob('records.*of*.jsonl'))
    parts = [p for p in parts if 'merged' not in p.name]
    with fp.open('w') as out:
        for p in parts:
            out.write(p.read_text())
    return fp

def summarize(records_fp):
    # Merge multi-line-per-pair (from retry passes): later cells override earlier
    by_pair = {}
    for l in records_fp.open():
        if not l.strip(): continue
        r = json.loads(l)
        p = r['pair']
        if p not in by_pair:
            by_pair[p] = {'pair': p, 'cell': r.get('cell'), 'systems': {}}
        for s, sd in (r.get('systems') or {}).items():
            # Prefer non-error entries
            existing = by_pair[p]['systems'].get(s)
            if existing and isinstance(existing, dict) and not existing.get('error'):
                continue
            by_pair[p]['systems'][s] = sd
    recs = list(by_pair.values())
    summary = {}
    for s in SYSTEMS:
        ran = valid = flipped = err = skip = 0
        first_round_eligible = 0
        for r in recs:
            sd = (r.get('systems') or {}).get(s)
            if not sd: continue
            if sd.get('error'): err += 1; continue
            if sd.get('skipped'):
                skip += 1
                if 'first-round' in str(sd.get('skipped','')): first_round_eligible += 1
                continue
            ran += 1
            v = sd.get('cf_validation') or {}
            if is_valid(v): valid += 1
            if is_valid(v) and sd.get('rejudged',{}).get('eligibility') == 'eligible':
                flipped += 1
        rate = (100*flipped/valid) if valid else 0
        summary[s] = dict(ran=ran, valid=valid, flipped=flipped, rate=rate,
                          errors=err, skipped=skip, first_round_elig=first_round_eligible,
                          total_records=len(recs))
    return summary

results = {}
for tag in CELLS:
    fp = merge_cell(tag)
    results[tag] = summarize(fp)
    print(f'merged {fp.name}: {sum(1 for _ in fp.open())} records')

print()
print('='*100)
print(f'{"":12} | '+' | '.join(f'{tag.replace("_filt",""):>22}' for tag in CELLS))
print('-'*100)
hdr = f'{"system":12} | '+' | '.join(f'{"flipped/valid (rate%)":>22}' for _ in CELLS)
print(hdr)
print('-'*100)
for s in SYSTEMS:
    cells_strs = []
    for tag in CELLS:
        d = results[tag][s]
        cells_strs.append(f'{d["flipped"]}/{d["valid"]} ({d["rate"]:5.1f}%)'.rjust(22))
    print(f'{s:12} | '+' | '.join(cells_strs))

print()
print('Coverage (ran / total ineligible / errors):')
for s in SYSTEMS:
    parts = []
    for tag in CELLS:
        d = results[tag][s]
        parts.append(f'{d["ran"]:3} ran, {d["errors"]:2} err')
    print(f'  {s:12}  ' + '   |   '.join(parts))

# Write CSV
csv_fp = HERE/'summary_3cell.csv'
with csv_fp.open('w') as f:
    w = csv.writer(f)
    w.writerow(['cell','system','ran','valid','flipped','rate','errors','skipped','first_round_elig'])
    for tag in CELLS:
        for s in SYSTEMS:
            d = results[tag][s]
            w.writerow([tag,s,d['ran'],d['valid'],d['flipped'],f'{d["rate"]:.1f}',d['errors'],d['skipped'],d['first_round_elig']])
print(f'\nwrote {csv_fp}')
