#!/usr/bin/env python3
"""Snapshot C': gpt-5 modifier + gpt-4.1 validator + blocker-filtered shah/tg rationales.

Same as C, but monkey-patches `bl.shahlab_blockers` and `bl.tg_blockers` so
they return the filtered `rationale_text` (only [not met]/[excluded] rows)
instead of the full per-criterion rationale. This isolates how much of C's
flip-rate gain came from the gpt-5 modifier vs from "longer context."
"""
import json, os, pathlib, sys
HERE = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored')
sys.path.insert(0, str(HERE/'experiments/counterfactual/utils'))
sys.path.insert(0, str(HERE/'experiments/counterfactual/05_self_faithfulness'))
from concurrent.futures import ThreadPoolExecutor, as_completed
import cf_dataset as ds
import cf_blockers as bl
import run as orig

# ── Monkey-patch: filter shah / tg rationale_text to blocker rows only ──
_orig_shah_blockers = bl.shahlab_blockers
def _shah_blockers_filtered(rec):
    out = _orig_shah_blockers(rec)
    full = out.get('rationale_text','') or ''
    # Per shah's [met]/[not met] convention: [not met] = patient fails the
    # criterion (regardless of side) = blocker.
    inc_block, exc_block = [], []
    section = None
    for line in full.split('\n'):
        s = line.strip()
        if s.startswith('Inclusion criteria'): section='inc'; continue
        if s.startswith('Exclusion criteria'): section='exc'; continue
        if '[not met]' in s.lower():
            (inc_block if section=='inc' else exc_block).append('  '+s)
    parts = []
    if inc_block: parts.append('Inclusion criteria the patient FAILS:\n' + '\n'.join(inc_block))
    if exc_block: parts.append('Exclusion criteria the patient TRIGGERS:\n' + '\n'.join(exc_block))
    filtered = '\n\n'.join(parts) or '(no [not met] rows found)'
    out['rationale_text'] = filtered
    return out
bl.shahlab_blockers = _shah_blockers_filtered

_orig_tg_blockers = bl.tg_blockers
def _tg_blockers_filtered(rec):
    out = _orig_tg_blockers(rec)
    full = out.get('rationale_text','') or ''
    inc_block, exc_block = [], []
    section = None
    for line in full.split('\n'):
        s = line.strip()
        if 'Inclusion criteria' in s and ':' in s: section='inc'; continue
        if 'Exclusion criteria' in s and ':' in s: section='exc'; continue
        sl = s.lower()
        is_block = False
        if section == 'inc' and '[not included]' in sl: is_block = True
        if section == 'exc' and '[excluded]' in sl and 'not excluded' not in sl: is_block = True
        if is_block:
            (inc_block if section=='inc' else exc_block).append('  '+s)
    parts = []
    if inc_block: parts.append('Inclusion criteria the patient FAILS:\n' + '\n'.join(inc_block))
    if exc_block: parts.append('Exclusion criteria the patient TRIGGERS:\n' + '\n'.join(exc_block))
    filtered = '\n\n'.join(parts) or '(no blocker rows found)'
    out['rationale_text'] = filtered
    return out
bl.tg_blockers = _tg_blockers_filtered

# ── Standard subset + slice handling ──
PAIRS = [p.strip() for p in open('/tmp/repro_subset.txt') if p.strip()]
SYSTEMS = ['aegis','v5','tg','shah']
slice_arg = os.environ.get('SLICE','0/1')
slice_idx, slice_n = map(int, slice_arg.split('/'))
PAIRS = PAIRS[slice_idx::slice_n]
SLICE_TAG = slice_arg.replace('/','of')
OUT = HERE/f'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_gpt5mod_filtered_subset.{SLICE_TAG}.jsonl'

if not (os.environ.get('OPENAI_ENDPOINT') and os.environ.get('OPENAI_API_KEY')):
    sys.exit('OPENAI_ENDPOINT + OPENAI_API_KEY required')

print(f'subset slice {SLICE_TAG}: {len(PAIRS)} pairs (synchronous, FILTERED shah/tg)', flush=True)
charts=ds.load_charts(); trials=ds.load_trial_text()
aegis=ds.load_aegis_v9_arbiter(); v5=ds.load_v5(); v5b=ds.load_v5_blockers()
tg=ds.load_tg(); shah=ds.load_shahlab()
mine=ds.load_v9_mine(); arb=ds.load_arbiter_cache()

cache=set()
if OUT.exists():
    for ln in OUT.open():
        try: cache.add(json.loads(ln)['pair'])
        except: pass
todo=[p for p in PAIRS if p not in cache]
print(f'cached: {len(cache)}  todo: {len(todo)}', flush=True)

with OUT.open('a') as f:
    for n, p in enumerate(todo, 1):
        try:
            rec=orig.process_pair(p, SYSTEMS, charts, trials, mine, arb,
                                  aegis, v5, tg, shah, v5b)
        except Exception as e:
            print(f'  [{SLICE_TAG}][ERR] {p}: {type(e).__name__}: {str(e)[:200]}', flush=True); continue
        f.write(json.dumps(rec)+'\n'); f.flush()
        sys_flips={s: int((rec.get('systems',{}).get(s,{}) or {}).get('flipped',False)) for s in SYSTEMS}
        print(f'  [{SLICE_TAG}][{n:2}/{len(todo)}] {p}  flips={sys_flips}', flush=True)
