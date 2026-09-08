#!/usr/bin/env python3
"""Re-rejudge Shah's v1 (gpt-4.1 modifier) CFs with the correct Koopman+lenient
binarization. Gives the number the paper SHOULD have published."""
import json, os, pathlib, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")
sys.path.insert(0, str(ROOT/"matchers/systems/shahlab"))
src = (ROOT/"matchers/systems/shahlab/run.py").read_text()
src_clean = src.replace("from run_better_nl_full import get_pair_inputs, collect_pairs","")
shah_mod = type(sys)("shah_run")
shah_mod.__dict__["__file__"] = str(ROOT/"matchers/systems/shahlab/run.py")
exec(compile(src_clean, str(ROOT/"matchers/systems/shahlab/run.py"),"exec"), shah_mod.__dict__)
build_prompt = shah_mod.build_prompt

ep = os.environ["OPENAI_ENDPOINT"]
key = os.environ["OPENAI_API_KEY"]

_inc_re = re.compile(r"(?i)\binclusion\s+criteria\s*:\s*")
_exc_re = re.compile(r"(?i)\bexclusion\s+criteria\s*:\s*")
def split_inc_exc(text):
    if not text: return "",""
    im = _inc_re.search(text); em = _exc_re.search(text)
    i = e = ""
    if im: i = text[im.end(): em.start() if em else len(text)].strip()
    if em: e = text[em.end():].strip()
    return i,e

# Load full SIGIR corpus for trial text
sigir = {}
for ln in (ROOT/"dataset/clinical_trial/sigir/corpus.jsonl").open():
    r = json.loads(ln); sigir[r["_id"]] = r
notes = {}
for ln in (ROOT/"dataset/clinical_trial/sigir/queries.jsonl").open():
    r = json.loads(ln); notes[r["_id"]] = r.get("text","")

# Read v1 Shah CFs
recs = []
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl").open():
    r = json.loads(ln)
    sh = (r.get("systems") or {}).get("shah", {})
    if sh.get("cf_chart") and sh.get("cf_valid"):
        recs.append({"pair": r["pair"], "cf_chart": sh["cf_chart"]})
print(f"v1 Shah CFs (cf_valid=True): {len(recs)}")

def trial_inc_exc(pair):
    _, tid = pair.split("__",1)
    rec = sigir.get(tid) or sigir.get(re.sub(r"(?<=NCT\d{8})[a-z]+$","",tid))
    if not rec: return "",""
    return split_inc_exc(rec.get("text",""))

def shah_call(prompt):
    body = {"messages":[{"role":"user","content":prompt}],"max_tokens":3000,
            "temperature":0,"response_format":{"type":"json_object"}}
    req = urllib.request.Request(
        f"{ep.replace('/openai/deployments/gpt-4.1','')}/openai/deployments/gpt-4.1/chat/completions?api-version=2024-08-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key":key,"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{[\s\S]*\}", txt)
    return json.loads(m.group(0)) if m else {}

def proc(rec):
    pair = rec["pair"]; cf = rec["cf_chart"]
    inc, exc = trial_inc_exc(pair)
    prompt = build_prompt(cf[:5000], inc, exc)
    try:
        res = shah_call(prompt)
    except Exception as e:
        return {"pair":pair,"error":str(e)[:150]}
    gd = res.get("global_decision")
    return {"pair":pair, "global_decision":gd,
            "lenient_eligible_gte1": (gd is not None and gd >= 1),
            "strict_eligible_gte2":  (gd is not None and gd >= 2)}

out = pathlib.Path("/tmp/shah_v1_correct_binarization.jsonl")
done = 0
with out.open("w") as f, ThreadPoolExecutor(max_workers=10) as ex:
    for fut in as_completed({ex.submit(proc, r): r for r in recs}):
        rec = fut.result()
        f.write(json.dumps(rec)+"\n"); f.flush()
        done += 1
        if done % 10 == 0 or done == len(recs):
            print(f"  done {done}/{len(recs)}", flush=True)

# Summarize
results = [json.loads(l) for l in out.open() if l.strip()]
results = [r for r in results if "global_decision" in r]
n = len(results)
lenient = sum(1 for r in results if r["lenient_eligible_gte1"])
strict  = sum(1 for r in results if r["strict_eligible_gte2"])
print(f"\n=== v1 Shah CFs with correct Koopman matcher ===")
print(f"  n: {n}")
print(f"  Lenient gd>=1 (Shah's actual rule): {lenient}/{n} = {lenient/n:.1%}")
print(f"  Strict gd>=2                      : {strict}/{n} = {strict/n:.1%}")
print(f"\nFor comparison:")
print(f"  v1 Shah flip rate (paper, wrong TG binarization): 57.9%")
print(f"  v2 Shah flip rate (gpt-5 modifier, Koopman gd>=1): 94.4%")
