#!/usr/bin/env python3
"""Re-judge the 61 age-corrected CFs with AEGIS (cmsrc pipeline).

For each new CF, swap it into a temp dataset, run match_patient_to_trial.py
with the production prompt root/map, and capture the new SAT/UNSAT verdict.
Compute the new flip rate."""
import json, os, pathlib, sys, subprocess, tempfile, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored')
CMSRC_DIR = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT/cmsrc')
CMSRC_PY = pathlib.Path('/Users/xyrus/.pyenv/versions/3.11.9/bin/python')
PR_ROOT = ROOT/'experiments/53_v2_full/inputs/prompt_root'
PR_MAP = PR_ROOT/'prompt_out/prompt_map.json'
ENDPOINT = os.environ.get('OPENAI_ENDPOINT','')
KEY = os.environ.get('OPENAI_API_KEY','')

sf = {r['pair']:r for r in [json.loads(l) for l in (ROOT/'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl').open() if l.strip()]}
new_cfs = {}
for ln in open('/tmp/age_corrected_cfs_full.jsonl'):
    o=json.loads(ln); new_cfs[o['pair']]=o['cf_chart']

# Loadable cache
cache_path = pathlib.Path('/tmp/age_corrected_aegis_rejudge.jsonl')
done = {}
if cache_path.exists():
    for ln in cache_path.open():
        try: o=json.loads(ln); done[o['pair']]=o
        except: continue

def rejudge(pair):
    if pair in done: return done[pair]
    a = sf[pair]['systems']['aegis']
    full_tid = a.get('deciding_variant')
    cf = new_cfs.get(pair)
    if not (full_tid and cf):
        return {'pair':pair, 'new_elig':'error', 'rationale':'missing tid or cf'}
    pid = pair.split('__')[0]
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        (td/'sigir').mkdir(parents=True)
        shutil.copy(ROOT/'dataset/clinical_trial/sigir/corpus.jsonl', td/'sigir/corpus.jsonl')
        with (td/'sigir/queries.jsonl').open('w') as fout:
            for line in open(ROOT/'dataset/clinical_trial/sigir/queries.jsonl'):
                try: o=json.loads(line)
                except: fout.write(line); continue
                if o.get('_id')==pid: o['text']=cf
                fout.write(json.dumps(o)+'\n')
        out_root = td/'cmsrc_out'
        cmd = [str(CMSRC_PY), str(CMSRC_DIR/'match_patient_to_trial.py'),
               full_tid, pid, '--out-root', str(out_root),
               '--prompt-root', str(PR_ROOT), '--prompt-map', str(PR_MAP),
               '--miner-mode', 'infer', '--data-root', str(td), '--build-root', '../build']
        env = dict(os.environ); env['OPENAI_ENDPOINT']=ENDPOINT; env['OPENAI_API_KEY']=KEY
        try:
            res = subprocess.run(cmd, cwd=str(CMSRC_DIR), capture_output=True, text=True, timeout=240, env=env)
        except Exception as e:
            return {'pair':pair, 'new_elig':'error', 'rationale':f'subprocess: {str(e)[:120]}'}
        if res.returncode != 0:
            return {'pair':pair, 'new_elig':'error', 'rationale':f'rc={res.returncode}: {res.stderr[-200:]}'}
        full_fp = out_root/pid/f'{full_tid}__full.json'
        if not full_fp.exists():
            return {'pair':pair, 'new_elig':'error', 'rationale':'no full.json'}
        try: o=json.loads(full_fp.read_text())
        except Exception as e: return {'pair':pair, 'new_elig':'error', 'rationale':f'json: {e}'}
        inc_sat = o.get('inclusion',{}).get('sat_like')
        exc_sat = o.get('exclusion',{}).get('sat_like')
        elig = 'eligible' if (inc_sat is not False) and (exc_sat is not False) else 'ineligible'
        return {'pair':pair, 'new_elig':elig, 'inc_sat':inc_sat, 'exc_sat':exc_sat}

to_run = [p for p in new_cfs if p not in done]
print(f'Re-judging {len(to_run)} pairs ({len(done)} cached)')
with cache_path.open('a') as f:
    with ThreadPoolExecutor(max_workers=4) as ex:
        i = 0
        for fut in as_completed({ex.submit(rejudge, p): p for p in to_run}):
            r = fut.result()
            f.write(json.dumps(r)+'\n'); f.flush()
            done[r['pair']] = r
            i += 1
            if i % 5 == 0: print(f'  {i}/{len(to_run)}  last={r["pair"]} -> {r["new_elig"]}', flush=True)

# Compute flip rate
old_flips = sum(1 for pair in new_cfs if sf[pair]['systems']['aegis'].get('flipped_end_to_end') is True)
new_flips = sum(1 for pair, r in done.items() if pair in new_cfs and r.get('new_elig') == 'eligible')
errs = sum(1 for pair, r in done.items() if pair in new_cfs and r.get('new_elig') == 'error')
print(f'\n=== Corpus-wide flip rate impact (61 age-affected pairs) ===')
print(f'  OLD flips (buggy CFs):       {old_flips}/61 = {100*old_flips/61:.1f}%')
print(f'  NEW flips (corrected CFs):   {new_flips}/61 = {100*new_flips/61:.1f}%')
print(f'  errors during re-judge:      {errs}/61')
print(f'  Δ flip count: {new_flips - old_flips:+d}')
