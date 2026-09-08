#!/usr/bin/env python3
"""Generic revalidator: read any self_faithfulness*.jsonl, re-validate every
(pair, system) cell's cf_chart with the v3 gpt-5 simclin prompt."""
import json, os, pathlib, re, sys, urllib.request, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

ap = argparse.ArgumentParser()
ap.add_argument('--input',  required=True)
ap.add_argument('--output', required=True)
ap.add_argument('--label',  default='Snapshot')
args = ap.parse_args()

HERE = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored')
sys.path.insert(0, str(HERE/'experiments/counterfactual/utils'))
import cf_dataset as ds

SIMCLIN_PROMPT = """You are a senior clinical-trial coordinator at the PRESCREEN stage.

ORIGINAL CHART:
{original_chart}

MODIFIED CHART (counterfactual):
{cf_chart}

INCLUSION CRITERIA:
{inclusion}

EXCLUSION CRITERIA:
{exclusion}

CITED BLOCKERS the modifier was asked to address:
{cited_block}

Return strict JSON:
{{
  "coherent":          true|false
  "flips_target_atom": true|false
  "keeps_other_facts": true|false
  "oracle_should_flip":true|false
  "explanation":       "<one sentence justification>"
}}
"""
EP5 = os.environ.get('OPENAI_ENDPOINT_GPT5') or os.environ.get('OPENAI_ENDPOINT','')
KEY = os.environ.get('OPENAI_API_KEY','')
BASE = EP5.split('/openai/')[0] if EP5 else ''

def call_gpt5(prompt):
    body = {'model':'gpt-5','messages':[{'role':'user','content':prompt}],
            'max_completion_tokens':2500,'response_format':{'type':'json_object'}}
    req = urllib.request.Request(
        f"{BASE}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
        data=json.dumps(body).encode(),
        headers={'api-key':KEY,'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=300) as r: resp=json.loads(r.read())
    txt = resp['choices'][0]['message']['content'] or ''
    try: return json.loads(txt)
    except:
        m = re.search(r'\{[\s\S]*\}', txt)
        return json.loads(m.group(0)) if m else {}

charts = ds.load_charts(); trials = ds.load_trial_text()
inc_re = re.compile(r'(?i)\binclusion\s+criteria\s*:\s*'); exc_re = re.compile(r'(?i)\bexclusion\s+criteria\s*:\s*')

records_in = [json.loads(l) for l in pathlib.Path(args.input).open() if l.strip()]
print(f'{args.label}: input {args.input} ({len(records_in)} pairs)', flush=True)

def revalidate(item):
    pair, system, sd = item
    cf = sd.get('cf_chart','')
    if not cf or sd.get('skipped'): return pair, system, None
    pid, nct = pair.split('__',1); base = re.sub(r'[a-z]+$','',nct)
    chart = charts.get(pid,''); trial = trials.get(base,'')
    im = inc_re.search(trial); em = exc_re.search(trial)
    inc = trial[im.end(): em.start() if em else len(trial)].strip() if im else ''
    exc = trial[em.end():].strip() if em else ''
    cited = json.dumps(sd.get('targets')) if system=='aegis' else (sd.get('cited_rationale') or sd.get('rationale','') or '')
    prompt = SIMCLIN_PROMPT.format(
        original_chart=chart[:5000], cf_chart=cf[:5000],
        inclusion=inc[:3000], exclusion=exc[:3000],
        cited_block=str(cited)[:2500])
    err = 'empty after retries'
    for _ in range(3):
        try:
            r = call_gpt5(prompt)
            if isinstance(r, dict) and 'coherent' in r: return pair, system, r
        except Exception as e: err = str(e)[:160]
    return pair, system, {'error': err}

work = []
for rec in records_in:
    pair = rec['pair']
    for system, sd in (rec.get('systems') or {}).items():
        if sd and not sd.get('skipped') and sd.get('cf_chart'):
            work.append((pair, system, sd))
print(f'cells to re-validate: {len(work)}', flush=True)

results = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    n = 0
    for fut in as_completed({ex.submit(revalidate, w): w for w in work}):
        pair, system, val = fut.result()
        results[(pair,system)] = val
        n += 1
        if n % 10 == 0 or n == len(work):
            print(f'  [{n}/{len(work)}]', flush=True)

out_records = []
for rec in records_in:
    new = {'pair': rec['pair'], 'systems': {}}
    for system, sd in (rec.get('systems') or {}).items():
        if not sd or sd.get('skipped'):
            new['systems'][system] = sd; continue
        v3val = results.get((rec['pair'], system), {})
        new_valid = (v3val.get("coherent") is True and v3val.get("flips_target_atom") is True and v3val.get("keeps_other_facts") is True)
        new['systems'][system] = {**sd,
            'v3_validator': v3val,
            'cf_valid_under_v3_validator': new_valid,
            'flipped_under_v3_validator': new_valid and sd.get('flipped', False)}
    out_records.append(new)

OUT = pathlib.Path(args.output)
with OUT.open('w') as f:
    for r in out_records: f.write(json.dumps(r)+'\n')
print(f'\nwrote {OUT}')
