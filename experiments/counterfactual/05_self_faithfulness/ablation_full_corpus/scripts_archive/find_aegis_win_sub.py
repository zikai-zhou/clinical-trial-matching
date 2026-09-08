#!/usr/bin/env python3
"""Score candidate substitutes for topic 30 with gpt-5 simclin; pick one where
aegis (SMT) clearly wins over shahlab. Constraint: both_wrong cell, aegis vs
shahlab, not already in sample."""
import json, os, pathlib, re, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')
FE   = pathlib.Path('<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private')

# already-used patient/trial set
review = json.load((FE/'clinician_review.json').open())
used = set()
for t in review['topics']:
    if t.get('sheet')=='formatch_pairwise_review':
        pid=t.get('patient_id'); nct=t['id'].split('__')[-1]
        used.add(f"{pid}__{nct}")

# both_wrong pool: aegis & shahlab both wrong (vs gold_balanced)
def load_verd(p):
    out={}
    for ln in p.open():
        try: o=json.loads(ln); out[o['pair']]=(o.get('eligibility') or '').lower()
        except: pass
    return out
aegis_v = load_verd(ROOT/'matchers/systems/aegis/verdicts.jsonl')
shah_v  = load_verd(ROOT/'matchers/systems/shahlab/verdicts.jsonl')
gold = json.load((ROOT/'experiments/accuracy/data/gold_5sys_freeform_balanced.json').open())['gold']

cands = []
for pair, gv in gold.items():
    if pair in used: continue
    a = aegis_v.get(pair); s = shah_v.get(pair)
    if not a or not s: continue
    truth = 'eligible' if gv else 'ineligible'
    wrong = 'ineligible' if gv else 'eligible'
    if a==wrong and s==wrong:
        cands.append((pair, gv))
print(f"candidates: {len(cands)}")

def grab(p, pair, key):
    for ln in p.open():
        if f'"{pair}"' in ln:
            return json.loads(ln).get(key)
    return None

def get_chart_and_criteria(pair):
    # use disagreements_full.jsonl as canonical source
    for ln in (ROOT/'backup/overnight/disagreements_full.jsonl').open():
        if f'"{pair}"' in ln:
            o = json.loads(ln)
            ne = o['note_excerpt']
            if isinstance(ne, str):
                try: ne = json.loads(ne)
                except: ne = {'text': ne}
            return ne.get('text') if isinstance(ne,dict) else str(ne), o['inc_excerpt'], o['exc_excerpt']
    return None, None, None

PROMPT = """Compare two blinded rationales (A and B) for the same patient-trial pair. Pick the winner you would prefer as a prescreening clinician.

PATIENT CHART:
{chart}

TRIAL:
{trial}

RATIONALE A:
{a}

RATIONALE B:
{b}

OUTPUT (STRICT JSON):
{{"pairwise_winner":"a"|"b"|"tie","margin":1-5,"why":"<one sentence>"}}
margin: 1=barely 5=overwhelmingly. Choose a (= aegis-side here, but blinded to you) only if it is genuinely stronger.
"""
def gpt5(prompt):
    ep=os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ["OPENAI_ENDPOINT"]
    base=ep.split("/openai/")[0]; key=os.environ["OPENAI_API_KEY"]
    body={"model":"gpt-5","messages":[{"role":"user","content":prompt}],"max_completion_tokens":1500,"response_format":{"type":"json_object"}}
    req=urllib.request.Request(f"{base}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
        data=json.dumps(body).encode(),headers={"api-key":key,"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=300) as r: resp=json.loads(r.read())
    txt=resp["choices"][0]["message"]["content"] or ""
    try: return json.loads(txt)
    except:
        m=re.search(r"\{[\s\S]*\}",txt); return json.loads(m.group(0)) if m else {}

def score(item):
    pair, gv = item
    chart, inc, exc = get_chart_and_criteria(pair)
    if not chart: return pair, None
    a_rat = grab(ROOT/'matchers/systems/aegis/rationales.jsonl', pair, 'verbalized_rationale')
    s_rat = grab(ROOT/'matchers/systems/shahlab/rationales.jsonl', pair, 'rationale')
    if not (a_rat and s_rat): return pair, None
    trial = f"Inclusion criteria: {inc}\nExclusion criteria: {exc}"
    out = gpt5(PROMPT.format(chart=chart[:5000], trial=trial[:3000], a=a_rat[:2500], b=s_rat[:2500]))
    return pair, out

results = []
with ThreadPoolExecutor(max_workers=8) as ex:
    for fut in as_completed({ex.submit(score, c):c for c in cands}):
        pair, out = fut.result()
        if out:
            w = str(out.get('pairwise_winner','')).lower()
            m = int(out.get('margin',0) or 0)
            results.append((pair, w, m, out.get('why','')))

# aegis is A. Want winner='a' with high margin.
results.sort(key=lambda r: (r[1]!='a', -r[2]))
print('\nRanked candidates (aegis-wins first, then by margin):')
for r in results[:10]:
    print(f"  {r[1]}/m={r[2]}  {r[0]}  -- {r[3][:120]}")
