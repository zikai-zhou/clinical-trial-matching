#!/usr/bin/env python3
"""Build K=7 CF audit: 4 systems, 19 pairs, 56 audit cells, max-reuse greedy."""
from __future__ import annotations
import json, pathlib, re, sys, difflib, shutil
from collections import defaultdict

ROOT = pathlib.Path("<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored")
FE_PRIVATE = pathlib.Path("<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private")

# === 1. Load per-system flip maps ===
def get_flips_from_rejudge(system):
    out = {}; mod = {}
    for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl").open():
        if not ln.strip(): continue
        r = json.loads(ln); mod[(r["pair"], r["system"])] = r
    for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier_rejudged.jsonl").open():
        if not ln.strip(): continue
        r = json.loads(ln)
        if r["system"] != system: continue
        if not mod.get((r["pair"], system), {}).get("cf_chart"): continue
        v = r.get("cf_eligibility_under_v2")
        if v in ("eligible","ineligible"): out[r["pair"]] = (v == "eligible")
    return out

smt = {}
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_smt_gpt5modifier.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    if r.get("cf_chart"): smt[r["pair"]] = bool(r.get("simclin_flips_cited"))
v5 = get_flips_from_rejudge("v5")
tg = get_flips_from_rejudge("tg")
shah_orig = {json.loads(l)["pair"]: json.loads(l).get("eligible_binary") for l in pathlib.Path("/tmp/shah_binary_original_v2pool.jsonl").open() if l.strip()}
shah_cf  = {json.loads(l)["pair"]: json.loads(l).get("eligible_binary") for l in pathlib.Path("/tmp/shah_binary_v2cf.jsonl").open() if l.strip()}
shah_bin = {p: bool(shah_cf[p]) for p in set(shah_orig)&set(shah_cf) if shah_orig[p] is False}

systems = [("SMT", smt), ("TG", tg), ("v5", v5), ("Shah", shah_bin)]
sys_names = [n for n,_ in systems]

# === 2. Greedy max-reuse selection at K=7 ===
import random; random.seed(42)
K = 7
shared = set.intersection(*(set(d.keys()) for _, d in systems))
target = {(s,c):K for s in sys_names for c in ("flipped","nonflipped")}
filled = {(s,c):0 for s in sys_names for c in ("flipped","nonflipped")}
pair_buckets = {p: [(s, "flipped" if d[p] else "nonflipped") for s,d in systems] for p in shared}
chosen = []; avail = set(shared)
while True:
    needed = {bk: target[bk]-filled[bk] for bk in target if filled[bk] < target[bk]}
    if not needed: break
    best, best_score = None, -1
    for p in avail:
        score = sum(1 for bk in pair_buckets[p] if bk in needed)
        if score > best_score: best, best_score = p, score
    if best_score == 0: break
    chosen.append(best); avail.discard(best)
    for bk in pair_buckets[best]:
        if filled[bk] < target[bk]: filled[bk] += 1
print(f"Chosen {len(chosen)} pairs at K={K}; coverage:")
for s in sys_names:
    print(f"  {s:10s} F={filled[(s,'flipped')]}/{K} NF={filled[(s,'nonflipped')]}/{K}")

# === 3. Load auxiliary data for topic building ===
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
    return i, e

def trial_info(tid):
    rec = sigir.get(tid) or sigir.get(re.sub(r"(?<=NCT\d{8})[a-z]+$","",tid))
    if not rec: return "","",""
    text = rec.get("text","")
    summary = text.split("Inclusion criteria:")[0].replace("Summary:","").strip() if "Inclusion criteria:" in text else text[:1000]
    inc, exc = split_inc_exc(text)
    return summary, inc, exc

smt_cfs = {p: r for r in [json.loads(l) for l in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_smt_gpt5modifier.jsonl").open() if l.strip()] for p in [r["pair"]] if r.get("cf_chart")}
baseline_cfs = {(r["pair"], r["system"]): r for r in [json.loads(l) for l in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl").open() if l.strip()] if r.get("cf_chart")}
rejudge_verdicts = {(r["pair"], r["system"]): r.get("cf_eligibility_under_v2") for r in [json.loads(l) for l in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier_rejudged.jsonl").open() if l.strip()]}

sf_main = {r["pair"]: r for r in [json.loads(l) for l in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl").open() if l.strip()]}
ext_rationales = {}
for sys_id, path in [("v5", ROOT/"matchers/systems/single_shot_llm/rationales.jsonl"),
                     ("tg", ROOT/"matchers/systems/trialgpt/rationales.jsonl"),
                     ("shah", ROOT/"matchers/systems/shahlab/rationales.jsonl")]:
    if path.exists():
        for ln in path.open():
            try: o = json.loads(ln)
            except: continue
            if o.get("pair"):
                ext_rationales[(o["pair"], sys_id)] = (o.get("rationale") or o.get("explanation","") or "")

def cited_for_system(pair, system_id):
    if system_id == "aegis":
        a = (sf_main.get(pair, {}).get("systems") or {}).get("aegis", {})
        targets = a.get("targets", [])
        if not targets: return "(no MaxSat targets)"
        return "Cited symbolic atoms (MaxSat min-flip set):\n" + "\n".join(
            f"- atom `{t.get('atom','')}`: current={t.get('current_value')} → target={t.get('target_value')}"
            for t in targets[:10])
    info = (sf_main.get(pair, {}).get("systems") or {}).get(system_id, {})
    cr = (info.get("cited_rationale") or "").strip()
    if cr: return cr[:2000]
    bl = info.get("blocker_lines") or []
    if bl: return "\n".join(str(x) for x in bl[:10])[:2000]
    return (ext_rationales.get((pair, system_id),"") or "(no rationale)")[:2000]

def word_diff(orig, cf):
    toks_a = re.findall(r"\S+|\s+", orig or "")
    toks_b = re.findall(r"\S+|\s+", cf or "")
    sm = difflib.SequenceMatcher(a=toks_a, b=toks_b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":  out.append({"type":"equal","text":"".join(toks_a[i1:i2])})
        elif tag == "delete": out.append({"type":"del","text":"".join(toks_a[i1:i2])})
        elif tag == "insert": out.append({"type":"add","text":"".join(toks_b[j1:j2])})
        elif tag == "replace":
            out.append({"type":"del","text":"".join(toks_a[i1:i2])})
            out.append({"type":"add","text":"".join(toks_b[j1:j2])})
    return out

SYSTEMS_LIST = [
    ("aegis", "A", "SMT"),
    ("v5",    "B", "Single Shot LLM (gpt-4.1)"),
    ("tg",    "C", "TrialGPT (gpt-4.1)"),
    ("shah",  "D", "Shah (gpt-4.1, binary-forced)"),
]

def build_rewrite(pair, system_id, label, system_display):
    pid, _ = pair.split("__", 1)
    orig_chart = notes.get(pid, "")
    if system_id == "aegis":
        rec = smt_cfs.get(pair)
        if not rec: return None
        cf_chart = rec.get("cf_chart","")
        verdict = "ineligible→eligible (Z3)" if rec.get("simclin_flips_cited") else "ineligible→ineligible (atoms not flipped)"
    else:
        rec = baseline_cfs.get((pair, system_id))
        if not rec: return None
        cf_chart = rec.get("cf_chart","")
        if system_id == "shah":
            sbin = shah_cf.get(pair)
            verdict = ("ineligible→eligible (binary)" if sbin else "ineligible→ineligible (binary)") if sbin is not None else "?"
        else:
            ver = rejudge_verdicts.get((pair, system_id)) or "?"
            verdict = f"ineligible→{ver}"
    return {
        "label": label,
        "system_blind_id": system_id,
        "system_display": system_display,
        "cf_chart": cf_chart,
        "cited_blocker_text": cited_for_system(pair, system_id),
        "system_verdict": verdict,
        "diff_segments": word_diff(orig_chart[:6000], cf_chart[:6000]),
    }

topics = []
for idx, pair in enumerate(chosen, 1):
    pid, tid = pair.split("__", 1)
    summary, inc, exc = trial_info(tid)
    orig_chart = notes.get(pid, "")
    if not orig_chart: continue
    rewrites = []
    for sys_id, label, disp in SYSTEMS_LIST:
        rw = build_rewrite(pair, sys_id, label, disp)
        if rw: rewrites.append(rw)
    if not rewrites: continue
    topics.append({
        "id": f"cf_audit_K7__{idx:02d}__{pair}",
        "sheet": "cf_rewrite_review",
        "task_id": "cf_rewrite_review",
        "display_index": idx,
        "patient_id": pid,
        "trial_id": tid,
        "original_chart": orig_chart,
        "trial_listing": f"Summary: {summary}\nInclusion criteria: {inc}\nExclusion criteria: {exc}",
        "trial_inclusion": inc,
        "trial_exclusion": exc,
        "rewrites": rewrites,
    })

print(f"\nBuilt {len(topics)} topics, {sum(len(t['rewrites']) for t in topics)} audit cells.")

target_file = FE_PRIVATE/"clinician_review.json"
backup = FE_PRIVATE/"clinician_review.pre_K7_audit.json.bak"
if target_file.exists() and not backup.exists():
    shutil.copy(target_file, backup); print(f"backup: {backup}")
target_file.write_text(json.dumps({"topics": topics}, indent=2))
print(f"wrote {target_file}")
