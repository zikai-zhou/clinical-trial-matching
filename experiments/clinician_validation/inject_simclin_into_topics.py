#!/usr/bin/env python3
"""Inject claude_simclin judgments into the cf_rewrite_review topics as
admin-visible simclin_* fields per rewrite.

These are visible only when logged in as admin (per server.py CF normalization
logic): simclin_coherent, simclin_flips_cited, simclin_new_blocker, simclin_explanation.

Run after rebuilding the audit & after the judgments dict is up to date.
"""
import json, pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from claude_simclin_judgments import JUDGMENTS

FE = pathlib.Path('/Users/xyrus/Desktop/llm-smt/clinical-trial-annotation-frontend/private')
target = FE/'clinician_review.json'

data = json.loads(target.read_text())
n_filled = 0
for topic in data['topics']:
    if topic.get('task_id') != 'cf_rewrite_review': continue
    tid = topic['id']
    judg_map = JUDGMENTS.get(tid)
    if not judg_map:
        print(f'  WARN: no judgments for {tid}'); continue
    for rw in topic.get('rewrites', []):
        label = rw.get('label')
        if label not in judg_map: continue
        coh, fl, nb, notes = judg_map[label]
        rw['simclin_coherent'] = coh
        rw['simclin_flips_cited'] = fl
        rw['simclin_new_blocker'] = nb
        rw['simclin_explanation'] = f'[claude pre-judgment] {notes}'
        n_filled += 1

target.write_text(json.dumps(data, indent=2))
print(f'\ninjected {n_filled} simclin_* annotations across the cf_rewrite_review rewrites')
print(f'(admin-only visibility per server.py CF normalization at line ~500)')
