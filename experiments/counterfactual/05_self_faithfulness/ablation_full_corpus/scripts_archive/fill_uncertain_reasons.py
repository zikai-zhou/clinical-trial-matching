#!/usr/bin/env python3
"""Backfill uncertain_reason for simclin_demo pairwise entries where verdict=uncertain."""
import json, os, pathlib, urllib.request, re
from concurrent.futures import ThreadPoolExecutor, as_completed

FE = pathlib.Path("<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private")

PROMPT = """You are a clinical-trial prescreening clinician. You reviewed the chart and trial below and were UNCERTAIN whether the patient is eligible. In 1–2 sentences, state concretely what you would need to check or confirm at the in-person visit to decide. Be specific (which chart fact, which test/measurement).

PATIENT CHART:
{chart}

TRIAL:
{trial}

OUTPUT (STRICT JSON):
{{"uncertain_reason": "<1-2 sentences>"}}
"""

def gpt5(prompt):
    ep = os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ["OPENAI_ENDPOINT"]
    base = ep.split("/openai/")[0]
    key = os.environ["OPENAI_API_KEY"]
    body = {"model":"gpt-5","messages":[{"role":"user","content":prompt}],
            "max_completion_tokens":2000,"response_format":{"type":"json_object"}}
    req = urllib.request.Request(
        f"{base}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key":key,"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    try: return json.loads(txt)
    except:
        m = re.search(r"\{[\s\S]*\}", txt)
        return json.loads(m.group(0)) if m else {}

review = json.load((FE/"clinician_review.json").open())
evals  = json.load((FE/"evaluations.json").open())
pairs  = {t["id"]:t for t in review["topics"] if t.get("sheet")=="formatch_pairwise_review"}
sim    = evals["users"]["simclin_demo"]["clinician_reviews"]

todo = []
for tid, t in pairs.items():
    r = sim.get(tid,{}).get("relevance",{})
    if r.get("clinician_decision")=="uncertain" and not (r.get("uncertain_reason") or "").strip():
        todo.append((tid, t))
print(f"need uncertain_reason for {len(todo)} simclin topics")

def proc(item):
    tid, t = item
    chart = (t.get("patient_note") or t.get("original_chart") or "")[:5000]
    trial = t.get("trial_listing","")[:3000]
    try:
        out = gpt5(PROMPT.format(chart=chart, trial=trial))
        return tid, (out.get("uncertain_reason","") or "").strip()
    except Exception as e:
        return tid, f"[error: {str(e)[:80]}]"

with ThreadPoolExecutor(max_workers=6) as ex:
    for fut in as_completed({ex.submit(proc, it):it for it in todo}):
        tid, reason = fut.result()
        if reason and not reason.startswith("[error"):
            sim[tid].setdefault("relevance",{})["uncertain_reason"] = reason
            print(f"  {tid}: {reason[:80]}...")

(FE/"evaluations.json").write_text(json.dumps(evals, indent=2))
print("written")
