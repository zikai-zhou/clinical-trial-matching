#!/usr/bin/env python3
"""Run gpt-5 simclin on every (pair, system) with a valid CF, with explicit
diff context in the prompt. Output extends clinician_simulated.jsonl with
diff-aware records (marked with version=diff_v1).

Cost: ~566 gpt-5 calls to fill all gaps.
"""
from __future__ import annotations
import argparse, difflib, json, os, pathlib, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")
sys.path.insert(0, str(ROOT / "experiments/counterfactual/05_self_faithfulness"))
from simulated_clinician import extract_target_for_system, load_charts, load_trials, llm_call


PROMPT_TEMPLATE = """You are a senior clinical-trial coordinator at the PRESCREEN stage.

A clinical-trial matcher said the patient was INELIGIBLE for this trial, citing
a specific blocker. We then asked an automated chart modifier to neutralize
that one blocker by editing the chart. Your job: judge the EDIT, not the patient.

ORIGINAL CHART
==============
{original_chart}

INCLUSION
=========
{inclusion}

EXCLUSION
=========
{exclusion}

SYSTEM-CITED BLOCKER
====================
atom: {atom}
chart evidence: {evidence}
current value: {current_value}
target value after modification: {target_value}

ORIGINAL SYSTEM VERDICT: {original_verdict}

THE EDIT (DIFF — what was changed)
===================================
{diff_summary}

CHART AFTER MODIFICATION
========================
{cf_chart}

QUESTIONS — answer in JSON with exactly these keys:
  coherent (bool)            : is the modified chart internally coherent and clinically plausible?
  flips_target_atom (bool)   : does the edit actually neutralize the cited blocker?
  keeps_other_facts (bool)   : does the rest of the chart still match the original (no NEW blockers introduced)?
  oracle_should_flip (bool)  : if a clinician saw the modified chart, would the trial be eligible now?
  oracle_verdict_on_cf (str) : "eligible" or "ineligible"
  explanation (str)          : 1-2 sentence rationale

Return ONLY the JSON object.
"""


def render_diff(orig: str, cf: str, max_segs: int = 60) -> str:
    """Word-level diff as a structured 'what changed' block."""
    toks_a = re.findall(r"\S+|\s+", orig)
    toks_b = re.findall(r"\S+|\s+", cf)
    sm = difflib.SequenceMatcher(a=toks_a, b=toks_b, autojunk=False)
    lines = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal": continue
        if tag in ("delete", "replace"):
            removed = "".join(toks_a[i1:i2]).strip()
            if removed:
                lines.append(f"  - REMOVED: {removed[:300]}")
        if tag in ("insert", "replace"):
            added = "".join(toks_b[j1:j2]).strip()
            if added:
                lines.append(f"  + ADDED:   {added[:300]}")
        if len(lines) >= max_segs:
            lines.append(f"  ... ({len(sm.get_opcodes())} total opcodes; truncated)")
            break
    return "\n".join(lines) if lines else "  (no textual change detected — likely identical)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--systems", default="aegis,v5,v5_blockers,shah,tg,v5_gpt5")
    ap.add_argument("--out", default=str(ROOT/"experiments/counterfactual/05_self_faithfulness/out/clinician_simulated_diff.jsonl"))
    ap.add_argument("--limit", type=int, default=0, help="cap total runs (0 = no cap)")
    args = ap.parse_args()

    ep_full = os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ.get("OPENAI_ENDPOINT", "")
    base = ep_full.split("/openai/")[0] if ep_full else ""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not key:
        sys.exit("need OPENAI_ENDPOINT(_GPT5) + OPENAI_API_KEY")

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    charts = load_charts()
    trials = load_trials()

    # Multi-system source
    sf_main = ROOT / "experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl"
    by_pair = {json.loads(l)["pair"]: json.loads(l) for l in sf_main.open() if l.strip()}

    # v5_gpt5 source (single-system schema)
    v5gpt5_by_pair = {}
    for l in (ROOT / "experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_v5gpt5.jsonl").open():
        try: o = json.loads(l)
        except: continue
        if o.get("pair") and o.get("cf_chart") and o.get("cf_valid"):
            v5gpt5_by_pair[o["pair"]] = o

    # Build work list
    work = []
    for s in systems:
        if s == "v5_gpt5":
            for pair, info in v5gpt5_by_pair.items():
                work.append((pair, s, info))
        else:
            for pair, rec in by_pair.items():
                info = (rec.get("systems") or {}).get(s) or {}
                if info.get("cf_valid") and info.get("cf_chart") and info.get("rejudged"):
                    work.append((pair, s, info))

    # Cache from existing diff-aware output
    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache = set()
    if out_path.exists():
        for ln in out_path.open():
            try: o = json.loads(ln)
            except: continue
            cache.add((o.get("pair"), o.get("system")))
    todo = [w for w in work if (w[0], w[1]) not in cache]
    if args.limit: todo = todo[:args.limit]
    print(f"total work: {len(work)}; cached: {len(cache)}; todo: {len(todo)}")

    def run_one(item):
        pair, system, info = item
        pid, tid = pair.split("__", 1)
        chart = charts.get(pid, "")
        inc, exc = trials.get(tid, ("", ""))
        atom, evidence, cur, tgt = extract_target_for_system(system, info)
        cf_chart = info.get("cf_chart") or ""
        rejudged_v = (info.get("rejudged") or {}).get("eligibility", "")
        sys_flipped = bool(info.get("flipped"))
        original_v = ("eligible" if rejudged_v == "ineligible" else "ineligible") if sys_flipped else rejudged_v

        diff_block = render_diff(chart[:5000], cf_chart[:5000])

        prompt = PROMPT_TEMPLATE.format(
            original_chart=chart[:5000],
            inclusion=inc[:3000], exclusion=exc[:3000],
            atom=atom, evidence=evidence,
            current_value=cur, target_value=tgt,
            original_verdict=original_v,
            diff_summary=diff_block,
            cf_chart=cf_chart[:5000],
        )
        try:
            j = llm_call("gpt-5", prompt, base, key)
        except Exception as e:
            j = {"error": str(e)[:200]}
        if not j: j = {"parse_fail": True}
        return {
            "pair": pair, "system": system,
            "system_flipped": sys_flipped,
            "system_cf_verdict": rejudged_v,
            "original_verdict": original_v,
            "coherent": j.get("coherent"),
            "flips_target_atom": j.get("flips_target_atom"),
            "keeps_other_facts": j.get("keeps_other_facts"),
            "oracle_should_flip": j.get("oracle_should_flip"),
            "oracle_verdict_on_cf": j.get("oracle_verdict_on_cf"),
            "explanation": (j.get("explanation") or "")[:500],
            "diff_aware": True,
            "diff_chars": len(diff_block),
            "version": "diff_v1",
        }

    done = 0
    with out_path.open("a") as f, ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(run_one, w): w for w in todo}
        for fut in as_completed(futures):
            rec = fut.result()
            f.write(json.dumps(rec) + "\n"); f.flush()
            done += 1
            if done % 25 == 0 or done == len(todo):
                print(f"  done {done}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
