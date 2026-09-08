#!/usr/bin/env python3
"""Re-rejudge Shah's CFs with a BINARY-FORCED Koopman prompt (no leniency).

We strip the 'be LENIENT, default to 2' instruction and replace the ternary
{0,1,2} output with binary {eligible, ineligible}. Same model (gpt-4.1),
same chart and trial inputs, same per-criterion assessment structure —
only the leniency-induced middle category removed.
"""
import json, os, pathlib, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")
ep = os.environ["OPENAI_ENDPOINT"]
key = os.environ["OPENAI_API_KEY"]
base = ep.split("/openai/")[0]

_inc_re = re.compile(r"(?i)\binclusion\s+criteria\s*:\s*")
_exc_re = re.compile(r"(?i)\bexclusion\s+criteria\s*:\s*")
def split_inc_exc(text):
    if not text: return "",""
    im = _inc_re.search(text); em = _exc_re.search(text)
    i = e = ""
    if im: i = text[im.end(): em.start() if em else len(text)].strip()
    if em: e = text[em.end():].strip()
    return i,e

sigir = {}
for ln in (ROOT/"dataset/clinical_trial/sigir/corpus.jsonl").open():
    r = json.loads(ln); sigir[r["_id"]] = r

HEADER_RE = re.compile(r'^(?:in|ex)clusion\s+criteria\s*:?\s*', re.IGNORECASE)
def to_list(s):
    out = []
    for p in re.split(r'\n+', (s or '').strip()):
        p = p.strip()
        while True:
            m = HEADER_RE.match(p)
            if not m: break
            p = p[m.end():].strip()
        p = re.sub(r'^[:\-\s]+', '', p).strip()
        if len(p) > 5: out.append(p)
    return out


PROMPT_BINARY = """# Task
Your job is to indicate which of the following inclusion and exclusion criteria are met by the patient, then make a binary eligibility decision.

For an **inclusion** criteria to be "met", the patient must have the condition described in the criteria.
For an **exclusion** criteria to be "met", the patient must NOT have the condition described in the criteria.

# Patient

Below is a clinical note describing the patient's current health status:

```
{note}
```

# Inclusion Criteria

The inclusion criteria being assessed are listed below:
{inc_str}

The exclusion criteria being assessed are listed below:
{exc_str}

# Assessment

For each of the criteria above, use the patient's clinical note to determine whether the patient meets it. Think step by step.

Format your response as a JSON object with:
- "assessments": a list of dicts, each with {{"criterion": str, "rationale": str, "is_met": bool, "confidence": "low|medium|high"}}
- "eligible": bool — true if the patient is eligible for this trial, false if not. Make a definitive binary decision; do NOT use a "maybe" or "uncertain" middle option.

Provide ONLY the JSON object."""


def build_prompt(note, inc_text, exc_text):
    inc_list = to_list(inc_text)
    exc_list = to_list(exc_text)
    inc_str = "\n".join(f"- inclusion_criteria_{i}: {c}" for i,c in enumerate(inc_list))
    exc_str = "\n".join(f"- exclusion_criteria_{i}: {c}" for i,c in enumerate(exc_list))
    return PROMPT_BINARY.format(note=note, inc_str=inc_str, exc_str=exc_str)


def call(prompt):
    body = {"messages":[{"role":"user","content":prompt}],"max_tokens":3000,
            "temperature":0,"response_format":{"type":"json_object"}}
    req = urllib.request.Request(
        f"{base}/openai/deployments/gpt-4.1/chat/completions?api-version=2024-08-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key":key,"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{[\s\S]*\}", txt)
    return json.loads(m.group(0)) if m else {}

def trial_text(pair):
    _, tid = pair.split("__",1)
    rec = sigir.get(tid) or sigir.get(re.sub(r"(?<=NCT\d{8})[a-z]+$","",tid))
    return split_inc_exc(rec.get("text","") if rec else "")

def proc(rec):
    pair = rec["pair"]; cf = rec.get("cf_chart") or rec.get("chart")
    inc, exc = trial_text(pair)
    prompt = build_prompt(cf[:5000], inc, exc)
    try: res = call(prompt)
    except Exception as e: return {"pair":pair,"error":str(e)[:200]}
    elig = res.get("eligible")
    return {"pair":pair, "eligible_binary": bool(elig) if isinstance(elig,bool) else (str(elig).lower() == "true"),
            "raw_eligible": elig}

# === STEP 1: Original verdict on unmodified charts using binary-forced prompt ===
print("=== Step 1: Original Shah verdict (binary-forced) on unmodified charts ===")
notes = {}
for ln in (ROOT/"dataset/clinical_trial/sigir/queries.jsonl").open():
    r = json.loads(ln); notes[r["_id"]] = r.get("text","")

# Use the 178 pairs we have v2 modifier CFs for
import collections
pairs_v2 = set()
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    if r.get("system")=="shah" and r.get("cf_chart"):
        pairs_v2.add(r["pair"])
print(f"v2 Shah CF pairs: {len(pairs_v2)}")

# Original verdict on each pair's UNMODIFIED chart
orig_recs = [{"pair":p, "cf_chart": notes.get(p.split("__")[0], "")} for p in pairs_v2 if notes.get(p.split("__")[0])]
print(f"  with notes: {len(orig_recs)}")

out_orig = pathlib.Path("/tmp/shah_binary_original_v2pool.jsonl")
done = 0
with out_orig.open("w") as f, ThreadPoolExecutor(max_workers=10) as ex:
    for fut in as_completed({ex.submit(proc, r): r for r in orig_recs}):
        rec = fut.result()
        f.write(json.dumps(rec)+"\n"); f.flush()
        done += 1
        if done % 25 == 0 or done == len(orig_recs):
            print(f"    {done}/{len(orig_recs)}", flush=True)

# === STEP 2: Rejudge on v2 CFs using binary-forced prompt ===
print("\n=== Step 2: Shah verdict (binary-forced) on v2 CFs ===")
cf_recs = []
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    if r.get("system")=="shah" and r.get("cf_chart"):
        cf_recs.append({"pair": r["pair"], "cf_chart": r["cf_chart"]})

out_cf = pathlib.Path("/tmp/shah_binary_v2cf.jsonl")
done = 0
with out_cf.open("w") as f, ThreadPoolExecutor(max_workers=10) as ex:
    for fut in as_completed({ex.submit(proc, r): r for r in cf_recs}):
        rec = fut.result()
        f.write(json.dumps(rec)+"\n"); f.flush()
        done += 1
        if done % 25 == 0 or done == len(cf_recs):
            print(f"    {done}/{len(cf_recs)}", flush=True)

# === Step 3: Compute flip rate ===
orig = {json.loads(l)["pair"]: json.loads(l) for l in out_orig.open() if l.strip()}
cf   = {json.loads(l)["pair"]: json.loads(l) for l in out_cf.open() if l.strip()}
shared = set(orig) & set(cf)
orig_inel = [p for p in shared if orig[p].get("eligible_binary") is False]
flipped = [p for p in orig_inel if cf[p].get("eligible_binary") is True]
print(f"\n=== Shah binary-forced FINAL ===")
print(f"  pairs: {len(shared)}")
print(f"  Originally ineligible (binary): {len(orig_inel)}")
print(f"  Flipped to eligible after v2 CF: {len(flipped)}")
print(f"  Flip rate: {len(flipped)/len(orig_inel):.1%}" if orig_inel else "n/a")
