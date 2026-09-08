#!/usr/bin/env python3
"""Re-judge v5's v2 CFs with the V5_VERBOSE_AGG prompt (chain-of-thought).

Tests whether explicit per-criterion reasoning closes the decorative-rationale
gap. Same gpt-4.1 backbone, same v2 CFs, but the prompt asks the matcher to
walk through every criterion before committing.

Step 1: Verbose verdict on ORIGINAL chart → identifies which pairs are
        originally ineligible under the verbose prompt.
Step 2: Verbose verdict on v2 CF chart → checks if the verdict flips.
"""
import json, os, pathlib, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")
ep = os.environ["OPENAI_ENDPOINT"]
key = os.environ["OPENAI_API_KEY"]
base = ep.split("/openai/")[0]

PROMPT = (ROOT/"matchers/systems/single_shot_llm/prompts/V5_VERBOSE_AGG.prompt").read_text()

def call(prompt):
    body = {"messages":[{"role":"user","content":prompt}],"max_tokens":3500,
            "temperature":0,"response_format":{"type":"json_object"}}
    req = urllib.request.Request(
        f"{base}/openai/deployments/gpt-4.1/chat/completions?api-version=2024-08-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key":key,"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{[\s\S]*\}", txt)
    return json.loads(m.group(0)) if m else {}

# Load trials + notes
notes = {}
for ln in (ROOT/"dataset/clinical_trial/sigir/queries.jsonl").open():
    r = json.loads(ln); notes[r["_id"]] = r.get("text","")
sigir = {}
for ln in (ROOT/"dataset/clinical_trial/sigir/corpus.jsonl").open():
    r = json.loads(ln); sigir[r["_id"]] = r

_inc_re = re.compile(r"(?i)\binclusion\s+criteria\s*:\s*")
_exc_re = re.compile(r"(?i)\bexclusion\s+criteria\s*:\s*")
def split_inc_exc(text):
    if not text: return "",""
    im = _inc_re.search(text); em = _exc_re.search(text)
    i = e = ""
    if im: i = text[im.end(): em.start() if em else len(text)].strip()
    if em: e = text[em.end():].strip()
    return i,e
def trial_text(pair):
    _, tid = pair.split("__",1)
    rec = sigir.get(tid) or sigir.get(re.sub(r"(?<=NCT\d{8})[a-z]+$","",tid))
    if not rec: return ""
    inc, exc = split_inc_exc(rec.get("text",""))
    return f"INCLUSION:\n{inc}\n\nEXCLUSION:\n{exc}"

def proc(rec):
    pair = rec["pair"]; chart = rec["chart"]
    prompt = PROMPT.replace("{{CHART}}", chart[:5000]).replace("{{TRIAL}}", trial_text(pair)[:5000])
    try: res = call(prompt)
    except Exception as e: return {"pair":pair,"error":str(e)[:200]}
    return {"pair":pair, "eligibility": (res.get("eligibility") or "unknown").lower(),
            "aggregation_reasoning": (res.get("aggregation_reasoning") or "")[:500]}

# v5's v2 CFs
cf_recs = []
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    if r.get("system")=="v5" and r.get("cf_chart"):
        cf_recs.append({"pair": r["pair"], "chart": r["cf_chart"]})
print(f"v5 v2 CF pool: {len(cf_recs)}")

# Step 1: original verbose verdict
print("\n=== Step 1: V5_VERBOSE on UNMODIFIED charts ===")
orig_recs = [{"pair":r["pair"], "chart": notes.get(r["pair"].split("__")[0], "")} for r in cf_recs]
orig_recs = [r for r in orig_recs if r["chart"]]
print(f"  with notes: {len(orig_recs)}")
out_orig = pathlib.Path("/tmp/v5verbose_original.jsonl")
done = 0
with out_orig.open("w") as f, ThreadPoolExecutor(max_workers=10) as ex:
    for fut in as_completed({ex.submit(proc, r): r for r in orig_recs}):
        rec = fut.result()
        f.write(json.dumps(rec)+"\n"); f.flush()
        done += 1
        if done % 25 == 0 or done == len(orig_recs):
            print(f"    {done}/{len(orig_recs)}", flush=True)

# Step 2: rejudge on v2 CFs
print("\n=== Step 2: V5_VERBOSE on v2 CFs ===")
out_cf = pathlib.Path("/tmp/v5verbose_v2cf.jsonl")
done = 0
with out_cf.open("w") as f, ThreadPoolExecutor(max_workers=10) as ex:
    for fut in as_completed({ex.submit(proc, r): r for r in cf_recs}):
        rec = fut.result()
        f.write(json.dumps(rec)+"\n"); f.flush()
        done += 1
        if done % 25 == 0 or done == len(cf_recs):
            print(f"    {done}/{len(cf_recs)}", flush=True)

# Step 3: compute flip rate
orig = {json.loads(l)["pair"]: json.loads(l) for l in out_orig.open() if l.strip()}
cf   = {json.loads(l)["pair"]: json.loads(l) for l in out_cf.open() if l.strip()}
shared = set(orig) & set(cf)
orig_inel = [p for p in shared if orig[p].get("eligibility") == "ineligible"]
flipped = [p for p in orig_inel if cf[p].get("eligibility") == "eligible"]
print(f"\n=== V5_VERBOSE on v2 CFs — FINAL ===")
print(f"  pairs: {len(shared)}")
print(f"  Originally ineligible (verbose): {len(orig_inel)}")
print(f"  Flipped to eligible after v2 CF: {len(flipped)}")
print(f"  Flip rate: {len(flipped)/len(orig_inel):.1%}" if orig_inel else "n/a")
print(f"\nReference:")
print(f"  v5 (single-shot, no reasoning)  : 29.2%  (gpt-4.1)")
print(f"  SMT                              : 90.3%")
