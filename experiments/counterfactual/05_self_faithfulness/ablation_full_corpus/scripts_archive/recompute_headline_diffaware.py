#!/usr/bin/env python3
"""Recompute CF headline numbers under the diff-aware gpt-5 simclin gate.

For each system, treat simclin_correct = coherent ∧ flips_target_atom ∧
keeps_other_facts as the new strict validity gate. Report:
  - simclin-gate valid CF count
  - simclin-gate flip rate
  - delta vs loose-validator headline

Works on whatever subset of clinician_simulated_diff.jsonl is complete.
"""
import json, pathlib
from collections import defaultdict

ROOT = pathlib.Path("<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored")

# Diff-aware simclin
diff_path = ROOT / "experiments/counterfactual/05_self_faithfulness/out/clinician_simulated_diff.jsonl"
diff_judgments = {}
for ln in diff_path.open():
    if not ln.strip(): continue
    r = json.loads(ln)
    if r.get("coherent") is None: continue   # skip parse fails
    diff_judgments[(r["pair"], r["system"])] = r

# Loose-validator CF state (the "previously valid" denominators)
loose_valid = defaultdict(set)   # system -> {pair, ...}
loose_flipped = defaultdict(set)
sf = ROOT / "experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl"
for ln in sf.open():
    rec = json.loads(ln); pair = rec["pair"]
    for s, info in (rec.get("systems") or {}).items():
        if info.get("cf_valid") and info.get("cf_chart"):
            loose_valid[s].add(pair)
            if info.get("flipped"): loose_flipped[s].add(pair)
for ln in (ROOT / "experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_v5gpt5.jsonl").open():
    try: o = json.loads(ln)
    except: continue
    if o.get("cf_valid") and o.get("cf_chart"):
        loose_valid["v5_gpt5"].add(o["pair"])
        if o.get("flipped"): loose_flipped["v5_gpt5"].add(o["pair"])

DISPLAY = {"aegis":"SMT", "v5":"Single Shot LLM", "v5_blockers":"Single Shot+grounds",
           "shah":"Shah", "tg":"TrialGPT", "v5_gpt5":"Single Shot LLM (gpt-5)"}

print(f"{'System':25s} | {'loose n / flip':>20s} | {'diff cov':>10s} | {'simclin-strict n / flip':>26s} | flip rate")
print("-"*120)
for s in ["aegis","v5","v5_blockers","tg","shah","v5_gpt5"]:
    pop_n = len(loose_valid[s])
    pop_flip = len(loose_flipped[s])
    if pop_n == 0: continue

    # Coverage of diff-aware simclin on this system's loose-valid CFs
    covered = [p for p in loose_valid[s] if (p, s) in diff_judgments]
    cov_pct = len(covered) / pop_n if pop_n else 0

    # Strict-valid under simclin = covered AND coherent AND flips_cited AND keeps_other
    strict_valid = []
    for p in covered:
        j = diff_judgments[(p, s)]
        if j.get("coherent") and j.get("flips_target_atom") and j.get("keeps_other_facts"):
            strict_valid.append(p)
    strict_flipped = [p for p in strict_valid if p in loose_flipped[s]]

    sv = len(strict_valid)
    sf_cnt = len(strict_flipped)
    flip_rate = sf_cnt / sv if sv else 0
    name = DISPLAY.get(s, s)
    print(f"{name:25s} | {pop_n:6d} / {pop_flip:5d} ({pop_flip/pop_n:5.1%}) | {len(covered):3d}/{pop_n} ({cov_pct:4.0%}) | {sv:6d} / {sf_cnt:5d} (of strict)        | {flip_rate:6.1%}")

print()
print("Coverage advisory: if 'diff cov' < 100%, the simclin-strict flip rate is computed on the covered subset only.")
print("Run again once clinician_simulated_diff.jsonl has more rows.")
