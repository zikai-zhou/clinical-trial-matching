#!/usr/bin/env python3
"""Re-run SMT chart modifier with gpt-5 + sharpened coherence prompt.

Reads SMT targets from self_faithfulness.jsonl, generates a new CF chart
per pair using the gpt-5 modifier, immediately runs gpt-5 simclin on the
result, and writes a self-contained record per pair.

Output: self_faithfulness_smt_gpt5modifier.jsonl
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, sys, urllib.request, difflib
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path("<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored")
sys.path.insert(0, "/tmp")
sys.path.insert(0, str(ROOT / "experiments/counterfactual/05_self_faithfulness"))

from cf_modifier_gpt5 import generate_cf_gpt5_targets
from simulated_clinician import extract_target_for_system, load_charts, load_trials, llm_call


def render_diff(orig: str, cf: str, max_segs: int = 60) -> str:
    toks_a = re.findall(r"\S+|\s+", orig)
    toks_b = re.findall(r"\S+|\s+", cf)
    sm = difflib.SequenceMatcher(a=toks_a, b=toks_b, autojunk=False)
    lines = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal": continue
        if tag in ("delete", "replace"):
            removed = "".join(toks_a[i1:i2]).strip()
            if removed: lines.append(f"  - REMOVED: {removed[:300]}")
        if tag in ("insert", "replace"):
            added = "".join(toks_b[j1:j2]).strip()
            if added: lines.append(f"  + ADDED:   {added[:300]}")
        if len(lines) >= max_segs:
            lines.append("  ... (truncated)")
            break
    return "\n".join(lines) if lines else "  (no change)"


SIMCLIN_PROMPT = """You are a senior clinical-trial coordinator at the PRESCREEN stage.

A clinical-trial matcher said the patient was INELIGIBLE. We then asked an
automated chart modifier to neutralize the cited blockers by editing the
chart. Your job: judge the EDIT, not the patient.

ORIGINAL CHART
==============
{original_chart}

INCLUSION
=========
{inclusion}

EXCLUSION
=========
{exclusion}

SYSTEM-CITED BLOCKER (atom-typed)
==================================
{targets_block}

ORIGINAL SYSTEM VERDICT: ineligible

THE EDIT (DIFF — what was changed)
===================================
{diff_summary}

CHART AFTER MODIFICATION
========================
{cf_chart}

QUESTIONS — answer in JSON with exactly these keys:
  coherent (bool)            : is the modified chart internally coherent and clinically plausible?
  flips_target_atom (bool)   : does the edit neutralize ALL cited blockers?
  keeps_other_facts (bool)   : does the rest of the chart still match the original (no NEW blockers)?
  oracle_should_flip (bool)  : if a clinician saw the modified chart, would the trial be eligible now?
  oracle_verdict_on_cf (str) : "eligible" or "ineligible"
  explanation (str)          : 1-2 sentences

Return ONLY the JSON object.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_smt_gpt5modifier.jsonl"))
    args = ap.parse_args()

    ep_full = os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ.get("OPENAI_ENDPOINT", "")
    base = ep_full.split("/openai/")[0] if ep_full else ""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not key: sys.exit("env required")

    charts = load_charts()
    trials = load_trials()

    sf = ROOT / "experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl"
    records = []
    for ln in sf.open():
        r = json.loads(ln)
        a = (r.get("systems") or {}).get("aegis", {})
        if not a.get("targets"): continue
        records.append({
            "pair": r["pair"],
            "targets": a["targets"],
            "original_loose_valid": a.get("cf_valid"),
            "original_flipped": a.get("flipped"),
        })

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache = set()
    if out_path.exists():
        for l in out_path.open():
            try: o = json.loads(l)
            except: continue
            cache.add(o.get("pair"))
    todo = [r for r in records if r["pair"] not in cache]
    if args.limit: todo = todo[:args.limit]
    print(f"total SMT records: {len(records)}; cached: {len(cache)}; todo: {len(todo)}")

    def run_one(rec):
        pair = rec["pair"]
        pid, tid = pair.split("__", 1)
        chart = charts.get(pid, "")
        inc, exc = trials.get(tid, ("", ""))
        targets = rec["targets"]

        # Step 1: gpt-5 modifier
        try:
            cf_chart = generate_cf_gpt5_targets(chart, targets)
        except Exception as e:
            return {"pair": pair, "modifier_error": str(e)[:200],
                    "original_loose_valid": rec["original_loose_valid"],
                    "original_flipped": rec["original_flipped"]}

        # Step 2: gpt-5 simclin (with diff context)
        targets_block = "\n".join([
            f"- {t['atom']}: current={t.get('current_value')} -> target={t.get('target_value')}"
            for t in targets
        ])[:1500]
        diff_block = render_diff(chart[:5000], cf_chart[:5000])
        prompt = SIMCLIN_PROMPT.format(
            original_chart=chart[:5000],
            inclusion=inc[:3000], exclusion=exc[:3000],
            targets_block=targets_block,
            diff_summary=diff_block,
            cf_chart=cf_chart[:5000],
        )
        try:
            j = llm_call("gpt-5", prompt, base, key)
        except Exception as e:
            j = {"error": str(e)[:200]}
        if not j: j = {}

        return {
            "pair": pair,
            "cf_chart": cf_chart,
            "modifier": "gpt-5-v2-coherent",
            "diff_summary": diff_block[:2000],
            "original_loose_valid": rec["original_loose_valid"],
            "original_flipped": rec["original_flipped"],
            "simclin_coherent": j.get("coherent"),
            "simclin_flips_cited": j.get("flips_target_atom"),
            "simclin_keeps_other": j.get("keeps_other_facts"),
            "simclin_oracle_should_flip": j.get("oracle_should_flip"),
            "simclin_explanation": (j.get("explanation") or "")[:500],
        }

    done = 0
    with out_path.open("a") as f, ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed({ex.submit(run_one, r): r for r in todo}):
            rec = fut.result()
            f.write(json.dumps(rec) + "\n"); f.flush()
            done += 1
            if done % 10 == 0 or done == len(todo):
                print(f"  done {done}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
