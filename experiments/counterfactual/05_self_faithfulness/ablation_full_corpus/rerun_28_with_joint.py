#!/usr/bin/env python3
"""Re-run the 28 cell-1 not_flipped AEGIS pairs with joint-maxsat blocker
extraction. Compare flip rates.

Pipeline per pair:
  1. aegis_blockers_joint(...) → targets, preserve, co_present, joint_verified
  2. cf_generator.generate_cf_from_targets(...)
  3. cf_validator.validate(...)
  4. judge_aegis_cmsrc(...) → re-check eligibility
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'experiments/counterfactual/utils'))
sys.path.insert(0, str(ROOT/'experiments/counterfactual/05_self_faithfulness'))

import cf_dataset as ds
import cf_blockers as bl
import cf_generator as gen
import cf_validator as val

charts  = ds.load_charts()
trials  = ds.load_trial_text()
mine    = ds.load_v9_mine()
arbiter = ds.load_arbiter_cache()

CMSRC_DIR = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT/cmsrc')
PR_ROOT = ROOT/'experiments/53_v2_full/inputs/prompt_root'
PR_MAP  = PR_ROOT/'prompt_out/prompt_map.json'

def judge_aegis(pair, deciding, cf_chart):
    pid = pair.split('__')[0]
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td); (td/'sigir').mkdir(parents=True)
        shutil.copy(ROOT/'dataset/clinical_trial/sigir/corpus.jsonl', td/'sigir/corpus.jsonl')
        with (td/'sigir/queries.jsonl').open('w') as fout:
            for line in (ROOT/'dataset/clinical_trial/sigir/queries.jsonl').open():
                try: o=json.loads(line)
                except: fout.write(line); continue
                if o.get('_id')==pid: o['text']=cf_chart
                fout.write(json.dumps(o)+'\n')
        out_root = td/'cmsrc_out'
        cmd = [sys.executable, str(CMSRC_DIR/'match_patient_to_trial.py'),
               deciding, pid, '--out-root', str(out_root),
               '--prompt-root', str(PR_ROOT), '--prompt-map', str(PR_MAP),
               '--miner-mode','infer','--data-root', str(td),'--build-root','../build']
        r = subprocess.run(cmd, cwd=str(CMSRC_DIR), capture_output=True, text=True, timeout=300, env=dict(os.environ))
        full_fp = out_root/pid/f'{deciding}__full.json'
        if not full_fp.exists():
            return {'eligibility':'error','rationale':f'rc={r.returncode}: {r.stderr[-200:]}'}
        try: o = json.loads(full_fp.read_text())
        except Exception as e: return {'eligibility':'error','rationale':f'json: {e}'}
        inc_sat = o.get('inclusion',{}).get('sat_like'); exc_sat = o.get('exclusion',{}).get('sat_like')
        elig = 'eligible' if (inc_sat is not False) and (exc_sat is not False) else 'ineligible'
        return {'eligibility': elig, 'inclusion_sat_like': inc_sat, 'exclusion_sat_like': exc_sat}

records_fp = HERE/'out'/'cell1_4.1m_4.1v_filt'/'records.merged.jsonl'
target_pairs = []
for ln in records_fp.open():
    r = json.loads(ln); sd = (r.get('systems') or {}).get('aegis')
    if not sd or sd.get('error') or sd.get('skipped'): continue
    v = sd.get('cf_validation') or {}
    if not v.get('valid'): continue
    if sd.get('rejudged',{}).get('eligibility') == 'eligible': continue
    target_pairs.append((r['pair'], sd.get('deciding_variant'), sd))

print(f'rerunning {len(target_pairs)} not_flipped pairs with joint maxsat...\n')
out_recs = []
for i, (pair, deciding, old_sd) in enumerate(target_pairs):
    print(f'[{i+1}/{len(target_pairs)}] {pair}', flush=True)
    pid, nct = pair.split('__', 1)
    chart = charts.get(pid,''); trial = trials.get(re.sub(r'[a-z]+$','',nct),'')
    vs = mine.get(pair, {})
    full_json = None
    for key, (full_tid, fj) in vs.items():
        if full_tid == deciding: full_json = fj; break
    if not full_json: continue

    blk = bl.aegis_blockers_joint(pair, deciding, full_json, arbiter)
    targets = (blk['inc_blockers'] + blk['exc_blockers'])[:8]
    preserve = ((blk.get('inc_preserve') or []) + (blk.get('exc_preserve') or []))[:25]
    other_atoms = ((blk.get('inc_all_atoms') or []) + (blk.get('exc_all_atoms') or []))[:80]

    cf = gen.generate_cf_from_targets(chart, targets, preserve=preserve, other_atoms=other_atoms)
    cf_val = val.validate(chart, cf, json.dumps(targets, indent=2), preserve_facts_text=json.dumps(preserve))
    rej = judge_aegis(pair, deciding, cf)
    out_recs.append({
        'pair': pair, 'deciding': deciding,
        'joint_verified': blk.get('joint_verified'),
        'co_present_atoms': blk.get('co_present_atoms'),
        'targets': targets, 'preserve_count': len(preserve),
        'cf_validation': cf_val, 'rejudged': rej,
        'old_rejudged': {'inc': old_sd.get('rejudged',{}).get('inclusion_sat_like'),
                          'exc': old_sd.get('rejudged',{}).get('exclusion_sat_like')},
    })
    print(f'    valid={cf_val.get("valid")} rej={rej.get("eligibility")} (inc={rej.get("inclusion_sat_like")} exc={rej.get("exclusion_sat_like")})')

out_fp = HERE/'aegis_failure_analysis'/'joint_maxsat_28_results.jsonl'
with out_fp.open('w') as f:
    for r in out_recs: f.write(json.dumps(r)+'\n')
print(f'\nwrote {out_fp}')

# Compare
flipped_now = sum(1 for r in out_recs if r['cf_validation'].get('valid') and r['rejudged'].get('eligibility') == 'eligible')
valid_now = sum(1 for r in out_recs if r['cf_validation'].get('valid'))
print(f'\n=== Before joint: 0/28 of these pairs flipped (by construction) ===')
print(f'=== After  joint: {flipped_now}/{valid_now} valid CFs flipped ({100*flipped_now/max(1,valid_now):.1f}%) ===')
print(f'(if added back to cell1 aegis: {123+flipped_now}/{151} = {100*(123+flipped_now)/151:.1f}%)')
