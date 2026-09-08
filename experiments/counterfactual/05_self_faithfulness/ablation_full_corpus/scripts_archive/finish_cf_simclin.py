#!/usr/bin/env python3
"""Force-run simclin on the 3 CF audit rewrites that the validator rejected.

These rewrites still appear in clinician_review.json (their cf_chart text
exists), so they get displayed in the UI — but the targeted simclin runner
skips them because cf_valid=False, leaving the simclin_* fields null. We
run simclin anyway so the audit data is complete; the explanation will
include a note that the loose validator originally rejected the CF.
"""
from __future__ import annotations
import json, os, pathlib, sys, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")
SF = HERE / "selffaith"  # not used
sys.path.insert(0, str(ROOT / "experiments/counterfactual/05_self_faithfulness"))
from simulated_clinician import PROMPT, extract_target_for_system, load_charts, load_trials, llm_call


def llm(prompt: str, base: str, key: str, model: str = "gpt-5") -> dict:
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": 4000,
        "response_format": {"type": "json_object"},
        "model": model,
    }
    req = urllib.request.Request(
        f"{base}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(json.loads(r.read())["choices"][0]["message"]["content"] or "{}")


def main():
    sf_main = ROOT / "experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl"
    sf_v5gpt5 = ROOT / "experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_v5gpt5.jsonl"
    out_path = ROOT / "experiments/counterfactual/05_self_faithfulness/out/clinician_simulated.jsonl"

    by_pair = {json.loads(l)["pair"]: json.loads(l) for l in sf_main.open() if l.strip()}
    v5gpt5_by_pair = {}
    for l in sf_v5gpt5.open():
        try: o = json.loads(l)
        except: continue
        if o.get("pair"): v5gpt5_by_pair[o["pair"]] = o

    charts = load_charts()
    trials = load_trials()

    targets = [
        ("sigir-201428__NCT01280292", "v5_blockers"),
        ("sigir-20144__NCT00305201", "v5_gpt5"),
        ("sigir-20142__NCT00455468", "v5_gpt5"),
    ]

    ep_full = os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ.get("OPENAI_ENDPOINT", "")
    base = ep_full.split("/openai/")[0] if ep_full else ""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not key:
        sys.exit("need OPENAI_ENDPOINT(_GPT5) + OPENAI_API_KEY")

    new_records = []
    for pair, system in targets:
        if system == "v5_gpt5":
            info = v5gpt5_by_pair.get(pair) or {}
            cf_chart = info.get("cf_chart")
            cited = info.get("cited_rationale", "")
            sys_flipped = info.get("flipped", False)
            cf_valid = info.get("cf_valid")
        else:
            rec = by_pair.get(pair) or {}
            info = (rec.get("systems") or {}).get(system) or {}
            cf_chart = info.get("cf_chart")
            cited = info.get("cited_rationale") or info.get("cited_blockers") or ""
            sys_flipped = info.get("flipped", False)
            cf_valid = info.get("cf_valid")

        pid, tid = pair.split("__", 1)
        chart = charts.get(pid, "")
        inc, exc = trials.get(tid, ("", ""))
        atom, evidence, cur, tgt = extract_target_for_system(system, info)
        rejudged_v = (info.get("rejudged") or {}).get("eligibility", "") if isinstance(info, dict) else ""
        original_v = ("eligible" if rejudged_v == "ineligible" else "ineligible") if sys_flipped else rejudged_v

        prompt = PROMPT.format(
            original_chart=chart[:5000],
            cf_chart=(cf_chart or "")[:5000],
            inclusion=inc[:3000], exclusion=exc[:3000],
            atom=atom, evidence=evidence,
            current_value=cur, target_value=tgt,
            original_verdict=original_v,
        )
        print(f"running simclin: {pair} / {system}  (cf_valid={cf_valid})", flush=True)
        try:
            j = llm_call("gpt-5", prompt, base, key)
        except Exception as e:
            j = {"error": str(e)[:200]}
        if not j: j = {}
        rec = {
            "pair": pair,
            "system": system,
            "system_flipped": sys_flipped,
            "system_cf_verdict": "eligible" if sys_flipped else "ineligible",
            "original_verdict": "ineligible",
            "loose_validator_cf_valid": cf_valid,
            "coherent": j.get("coherent"),
            "flips_target_atom": j.get("flips_target_atom"),
            "keeps_other_facts": j.get("keeps_other_facts"),
            "oracle_should_flip": j.get("oracle_should_flip"),
            "oracle_verdict_on_cf": j.get("oracle_verdict_on_cf"),
            "explanation": (
                (j.get("explanation") or "")
                + ("  [Note: loose validator originally rejected this CF (cf_valid=False).]"
                   if cf_valid is False else "")
            ),
        }
        new_records.append(rec)

    with out_path.open("a") as f:
        for r in new_records:
            f.write(json.dumps(r) + "\n")
    print(f"appended {len(new_records)} records to {out_path}")


if __name__ == "__main__":
    main()
