#!/usr/bin/env python3
"""Re-run gpt-5 simclin pairwise verdict with FORCED accept/reject (no uncertain).

Keeps existing pairwise_winner and rationale_axes; only refreshes:
  - clinician_decision (accept|reject — forced binary)
  - clinician_rationale (1–2 sentence why)
  - uncertain_reason (optional "what to check at visit" note — now always populated)
"""
import json, os, pathlib, urllib.request, re
from concurrent.futures import ThreadPoolExecutor, as_completed

FE = pathlib.Path("<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private")

PROMPT = """You are a clinical-trial prescreening clinician. Independently decide whether this patient is likely eligible for this trial. You MUST choose accept or reject — "uncertain" is not allowed. When borderline, commit to the more clinically defensible side.

PATIENT CHART:
{chart}

TRIAL:
{trial}

OUTPUT (STRICT JSON):
{{
  "verdict": "accept" | "reject",
  "brief_reasoning": "<1-2 sentences citing the decisive chart fact and criterion>",
  "what_to_check_at_visit": "<one sentence: a specific item to verify at the in-person prescreening visit, e.g. 'confirm EGFR mutation status'>"
}}
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
pairs  = [t for t in review["topics"] if t.get("sheet")=="formatch_pairwise_review"]
sim    = evals["users"]["simclin_demo"]["clinician_reviews"]
print(f"refreshing {len(pairs)} pairwise verdicts under forced binarization")

def proc(t):
    tid = t["id"]
    chart = (t.get("patient_note") or t.get("original_chart") or "")[:5000]
    trial = t.get("trial_listing","")[:3000]
    try:
        out = gpt5(PROMPT.format(chart=chart, trial=trial))
    except Exception as e:
        return tid, None, None, None
    v = str(out.get("verdict","")).strip().lower()
    if v not in ("accept","reject"): v = "reject"
    return tid, v, (out.get("brief_reasoning","") or "").strip(), (out.get("what_to_check_at_visit","") or "").strip()

with ThreadPoolExecutor(max_workers=8) as ex:
    done = 0
    for fut in as_completed({ex.submit(proc, t): t for t in pairs}):
        tid, v, br, wt = fut.result()
        if v is None: continue
        r = sim.setdefault(tid, {}).setdefault("relevance", {})
        r["clinician_decision"] = v
        if br: r["clinician_rationale"] = br
        if wt: r["uncertain_reason"] = wt  # repurposed as optional "what to check"
        done += 1
        if done % 5 == 0: print(f"  {done}/{len(pairs)}", flush=True)

(FE/"evaluations.json").write_text(json.dumps(evals, indent=2))
from collections import Counter
c = Counter(sim[t["id"]]["relevance"].get("clinician_decision") for t in pairs)
print(f"final verdict mix: {dict(c)}")
print("written")
