#!/usr/bin/env python3
"""Re-run Shah on the 16 pairs affected by the parser-fragility bug.

Loads `build_prompt` + `call` from the fixed shahlab/run.py module and writes
new records to a side jsonl that we then merge into the canonical
rationales.jsonl + verdicts.jsonl.
"""
from __future__ import annotations
import json, os, pathlib, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path("<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored")

# Load the fixed parser from shahlab/run.py (avoiding its main-module imports)
import importlib.util
spec = importlib.util.spec_from_file_location("shah_run", ROOT/"matchers/systems/shahlab/run.py")
# Patch out the offending imports before exec
src = (ROOT/"matchers/systems/shahlab/run.py").read_text()
src_clean = src.replace("from run_better_nl_full import get_pair_inputs, collect_pairs", "")
shah_mod = type(sys)("shah_run")
shah_mod.__dict__["__file__"] = str(ROOT/"matchers/systems/shahlab/run.py")
exec(compile(src_clean, str(ROOT/"matchers/systems/shahlab/run.py"), "exec"), shah_mod.__dict__)
build_prompt = shah_mod.build_prompt

KEY = os.environ.get("OPENAI_API_KEY","")
EP  = os.environ.get("OPENAI_ENDPOINT","")
BASE = EP.split("/openai/")[0] if EP else ""

# Load corpus + patient notes
sigir_corpus = {}
for ln in (ROOT/"dataset/clinical_trial/sigir/corpus.jsonl").open():
    try: r = json.loads(ln)
    except: continue
    sigir_corpus[r["_id"]] = r

def load_notes():
    notes = {}
    for ln in (ROOT/"dataset/clinical_trial/sigir/queries.jsonl").open():
        try: r = json.loads(ln)
        except: continue
        notes[r["_id"]] = r.get("text","")
    return notes
NOTES = load_notes()
print(f"corpus={len(sigir_corpus)}  notes={len(NOTES)}")

_inc_re = re.compile(r"(?i)\binclusion\s+criteria\s*:\s*")
_exc_re = re.compile(r"(?i)\bexclusion\s+criteria\s*:\s*")
def split_inc_exc(text):
    if not text: return "", ""
    im = _inc_re.search(text); em = _exc_re.search(text)
    i = e = ""
    if im: i = text[im.end(): em.start() if em else len(text)].strip()
    if em: e = text[em.end():].strip()
    return i, e

def trial_inputs(pair):
    pid, nct = pair.split("__", 1)
    note = NOTES.get(pid, "")
    rec = sigir_corpus.get(nct)
    if not rec:
        parent = re.sub(r"(?<=NCT\d{8})[a-z]+$", "", nct)
        rec = sigir_corpus.get(parent)
    inc, exc = split_inc_exc(rec.get("text","") if rec else "")
    return note, inc, exc

def llm_call(prompt):
    body = {
        "messages": [{"role":"user","content":prompt}],
        "max_tokens": 3000, "temperature": 0,
        "response_format": {"type":"json_object"},
    }
    req = urllib.request.Request(
        f"{EP}/chat/completions?api-version=2024-08-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key": KEY, "Content-Type":"application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{[\s\S]*\}", txt)
    return json.loads(m.group(0)) if m else None

def render_rationale(assessments, eligibility, global_decision):
    """Recreate the formatted rationale text (matching the existing schema)."""
    inc = [a for a in assessments if "inclusion" in str(a.get("criterion","")).lower()]
    exc = [a for a in assessments if "exclusion" in str(a.get("criterion","")).lower()]
    out = []
    out.append("Inclusion criteria (per-criterion verdict):")
    for a in inc:
        m = "[met]" if a.get("is_met") else "[not met]"
        out.append(f"  - {m} {(a.get('rationale','') or '').strip()}")
    out.append("Exclusion criteria (per-criterion verdict):")
    for a in exc:
        m = "[met]" if a.get("is_met") else "[not met]"
        out.append(f"  - {m} {(a.get('rationale','') or '').strip()}")
    return "\n".join(out)

# Run on the 16 affected pairs
affected = []
for ln in (ROOT/"matchers/systems/shahlab/rationales.jsonl").open():
    r = json.loads(ln)
    if (r.get("n_inc_assessed") or 0) == 0 or (r.get("n_exc_assessed") or 0) == 0:
        affected.append(r["pair"])
print(f"Affected: {len(affected)}")

def proc(pair):
    note, inc, exc = trial_inputs(pair)
    if not note:
        return pair, {"pair": pair, "error": "no_note"}
    prompt = build_prompt(note, inc, exc)
    try:
        res = llm_call(prompt)
    except Exception as e:
        return pair, {"pair": pair, "error": str(e)[:200]}
    if not res:
        return pair, {"pair": pair, "error": "no_parse"}
    gd = res.get("global_decision")
    asmts = res.get("assessments") or []
    n_inc = sum(1 for a in asmts if "inclusion" in str(a.get("criterion","")).lower())
    n_exc = sum(1 for a in asmts if "exclusion" in str(a.get("criterion","")).lower())
    eligible = (gd is not None and gd >= 1)
    return pair, {
        "pair": pair,
        "eligibility": "eligible" if eligible else "ineligible",
        "global_decision": gd,
        "rationale": render_rationale(asmts, "eligible" if eligible else "ineligible", gd),
        "n_inc_assessed": n_inc,
        "n_exc_assessed": n_exc,
        "assessments": asmts[:30],
    }

out_path = ROOT/"matchers/systems/shahlab/rerun_parser_fix.jsonl"
done = 0
with out_path.open("w") as f, ThreadPoolExecutor(max_workers=6) as ex:
    for fut in as_completed({ex.submit(proc, p): p for p in affected}):
        pair, rec = fut.result()
        f.write(json.dumps(rec) + "\n"); f.flush()
        done += 1
        if done % 4 == 0 or done == len(affected):
            print(f"  {done}/{len(affected)}", flush=True)

print(f"\nwrote {out_path}")
