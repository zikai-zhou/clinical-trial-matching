#!/usr/bin/env python3
"""Find all (pair, system) cells with errors and rerun JUST those.

Resume in run_full_ablation.py only skips a (pair, system) if it's already in
records.*.jsonl. So we delete the errored cells from the files first, then
re-launch the driver.
"""
import json, pathlib, sys, os

HERE = pathlib.Path(__file__).resolve().parent
OUT  = HERE/'out'
CELLS = ['cell1_4.1m_4.1v_filt', 'cell2_4.1m_v3v_filt', 'cell3_5m_v3v_filt']

def cell_to_env(tag):
    """Map cell tag -> CELL number."""
    return {'cell1_4.1m_4.1v_filt':1, 'cell2_4.1m_v3v_filt':2, 'cell3_5m_v3v_filt':3}[tag]

for tag in CELLS:
    d = OUT/tag
    err_pairs = {}  # pair -> list[system]
    for fp in sorted(d.glob('records.*of*.jsonl')):
        if 'merged' in fp.name: continue
        recs = [json.loads(l) for l in fp.open() if l.strip()]
        for r in recs:
            errored = [s for s, sd in (r.get('systems') or {}).items()
                       if isinstance(sd, dict) and sd.get('error')]
            for s in errored:
                err_pairs.setdefault(r['pair'], []).append(s)
    print(f'\n{tag}: {sum(len(v) for v in err_pairs.values())} errored cells across {len(err_pairs)} pairs')

    # Strip errored-system fields out of the slice files (keeps non-error cells)
    for fp in sorted(d.glob('records.*of*.jsonl')):
        if 'merged' in fp.name: continue
        recs = [json.loads(l) for l in fp.open() if l.strip()]
        changed = False
        for r in recs:
            sds = r.get('systems') or {}
            for s in list(sds):
                if isinstance(sds[s], dict) and sds[s].get('error'):
                    del sds[s]
                    changed = True
        if changed:
            with fp.open('w') as f:
                for r in recs: f.write(json.dumps(r)+'\n')
            print(f'  stripped errors from {fp.name}')
