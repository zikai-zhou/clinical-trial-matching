#!/usr/bin/env python3
"""Use gpt-5 ('simclin') to fill in clinician judgments for each rewrite in
the cf_rewrite_review topics, then write to evaluations.json under user
'claude_simclin_cell3'.

For each (topic, rewrite), simclin sees:
  - original chart
  - CF chart (modified version)
  - cited blocker rationale the system used
  - trial inclusion + exclusion criteria

It returns:
  cf_coherent       : yes | no | partial
  cf_flipped_cited  : yes | no | partial
  cf_new_blocker    : yes | no
  cf_notes          : 1-2 sentence rationale
"""
import json, os, pathlib, re, sys, urllib.request, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')
FE = pathlib.Path('<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private')

# Load env
env_lines = (ROOT/'.env').read_text().splitlines()
for line in env_lines:
    if '=' not in line or line.strip().startswith('#'): continue
    k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
ENDPOINT_GPT5 = os.environ.get('OPENAI_ENDPOINT_GPT5') or os.environ['OPENAI_ENDPOINT']
KEY = os.environ['OPENAI_API_KEY']
BASE = ENDPOINT_GPT5.split('/openai/')[0]

PROMPT = """You are an expert clinician auditing a chart modification. The matcher had labeled this patient INELIGIBLE for the trial; the system below modified the patient chart to (in principle) flip the eligibility verdict. Your job: judge the QUALITY of the modification.

# Trial inclusion criteria
{inc}

# Trial exclusion criteria
{exc}

# Original patient chart
{orig}

# System's CITED blockers (what the system said was blocking eligibility)
{cited}

# Modified patient chart (the counterfactual)
{cf}

Answer the following STRICTLY in JSON. Use exactly the field names below.

{{
  "cf_coherent":      "yes" | "no" | "partial",     // Is the modified chart clinically coherent? (no internal contradictions, plausible clinical narrative)
  "cf_flipped_cited": "yes" | "no" | "partial",     // Does the modification address ALL cited blockers?
  "cf_new_blocker":   "yes" | "no",                 // Does the modification introduce NEW grounds for ineligibility that weren't present originally?
  "cf_notes":         "<1-2 sentences explaining your judgment>"
}}
"""

def call_gpt5(prompt, max_tokens=900):
    body = {'model':'gpt-5',
            'messages':[{'role':'user','content':prompt}],
            'max_completion_tokens': max_tokens + 4000,
            'response_format':{'type':'json_object'}}
    req = urllib.request.Request(
        f'{BASE}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview',
        data=json.dumps(body).encode(),
        headers={'api-key': KEY, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    return resp['choices'][0]['message']['content'] or ''

def judge_one(args):
    pair, label, system_id, prompt = args
    for attempt in range(3):
        try:
            txt = call_gpt5(prompt)
            o = json.loads(txt)
            # Normalize
            for k in ('cf_coherent','cf_flipped_cited'):
                o[k] = str(o.get(k,'')).strip().lower()
                if o[k] not in ('yes','no','partial'): o[k] = 'partial'
            o['cf_new_blocker'] = str(o.get('cf_new_blocker','')).strip().lower()
            if o['cf_new_blocker'] not in ('yes','no'): o['cf_new_blocker'] = 'no'
            o['cf_notes'] = str(o.get('cf_notes','')).strip()[:600]
            return (pair, label, system_id, o)
        except Exception as e:
            if attempt == 2:
                return (pair, label, system_id, {'cf_coherent':'partial','cf_flipped_cited':'partial','cf_new_blocker':'no','cf_notes':f'[ERR {type(e).__name__}: {str(e)[:100]}]'})

# Build work list from the cf_audit JSON
audit = json.load((ROOT/'experiments/clinician_validation/cf_audit_K7_cell3.json').open())
work = []
for topic in audit['topics']:
    inc = topic.get('trial_inclusion','') or ''
    exc = topic.get('trial_exclusion','') or ''
    orig = topic.get('original_chart','') or ''
    for rw in topic.get('rewrites', []):
        prompt = PROMPT.format(
            inc=inc, exc=exc, orig=orig,
            cited=rw.get('cited_blocker_text','') or '',
            cf=rw.get('cf_chart','') or '')
        work.append((topic['id'], rw['label'], rw['system_blind_id'], prompt))

print(f'judging {len(work)} cells with gpt-5 simclin (8-way parallel)...', flush=True)
results = {}  # (pair, label) -> judgment
done = 0
with ThreadPoolExecutor(max_workers=8) as ex:
    futures = {ex.submit(judge_one, w): w for w in work}
    for fut in as_completed(futures):
        pair, label, system_id, ans = fut.result()
        results[(pair, label)] = (system_id, ans)
        done += 1
        if done % 10 == 0 or done == len(work):
            print(f'  [{done}/{len(work)}] {pair[-30:]} {label} ({system_id}) cf_coh={ans["cf_coherent"]} cf_flip={ans["cf_flipped_cited"]}', flush=True)

# Write to evaluations.json under user 'gpt5_simclin_cell3'
evals = json.load((FE/'evaluations.json').open())
evals.setdefault('users',{}).setdefault('gpt5_simclin_cell3', {'clinician_reviews': {}})
revs = evals['users']['gpt5_simclin_cell3']['clinician_reviews']

now = datetime.now(timezone.utc).isoformat()
for topic in audit['topics']:
    tid = topic['id']
    rewrites = {}
    for rw in topic.get('rewrites', []):
        ans = results.get((tid, rw['label']))
        if not ans: continue
        _, j = ans
        rewrites[rw['label']] = {
            'cf_coherent': j['cf_coherent'],
            'cf_flipped_cited': j['cf_flipped_cited'],
            'cf_new_blocker': j['cf_new_blocker'],
            'cf_notes': f'[gpt-5 simclin] {j["cf_notes"]}',
        }
    revs[tid] = {
        'relevance': {
            'clinician_decision':'', 'clinician_rationale':'', 'pairwise_winner':'',
            'cf_coherent':'', 'cf_flipped_cited':'', 'cf_new_blocker':'', 'cf_notes':'',
            'rewrites': rewrites, 'rationale_axes': {},
        },
        'subcohorts': {}, 'updated_at': now,
    }

(FE/'evaluations.json').write_text(json.dumps(evals, indent=2))
print(f'\nwrote {len(work)} cells across {len(audit["topics"])} topics as gpt5_simclin_cell3 in {FE/"evaluations.json"}')
