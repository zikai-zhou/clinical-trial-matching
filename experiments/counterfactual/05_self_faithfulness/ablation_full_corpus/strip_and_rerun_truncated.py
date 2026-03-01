#!/usr/bin/env python3
"""Strip affected (pair, system) cells from records.*.jsonl in-place so the
driver's per-(pair, system) resume will re-run only those cells.

Reads affected_cells.json (produced by find_truncated_pairs.py).
"""
import json, pathlib, sys
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE/'out'

affected = json.loads((HERE/'affected_cells.json').read_text())
# Group by (cell, source_variant, pair): which systems to strip
to_strip = defaultdict(set)  # (cell, variant) -> {(pair, system), ...}
for a in affected:
    cell = a['cell']; pair = a['pair']; system = a['system']
    variant = '_v5cot5' if system == 'v5_cot_gpt5' else ''
    to_strip[(cell, variant)].add((pair, system))

for (cell, variant), pair_systems in sorted(to_strip.items()):
    src = OUT/(cell+variant)
    if not src.exists():
        print(f'skip {cell}{variant}: dir missing'); continue
    print(f'\n{cell}{variant}: {len(pair_systems)} (pair,system) cells to strip')
    stripped = 0
    for fp in sorted(src.glob('records.*of*.jsonl')):
        if 'merged' in fp.name: continue
        recs = []
        for ln in fp.open():
            try: r = json.loads(ln)
            except: continue
            p = r['pair']
            sds = r.get('systems') or {}
            for s in list(sds):
                if (p, s) in pair_systems:
                    del sds[s]; stripped += 1
            r['systems'] = sds
            recs.append(r)
        with fp.open('w') as f:
            for r in recs: f.write(json.dumps(r)+'\n')
    print(f'  stripped {stripped} cells')
print('\nDone. Now relaunch the driver — it will pick up missing (pair, system) cells via per-cell resume.')
