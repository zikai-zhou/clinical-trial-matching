#!/usr/bin/env python3
"""Run the FULL 5-axis simclin prompt on top candidates to find genuine A-wins."""
import json, os, pathlib, re, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')
FE   = pathlib.Path('<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private')

CANDS = ['sigir-201428__NCT01280292','sigir-20159__NCT02105714',
         'sigir-201430__NCT00311805','sigir-20147__NCT00883493','sigir-201527__NCT02441439',
         'sigir-201417__NCT00630513','sigir-201414__NCT01470040',
         'sigir-20141__NCT02001545','sigir-201420__NCT01853735','sigir-201516__NCT01748162',
         'sigir-201522__NCT01178177','sigir-201528__NCT00065806','sigir-201514__NCT02612558',
         'sigir-201521__NCT01521403','sigir-201517__NCT01131312']

def grab(p, pair, key):
    for ln in p.open():
        if f'"{pair}"' in ln:
            return json.loads(ln).get(key)
    return None

def get_cc(pair):
    for ln in (ROOT/'backup/overnight/disagreements_full.jsonl').open():
        if f'"{pair}"' in ln:
            o = json.loads(ln); ne = o['note_excerpt']
            if isinstance(ne,str):
                try: ne = json.loads(ne)
                except: ne = {'text': ne}
            return ne.get('text',str(ne)) if isinstance(ne,dict) else str(ne), o['inc_excerpt'], o['exc_excerpt']
    return None,None,None

PROMPT = """You are a prescreening clinician. Two blinded rationales (A,B) for the same patient-trial pair. Rate each 1-5 on five axes (logical_consistency, chart_traceability, criterion_completeness, support_score, actionability) and pick a winner.

CHART:
{chart}

TRIAL:
{trial}

A:
{a}

B:
{b}

OUTPUT (STRICT JSON):
{{"pairwise_winner":"a"|"b"|"tie",
  "rationale_axes":{{"A":{{"logical_consistency":1-5,"chart_traceability":1-5,"criterion_completeness":1-5,"support_score":1-5,"actionability":1-5}},
                     "B":{{"logical_consistency":1-5,"chart_traceability":1-5,"criterion_completeness":1-5,"support_score":1-5,"actionability":1-5}}}},
  "why":"<one sentence>"}}
"""
def gpt5(prompt):
    ep=os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ["OPENAI_ENDPOINT"]
    base=ep.split("/openai/")[0]; key=os.environ["OPENAI_API_KEY"]
    body={"model":"gpt-5","messages":[{"role":"user","content":prompt}],"max_completion_tokens":3000,"response_format":{"type":"json_object"}}
    req=urllib.request.Request(f"{base}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
        data=json.dumps(body).encode(),headers={"api-key":key,"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=300) as r: resp=json.loads(r.read())
    txt=resp["choices"][0]["message"]["content"] or ""
    try: return json.loads(txt)
    except:
        m=re.search(r"\{[\s\S]*\}",txt); return json.loads(m.group(0)) if m else {}

def proc(pair):
    chart,inc,exc = get_cc(pair)
    if not chart: return pair, None
    a = grab(ROOT/'matchers/systems/aegis/rationales.jsonl', pair, 'verbalized_rationale')
    s = grab(ROOT/'matchers/systems/shahlab/rationales.jsonl', pair, 'rationale')
    if not (a and s): return pair, None
    trial=f"Inclusion criteria: {inc}\nExclusion criteria: {exc}"
    out=gpt5(PROMPT.format(chart=chart[:5000],trial=trial[:3000],a=a[:2500],b=s[:2500]))
    return pair, out

with ThreadPoolExecutor(max_workers=8) as ex:
    rows=[]
    for fut in as_completed({ex.submit(proc,p):p for p in CANDS}):
        pair,out=fut.result()
        if not out: continue
        ax=out.get('rationale_axes',{})
        Aavg = sum(ax.get('A',{}).get(k,0) for k in ['logical_consistency','chart_traceability','criterion_completeness','support_score','actionability'])/5.0
        Bavg = sum(ax.get('B',{}).get(k,0) for k in ['logical_consistency','chart_traceability','criterion_completeness','support_score','actionability'])/5.0
        rows.append((pair, out.get('pairwise_winner',''), Aavg, Bavg, Aavg-Bavg, out.get('why','')[:140]))
rows.sort(key=lambda r:(r[1]!='a', -(r[4])))
print('\npair / winner / A_avg / B_avg / A-B / why')
for r in rows: print(f"  {r[0]:38} {r[1]:4} A={r[2]:.2f} B={r[3]:.2f} dAB={r[4]:+.2f} | {r[5]}")
