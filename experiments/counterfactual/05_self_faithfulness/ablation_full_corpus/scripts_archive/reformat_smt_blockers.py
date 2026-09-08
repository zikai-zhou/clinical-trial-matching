#!/usr/bin/env python3
"""Reformat SMT (aegis) cited_blocker_text from atom-machine-format into
plain-language ADD/REMOVE/CHANGE bullets a clinician can read.

Atom name parsing rules:
  patient_has_finding_of_chest_pain_now → "patient has finding of chest pain (current)"
  patient_has_..._inthehistory → "(historical)" suffix
  X@@qualifier → "X — qualified by: <qualifier>"
  __THRESH__::X::ge::N → "patient X must be ≥ N"
  patient_age_value_recorded_now_in_years (numeric) → "patient age in years"
"""
import json, pathlib, re

FE_PRIVATE = pathlib.Path("/Users/xyrus/Desktop/llm-smt/clinical-trial-annotation-frontend/private")
ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")

# Load SMT targets from full self_faithfulness.jsonl
smt_targets = {}
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    a = (r.get("systems") or {}).get("aegis", {})
    if a.get("targets"):
        smt_targets[r["pair"]] = a["targets"]
print(f"loaded SMT targets for {len(smt_targets)} pairs")


def humanize_atom(atom_name):
    """Convert atom_snake_case@@qualifier into plain language."""
    # Threshold atoms: __THRESH__::var::op::value
    if atom_name.startswith("__THRESH__::"):
        parts = atom_name.split("::")
        if len(parts) == 4:
            _, var, op, val = parts
            op_word = {"ge":"≥", "le":"≤", "gt":">", "lt":"<", "eq":"=", "ne":"≠"}.get(op, op)
            return f"{humanize_var(var)} must be {op_word} {val}"
    # @@qualifier split
    if "@@" in atom_name:
        base, qual = atom_name.split("@@", 1)
        return f"{humanize_var(base)} — qualified by: {qual.replace('_',' ')}"
    return humanize_var(atom_name)

def humanize_var(name):
    s = name.replace("_", " ")
    # Tidy common patterns
    s = re.sub(r"\binthehistory\b", "(historical)", s)
    s = re.sub(r"\bnow\b", "(current)", s)
    s = re.sub(r"\bvalue recorded\b", "value", s)
    s = re.sub(r"\bwithunit ([a-z]+)\b", r"in \1", s)
    return s.strip()


def format_blockers(targets):
    """Group targets into ADD / REMOVE / SET-VALUE buckets, render plain text."""
    add_lines = []      # current=False → target=True
    remove_lines = []   # current=True → target=False
    setval_lines = []   # numeric/string value changes
    silent_lines = []   # target=None (any value, or "any" satisfying)

    for t in targets:
        atom = t.get("atom","")
        cv = t.get("current_value")
        tv = t.get("target_value")
        human = humanize_atom(atom)
        if isinstance(cv, bool) and isinstance(tv, bool):
            if cv is False and tv is True:
                add_lines.append(human)
            elif cv is True and tv is False:
                remove_lines.append(human)
            else:
                setval_lines.append(f"{human}: {cv} → {tv}")
        elif tv is None:
            silent_lines.append(human)
        else:
            setval_lines.append(f"{human}: currently {cv} → must be {tv}")

    blocks = []
    if add_lines:
        blocks.append("ADD (these facts are currently missing — the chart should establish them):\n" +
                      "\n".join(f"  • {x}" for x in add_lines))
    if remove_lines:
        blocks.append("REMOVE (these facts are currently in the chart — they must be removed or contradicted):\n" +
                      "\n".join(f"  • {x}" for x in remove_lines))
    if setval_lines:
        blocks.append("CHANGE VALUE:\n" +
                      "\n".join(f"  • {x}" for x in setval_lines))
    if silent_lines:
        blocks.append("MAKE SILENT (chart should no longer commit to a value for these):\n" +
                      "\n".join(f"  • {x}" for x in silent_lines))

    if not blocks:
        return "(no symbolic targets)"
    header = "SMT's reasons for ineligibility (machine-derived atomic facts). To make the patient eligible, the chart must:\n"
    return header + "\n\n".join(blocks)


# Test on a sample
sample = list(smt_targets.values())[0]
print("\n--- SAMPLE ---")
print(format_blockers(sample[:5]))
print("---\n")

# Apply to clinician_review.json
review = json.load((FE_PRIVATE/"clinician_review.json").open())
n_patched = 0
for t in review["topics"]:
    if t.get("sheet") != "cf_rewrite_review": continue
    pair = f"{t['patient_id']}__{t['trial_id']}"
    targets = smt_targets.get(pair, [])
    for rw in t.get("rewrites", []):
        if rw.get("system_blind_id") != "aegis": continue
        rw["cited_blocker_text"] = format_blockers(targets)
        n_patched += 1
(FE_PRIVATE/"clinician_review.json").write_text(json.dumps(review, indent=2))
print(f"patched {n_patched} SMT rewrites in clinician_review.json")
