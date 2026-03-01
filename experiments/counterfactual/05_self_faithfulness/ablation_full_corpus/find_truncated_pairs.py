#!/usr/bin/env python3
"""Identify (pair, system) cells whose modifier or validator saw truncated input.

Truncation thresholds in the OLD code (now lifted):
- v5_cot, v5_cot_gpt5: criterion[:120], chart_fact[:120] in decisive_blockers
- v5_blockers: criterion[:100], fact[:100] in blockers
- tg:           reasoning[:200] per inc/exc row
- cf_generator.generate_cf_from_rationale: trial[:2500], rationale[:2500], supports[:2500]
- cf_generator.generate_cf_from_targets: chart[:3500] (charts max 1290 so 0 impact)
- cf_validator.validate: original_chart[:3500], cf_chart[:3500], cited_facts_text[:3000], preserve_facts_text[:2000]

We mark a cell "affected" if ANY of these caps actually fired on its content.

Writes a JSON list of (cell_tag, pair, system) tuples to rerun.
"""
import json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE/'out'
CELLS = ['cell1_4.1m_4.1v_filt', 'cell2_4.1m_v3v_filt', 'cell3_5m_v3v_filt']

# Load trial sizes (to detect trial[:2500] truncation in cf_generator)
ROOT = HERE.parents[3]
trial_sizes = {}
import re
for ln in open(ROOT/'dataset/clinical_trial/sigir/corpus.jsonl'):
    try:
        o = json.loads(ln); trial_sizes[o.get('_id','')] = len(o.get('text','') or '')
    except: pass

def trial_too_long(pair):
    pid, nct = pair.split('__',1)
    base = re.sub(r'[a-z]+$','',nct)
    return trial_sizes.get(base, 0) > 2500

affected = []  # list of {cell, pair, system, reasons}
SOURCE_VARIANTS = [
    ('', None),               # default dir, all systems except v5_cot_gpt5
    ('_v5cot5', 'v5_cot_gpt5'),  # v5_cot_gpt5 only
]
for cell in CELLS:
  for variant, only_system in SOURCE_VARIANTS:
    src = OUT/(cell+variant)
    if not src.exists(): continue
    by_pair = {}
    for fp in src.glob('records.*of*.jsonl'):
        if 'merged' in fp.name: continue
        for ln in fp.open():
            try: r = json.loads(ln)
            except: continue
            p = r['pair']
            if p in by_pair and isinstance(by_pair[p], dict) and not by_pair[p].get('error'): continue
            by_pair[p] = r
    for p, r in by_pair.items():
        for system, sd in (r.get('systems') or {}).items():
            if only_system and system != only_system: continue
            if not sd or sd.get('error') or sd.get('skipped'): continue
            reasons = []
            # 1. Per-blocker text caps
            fr = sd.get('first_round') or {}
            if system in ('v5_cot','v5_cot_gpt5'):
                for b in fr.get('decisive_blockers') or []:
                    if len(b.get('criterion','')) > 120 or len(b.get('chart_fact','')) > 120:
                        reasons.append('blocker_text>120'); break
            elif system == 'v5_blockers':
                for b in fr.get('blockers') or []:
                    if len(b.get('criterion','')) > 100 or len(b.get('fact','')) > 100:
                        reasons.append('blocker_text>100'); break
            elif system == 'tg':
                for row in (fr.get('inc_rows') or []) + (fr.get('exc_rows') or []):
                    if len(row.get('reasoning','')) > 200:
                        reasons.append('tg_reasoning>200'); break
            # 2. trial > 2500 (modifier truncated trial)
            if system != 'aegis' and trial_too_long(p):
                reasons.append('trial>2500')
            # 3. Rationale > 2500 in cf_generator (text-rationale modifier)
            rat = sd.get('cited_rationale','') or ''
            if system != 'aegis' and len(rat) > 2500:
                reasons.append('rationale>2500')
            # 4. AEGIS validator cited/preserve caps
            if system == 'aegis':
                targets_str = json.dumps(sd.get('targets',[]), indent=2)
                if len(targets_str) > 3000: reasons.append('aegis_cited>3000')
                preserve_str = json.dumps(sd.get('preserve',[]))
                if len(preserve_str) > 2000: reasons.append('aegis_preserve>2000')
            if reasons:
                affected.append({'cell': cell, 'pair': p, 'system': system, 'reasons': reasons})

# Summarize
from collections import Counter
print(f'Total affected cells: {len(affected)}')
print()
per_cell_sys = Counter((a['cell'], a['system']) for a in affected)
for (cell, sys_), n in sorted(per_cell_sys.items()):
    print(f'  {cell:35} {sys_:14} {n:4}')
print()
reason_counts = Counter(r for a in affected for r in a['reasons'])
print('Reason distribution:')
for r, n in reason_counts.most_common():
    print(f'  {r:25} {n:5}')

out_fp = HERE/'affected_cells.json'
out_fp.write_text(json.dumps(affected, indent=2))
print(f'\nwrote {out_fp}')
