#!/usr/bin/env python3
"""Fill the K=7 audit instrument with existing simclin judgments as if
'simclin_demo' user had filled them in. Uses the simclin_* fields already
present in the v2 modifier output files."""
import json, pathlib

ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")
FE_PRIVATE = pathlib.Path("/Users/xyrus/Desktop/llm-smt/clinical-trial-annotation-frontend/private")

# Load simclin judgments from v2 modifier output files
simclin = {}  # (pair, system) → {coherent, flips_cited, keeps_other, explanation}

for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_smt_gpt5modifier.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    if r.get("cf_chart"):
        simclin[(r["pair"], "aegis")] = {
            "coherent": bool(r.get("simclin_coherent")),
            "flips_cited": bool(r.get("simclin_flips_cited")),
            "keeps_other": bool(r.get("simclin_keeps_other")),
            "explanation": r.get("simclin_explanation","")[:500],
        }

for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    if r.get("cf_chart"):
        simclin[(r["pair"], r["system"])] = {
            "coherent": bool(r.get("simclin_coherent")),
            "flips_cited": bool(r.get("simclin_flips_cited")),
            "keeps_other": bool(r.get("simclin_keeps_other")),
            "explanation": r.get("simclin_explanation","")[:500],
        }
print(f"loaded {len(simclin)} simclin judgments")

# Load the new K=7 instrument
review = json.load((FE_PRIVATE/"clinician_review.json").open())
topics = review.get("topics", review) if isinstance(review, dict) else review

# Build evaluations.json — simclin_demo's filled-in answers
evals = {"users": {"simclin_demo": {"clinician_reviews": {}}}}
from datetime import datetime, timezone
now_iso = datetime.now(timezone.utc).isoformat()

n_filled = 0; n_missing = 0
for topic in topics:
    topic_id = topic["id"]
    pair = f"{topic['patient_id']}__{topic['trial_id']}"

    rewrites = {}
    for rw in topic.get("rewrites", []):
        sys_id = rw.get("system_blind_id")
        label = rw.get("label","")
        s = simclin.get((pair, sys_id))
        if not s:
            n_missing += 1
            continue
        # Inject simclin's three answers as if a clinician filled them
        rewrites[label] = {
            "cf_coherent": "yes" if s["coherent"] else "no",
            "cf_flipped_cited": "yes" if s["flips_cited"] else "no",
            # cf_new_blocker is the INVERSE of keeps_other (new_blocker = NOT keeps_other)
            "cf_new_blocker": "no" if s["keeps_other"] else "yes",
            "cf_notes": f"[gpt-5 simclin] {s['explanation']}"[:400],
        }
        n_filled += 1

    evals["users"]["simclin_demo"]["clinician_reviews"][topic_id] = {
        "relevance": {
            "clinician_decision": "",
            "clinician_rationale": "",
            "pairwise_winner": "",
            "cf_coherent": "",
            "cf_flipped_cited": "",
            "cf_new_blocker": "",
            "cf_notes": "",
            "rewrites": rewrites,
            "rationale_axes": {},
        },
        "subcohorts": {},
        "updated_at": now_iso,
    }

# Backup + write evaluations.json
target = FE_PRIVATE/"evaluations.json"
backup = FE_PRIVATE/"evaluations.pre_K7_simclin.json.bak"
if target.exists() and not backup.exists():
    import shutil; shutil.copy(target, backup); print(f"backup: {backup}")
target.write_text(json.dumps(evals, indent=2))
print(f"wrote {target}")
print(f"filled {n_filled} rewrites, missing {n_missing}")
