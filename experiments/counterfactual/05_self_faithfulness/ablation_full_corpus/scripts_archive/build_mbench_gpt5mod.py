#!/usr/bin/env python3
"""Build the gpt-5-modifier mbench directory structure for all systems.

Mirrors the existing mbench/ layout (which is gpt-4.1 modifier) so we get
two clean experimental folders under counterfactual/:
  mbench_modifier_gpt4_1/  (rename of existing mbench/)
  mbench_modifier_gpt5/    (new — built here)

For baselines (v5, v5_blockers, tg, shah, v5_gpt5) — rejudge results exist
in self_faithfulness_baselines_gpt5modifier_rejudged.jsonl, so we can
categorize each pair into flipped/not_flipped/invalid_cf right now.

For aegis — only 61/176 pairs have AEGIS rejudge (from the age-target fix).
The remaining 115 are marked 'pending' under aegis/_pending/.
"""
import json, pathlib, shutil
ROOT = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored')
OUT = ROOT/'experiments/counterfactual/05_self_faithfulness/mbench_modifier_gpt5'
if OUT.exists(): shutil.rmtree(OUT)
OUT.mkdir(parents=True)

# Load baselines + their rejudge
baseline_cfs = {(r['pair'], r['system']): r for r in [json.loads(l) for l in (ROOT/'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl').open() if l.strip()]}
rejudges = {(r['pair'], r['system']): r for r in [json.loads(l) for l in (ROOT/'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier_rejudged.jsonl').open() if l.strip()]}
smt_cfs = {r['pair']: r for r in [json.loads(l) for l in (ROOT/'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_smt_gpt5modifier.jsonl').open() if l.strip()]}

# AEGIS partial rejudge
aegis_rj = {}
p_age = pathlib.Path('/tmp/age_corrected_aegis_rejudge.jsonl')
if p_age.exists():
    for ln in p_age.open():
        try: o=json.loads(ln); aegis_rj[o['pair']]=o
        except: continue

# Build per-system per-pair
SYSTEMS = ['aegis','v5','v5_blockers','tg','shah','v5_gpt5']
counts = {s: {'flipped':0,'not_flipped':0,'invalid_cf':0,'pending':0,'skipped':0} for s in SYSTEMS}

def write_pair(sys_name, pair, outcome, info):
    pdir = OUT/sys_name/outcome/pair
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir/'info.json').write_text(json.dumps(info, indent=2))
    if info.get('cf_chart'):
        (pdir/'cf_chart.txt').write_text(info['cf_chart'])
    if info.get('cited'):
        (pdir/'cited_blockers.txt').write_text(str(info['cited']))

for (pair, sys), rec in baseline_cfs.items():
    if sys not in SYSTEMS: continue
    cf_valid = rec.get('cf_valid')
    rj = rejudges.get((pair, sys), {})
    rj_elig = rj.get('cf_eligibility_under_v2')
    if cf_valid is False:
        outcome = 'invalid_cf'
    elif rj_elig == 'eligible':
        outcome = 'flipped'
    elif rj_elig in ('ineligible','uncertain'):
        outcome = 'not_flipped'
    else:
        outcome = 'pending'
    counts[sys][outcome] += 1
    write_pair(sys, pair, outcome, {
        'pair': pair, 'system': sys,
        'cf_chart': rec.get('cf_chart',''),
        'cited': rec.get('cited_blocker_text') or rec.get('cited',''),
        'cf_valid': cf_valid,
        'rejudged_verdict': rj_elig,
        'simclin_flips_cited': rec.get('simclin_flips_cited'),
    })

# AEGIS
for pair, rec in smt_cfs.items():
    sys = 'aegis'
    cf_valid = rec.get('simclin_coherent') is True and rec.get('simclin_keeps_other') is True
    if not cf_valid:
        outcome = 'invalid_cf'
        rj_elig = None
    elif pair in aegis_rj:
        rj_elig = aegis_rj[pair].get('new_elig')
        outcome = 'flipped' if rj_elig=='eligible' else 'not_flipped'
    else:
        outcome = 'pending'
        rj_elig = None
    counts[sys][outcome] += 1
    write_pair(sys, pair, outcome, {
        'pair': pair, 'system': sys,
        'cf_chart': rec.get('cf_chart',''),
        'cited': rec.get('cited_blocker_text',''),
        'cf_valid': cf_valid,
        'rejudged_verdict': rj_elig,
        'simclin_flips_cited': rec.get('simclin_flips_cited'),
    })

# Write summaries
lines = ['# mbench_modifier_gpt5 — counterfactual self-faithfulness with gpt-5 modifier\n']
lines.append('| system | flipped | not_flipped | invalid_cf | pending | total | self-flip rate (validator-confirmed) |')
lines.append('|---|---|---|---|---|---|---|')
for sys in SYSTEMS:
    c = counts[sys]
    total = c['flipped'] + c['not_flipped'] + c['invalid_cf'] + c['pending']
    denom = c['flipped'] + c['not_flipped']
    rate = f'{100*c["flipped"]/denom:.1f}%' if denom else 'n/a'
    if c['pending']: rate += f' ({c["pending"]} pending)'
    lines.append(f'| {sys} | {c["flipped"]} | {c["not_flipped"]} | {c["invalid_cf"]} | {c["pending"]} | {total} | {rate} |')
lines.append('')
lines.append('Modifier: gpt-5 (atom-target chart editor)\n')
lines.append('Validator: gpt-5 simulated clinician (coherent + keeps_other)\n')
lines.append('Rejudge: each system\'s own production matcher.\n')
(OUT/'00_index.md').write_text('\n'.join(lines))

print('Wrote', OUT)
print('\n'.join(lines))
