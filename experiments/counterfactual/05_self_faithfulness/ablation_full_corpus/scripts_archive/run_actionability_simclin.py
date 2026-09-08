#!/usr/bin/env python3
"""Run gpt-5 simclin to emit actionability rating per pairwise rationale.

Actionability axis: "do clinicians know what to check for at the prescreening visit?"
One call per pairwise topic, returning {A_actionability, B_actionability} (1-5).
"""
from __future__ import annotations
import json, os, pathlib, urllib.request, re, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")
FE = pathlib.Path("/Users/xyrus/Desktop/llm-smt/clinical-trial-annotation-frontend/private")

PROMPT = """You are a clinical-trial prescreening clinician. You will receive a patient chart, a trial's criteria, and TWO blinded rationale candidates (A and B). For each rationale, rate ACTIONABILITY on a 1–5 Likert scale.

ACTIONABILITY rates: "After reading this rationale, do prescreening clinicians know what specifically to check for or confirm at the in-person visit?"

  1 = no actionable next steps; clinicians left with no specific items to verify or document
  2 = a few vague items; clinicians unsure exactly what to check
  3 = some actionable items but incomplete or buried in prose
  4 = mostly actionable: most decisive criteria have specific verification steps named
  5 = fully actionable: every decisive criterion has a clear, concrete check (with what chart fact or what test/measurement) that the visiting clinician can do

A "decisive criterion" is one that, if confirmed at the visit, would change the prescreen decision (forward vs. defer).

# INPUTS

PATIENT CHART:
{chart}

TRIAL:
{trial}

RATIONALE A:
{rat_a}

RATIONALE B:
{rat_b}

# OUTPUT (STRICT JSON, no commentary outside)

{{
  "rationale_A_actionability": <int 1-5>,
  "rationale_B_actionability": <int 1-5>,
  "actionability_explanation": "<one sentence comparing A vs B on actionability>"
}}
"""


def gpt5(prompt):
    ep = os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ.get("OPENAI_ENDPOINT","")
    base = ep.split("/openai/")[0] if ep else ""
    key = os.environ["OPENAI_API_KEY"]
    body = {"model":"gpt-5","messages":[{"role":"user","content":prompt}],
            "max_completion_tokens":6000,"response_format":{"type":"json_object"}}
    req = urllib.request.Request(
        f"{base}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key":key,"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    try: return json.loads(txt)
    except:
        m = re.search(r"\{[\s\S]*\}", txt)
        return json.loads(m.group(0)) if m else {}


review = json.load((FE/"clinician_review.json").open())
pairwise = [t for t in review["topics"] if t.get("sheet")=="formatch_pairwise_review"]

def process(t):
    tid = t["id"]
    chart = t.get("patient_note") or t.get("original_chart") or ""
    trial = t.get("trial_listing","")
    rat_a = t.get("rationale_A_text","")
    rat_b = t.get("rationale_B_text","")
    prompt = PROMPT.format(chart=chart[:5000], trial=trial[:3000],
                           rat_a=rat_a[:2500], rat_b=rat_b[:2500])
    try:
        out = gpt5(prompt)
    except Exception as e:
        return {"tid":tid, "error":str(e)[:200]}
    return {"tid":tid,
            "A": int(out.get("rationale_A_actionability") or 0) if isinstance(out.get("rationale_A_actionability"),(int,float)) else None,
            "B": int(out.get("rationale_B_actionability") or 0) if isinstance(out.get("rationale_B_actionability"),(int,float)) else None,
            "explanation": (out.get("actionability_explanation","") or "")[:400]}


out_path = pathlib.Path("/tmp/actionability_simclin.jsonl")
done_cache = set()
if out_path.exists():
    for ln in out_path.open():
        try: o = json.loads(ln)
        except: continue
        if "A" in o and "B" in o: done_cache.add(o["tid"])
todo = [t for t in pairwise if t["id"] not in done_cache]
print(f"pairwise total: {len(pairwise)}, cached: {len(done_cache)}, todo: {len(todo)}")

done = 0
with out_path.open("a") as f, ThreadPoolExecutor(max_workers=8) as ex:
    for fut in as_completed({ex.submit(process, t): t for t in todo}):
        rec = fut.result()
        f.write(json.dumps(rec)+"\n"); f.flush()
        done += 1
        if done % 5 == 0 or done == len(todo):
            print(f"  {done}/{len(todo)}", flush=True)

# Apply to evaluations.json
results = {}
for ln in out_path.open():
    try: o = json.loads(ln)
    except: continue
    if o.get("A") and o.get("B"): results[o["tid"]] = (o["A"], o["B"], o.get("explanation",""))

print(f"\nloaded {len(results)} valid actionability ratings; applying to simclin_demo")

evals = json.load((FE/"evaluations.json").open())
sim_revs = evals["users"]["simclin_demo"]["clinician_reviews"]
n_updated = 0
for tid, (a, b, expl) in results.items():
    if tid not in sim_revs: continue
    axes = sim_revs[tid].setdefault("relevance", {}).setdefault("rationale_axes", {})
    axes.setdefault("A", {})["actionability"] = max(1, min(5, int(a)))
    axes.setdefault("B", {})["actionability"] = max(1, min(5, int(b)))
    n_updated += 1
(FE/"evaluations.json").write_text(json.dumps(evals, indent=2))
print(f"updated {n_updated} pairwise topics with real gpt-5 actionability ratings")
