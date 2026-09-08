#!/usr/bin/env python3
"""Build clinician_review.json for the 34-pair K=10 CF audit sample.

Each topic = one (patient, trial) pair with multiple system rewrites:
  - SMT (typed-atom modifier from gpt-5)
  - v5 (free-text modifier from gpt-4.1's V5 rationale)
  - v5_blockers (free-text modifier from gpt-4.1's V5_TWO_STEP_BLOCKERS rationale)
  - TG (free-text modifier from TrialGPT's blocker_lines)
  - Shah-binary (free-text modifier from Shah's per-criterion rationale)
  - v5_gpt5 (free-text modifier from gpt-5's V5 rationale) — in-family

Note: V5_VERBOSE is a matcher-only variant that re-judges v5's modifier output,
so it has no distinct CF and is omitted from the modifier-quality audit."""
from __future__ import annotations
import json, pathlib, re, sys, difflib

ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")
FE_PRIVATE = pathlib.Path("/Users/xyrus/Desktop/llm-smt/clinical-trial-annotation-frontend/private")

# Load the sample
sample = json.load(open("/tmp/cf_audit_v2_final_K10.json"))
pairs = sample["pairs"]
print(f"Loading {len(pairs)} pairs from K=10 final sample")

# Load SIGIR corpus + queries
sigir = {}
for ln in (ROOT/"dataset/clinical_trial/sigir/corpus.jsonl").open():
    r = json.loads(ln); sigir[r["_id"]] = r
notes = {}
for ln in (ROOT/"dataset/clinical_trial/sigir/queries.jsonl").open():
    r = json.loads(ln); notes[r["_id"]] = r.get("text","")

_inc_re = re.compile(r"(?i)\binclusion\s+criteria\s*:\s*")
_exc_re = re.compile(r"(?i)\bexclusion\s+criteria\s*:\s*")
def split_inc_exc(text):
    if not text: return "",""
    im = _inc_re.search(text); em = _exc_re.search(text)
    i = e = ""
    if im: i = text[im.end(): em.start() if em else len(text)].strip()
    if em: e = text[em.end():].strip()
    return i,e

def trial_info(tid):
    rec = sigir.get(tid) or sigir.get(re.sub(r"(?<=NCT\d{8})[a-z]+$","",tid))
    if not rec: return "", "", ""
    text = rec.get("text","")
    summary = text.split("Inclusion criteria:")[0].strip().replace("Summary:","").strip() if "Inclusion criteria:" in text else text[:1000]
    inc, exc = split_inc_exc(text)
    return summary, inc, exc

# Load all system CFs
def load_jsonl_by_pair_system(path, key_filter=None):
    out = {}
    for ln in pathlib.Path(path).open():
        if not ln.strip(): continue
        r = json.loads(ln)
        if key_filter and r.get("system") != key_filter: continue
        k = (r.get("pair"), r.get("system") or key_filter)
        out[k] = r
    return out

smt_cfs = {}
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_smt_gpt5modifier.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    if r.get("cf_chart"):
        smt_cfs[r["pair"]] = r

# Baselines (v5, v5_blockers, tg, shah, v5_gpt5)
baseline_cfs = {}
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    if r.get("cf_chart"):
        baseline_cfs[(r["pair"], r["system"])] = r

# Rejudged verdicts (cf_eligibility_under_v2)
rejudge_verdicts = {}
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier_rejudged.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    rejudge_verdicts[(r["pair"], r["system"])] = r.get("cf_eligibility_under_v2")

# Shah-binary rejudges (for verdict info)
shah_bin_cf = {}
for ln in pathlib.Path("/tmp/shah_binary_v2cf.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    shah_bin_cf[r["pair"]] = r

# Original cited rationale per system (loaded from main self_faithfulness.jsonl + external)
sf_main = {}
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    sf_main[r["pair"]] = r

# External rationale files for systems that don't carry it inline
ext_rationales = {}
for sys_id, path in [("v5", ROOT/"matchers/systems/single_shot_llm/rationales.jsonl"),
                     ("v5_blockers", ROOT/"matchers/systems/single_shot_llm/rationales.jsonl"),
                     ("tg", ROOT/"matchers/systems/trialgpt/rationales.jsonl"),
                     ("shah", ROOT/"matchers/systems/shahlab/rationales.jsonl")]:
    if path.exists():
        for ln in path.open():
            try: o = json.loads(ln)
            except: continue
            p = o.get("pair")
            if p: ext_rationales[(p, sys_id)] = o.get("rationale") or o.get("explanation","")

# v5_gpt5 rationale
v5g5_rat = {}
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_v5gpt5.jsonl").open():
    if not ln.strip(): continue
    try: r = json.loads(ln)
    except: continue
    if r.get("cited_rationale"):
        v5g5_rat[r["pair"]] = r["cited_rationale"]

def cited_for_system(pair, system_id):
    if system_id == "aegis":
        # SMT — get targets
        a = (sf_main.get(pair, {}).get("systems") or {}).get("aegis", {})
        targets = a.get("targets", [])
        if not targets: return "(no MaxSat targets recorded)"
        return "Cited symbolic atoms (MaxSat min-flip set):\n" + "\n".join(
            f"- atom `{t.get('atom','')}`: current={t.get('current_value')} → target={t.get('target_value')}"
            for t in targets[:10])
    if system_id == "v5_gpt5":
        return v5g5_rat.get(pair, "(no rationale)")[:1500]
    # Try inline first
    info = (sf_main.get(pair, {}).get("systems") or {}).get(system_id, {})
    cr = (info.get("cited_rationale") or "").strip()
    if cr: return cr[:1500]
    bl = info.get("blocker_lines") or []
    if bl: return "\n".join(str(x) for x in bl[:10])[:1500]
    # Fall back to external
    return (ext_rationales.get((pair, system_id), "") or "(no rationale)")[:1500]

# Build word-level diff
def word_diff(orig, cf, max_segs=40):
    toks_a = re.findall(r"\S+|\s+", orig or "")
    toks_b = re.findall(r"\S+|\s+", cf or "")
    sm = difflib.SequenceMatcher(a=toks_a, b=toks_b, autojunk=False)
    segs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            segs.append({"type":"equal","text":"".join(toks_a[i1:i2])})
        elif tag == "delete":
            segs.append({"type":"del","text":"".join(toks_a[i1:i2])})
        elif tag == "insert":
            segs.append({"type":"add","text":"".join(toks_b[j1:j2])})
        elif tag == "replace":
            segs.append({"type":"del","text":"".join(toks_a[i1:i2])})
            segs.append({"type":"add","text":"".join(toks_b[j1:j2])})
    return segs


def make_rewrite(pair, system_id, label, system_display):
    """Build one rewrite entry for a topic."""
    pid, tid = pair.split("__", 1)
    orig_chart = notes.get(pid, "")
    if system_id == "aegis":
        rec = smt_cfs.get(pair)
        if not rec: return None
        cf_chart = rec.get("cf_chart","")
        cited = cited_for_system(pair, "aegis")
        verdict = "ineligible→eligible (Z3 mechanical flip)" if rec.get("simclin_flips_cited") else "ineligible→ineligible (atoms not fully flipped)"
    else:
        rec = baseline_cfs.get((pair, system_id))
        if not rec: return None
        cf_chart = rec.get("cf_chart","")
        cited = cited_for_system(pair, system_id)
        if system_id == "shah":
            sbin = shah_bin_cf.get(pair, {})
            ver_bool = sbin.get("eligible_binary")
            verdict = ("ineligible→eligible (binary-forced)" if ver_bool else "ineligible→ineligible (binary-forced)") if ver_bool is not None else "?"
        else:
            ver = rejudge_verdicts.get((pair, system_id)) or "?"
            verdict = f"ineligible→{ver}"

    return {
        "label": label,
        "system_blind_id": system_id,
        "system_display": system_display,
        "cf_chart": cf_chart,
        "cited_blocker_text": cited,
        "system_verdict": verdict,
        "diff_segments": word_diff(orig_chart[:6000], cf_chart[:6000]),
    }


# Build topics
topics = []
display_idx = 1
for pair in pairs:
    pid, tid = pair.split("__", 1)
    summary, inc, exc = trial_info(tid)
    orig_chart = notes.get(pid, "")
    if not orig_chart:
        print(f"  WARN: no note for {pid}, skipping")
        continue

    rewrites = []
    SYSTEMS = [
        ("aegis",       "A", "SMT"),
        ("v5",          "B", "Single Shot LLM (gpt-4.1)"),
        ("v5_blockers", "C", "Single Shot+grounds (gpt-4.1)"),
        ("tg",          "D", "TrialGPT (gpt-4.1)"),
        ("shah",        "E", "Shah (gpt-4.1)"),
        ("v5_gpt5",     "F", "Single Shot LLM (gpt-5)"),
    ]
    for sys_id, label, disp in SYSTEMS:
        rw = make_rewrite(pair, sys_id, label, disp)
        if rw: rewrites.append(rw)

    if not rewrites:
        print(f"  WARN: no rewrites for {pair}, skipping")
        continue

    topics.append({
        "id": f"cf_v2_audit__{display_idx:02d}__{pair}",
        "sheet": "cf_rewrite_review",
        "task_id": "cf_rewrite_review",
        "display_index": display_idx,
        "patient_id": pid,
        "trial_id": tid,
        "original_chart": orig_chart,
        "trial_listing": f"Summary: {summary}\nInclusion criteria: {inc}\nExclusion criteria: {exc}",
        "trial_inclusion": inc,
        "trial_exclusion": exc,
        "rewrites": rewrites,
    })
    display_idx += 1

print(f"Built {len(topics)} CF audit topics ({sum(len(t['rewrites']) for t in topics)} total system-audit cells)")

# Wrap into clinician_review schema
out = {"topics": topics, "tasks": [{"task_id":"cf_rewrite_review","title":"Counterfactual Modification Validity Experiments"}]}

# Backup existing & write new
target = FE_PRIVATE/"clinician_review.json"
backup = FE_PRIVATE/"clinician_review.pre_v2_audit.json.bak"
if target.exists() and not backup.exists():
    import shutil
    shutil.copy(target, backup)
    print(f"Backed up existing to {backup}")
target.write_text(json.dumps(out, indent=2))
print(f"Wrote {target}")
