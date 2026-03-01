#!/usr/bin/env python3
"""Falsifier test: re-judge v5's v2-modifier CFs with the gpt-5 matcher.

If gpt-5 on v5-CFs flips at ~v5's rate (29%) → the 81% v5_gpt5 number is
artifact (gpt-5 generates narrower rationales → easier modifications → flips).
If gpt-5 on v5-CFs flips at ~v5_gpt5's rate (81%) → gpt-5 matcher really
is more responsive, regardless of who wrote the rationale.

Reads v5's CFs from self_faithfulness_baselines_gpt5modifier.jsonl.
Writes to /tmp/falsifier_v5cf_gpt5_judge.jsonl.
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path("<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored")
sys.path.insert(0, str(ROOT / "experiments/counterfactual/05_self_faithfulness"))
sys.path.insert(0, str(ROOT / "experiments/counterfactual/utils"))
from simulated_clinician import load_charts, load_trials
import cf_judge as jg


def judge_v5_with_gpt5(chart, trial):
    """V5 prompt, gpt-5 endpoint — same as the proper v5_gpt5 judger."""
    ep5 = os.environ.get("OPENAI_ENDPOINT_GPT5") or ""
    key = os.environ.get("OPENAI_API_KEY","")
    base = ep5.split("/openai/")[0] if ep5 else ""
    prompt = jg.V5_PROMPT.format(chart=chart[:5000], trial=trial[:5000])
    body = {"model":"gpt-5","messages":[{"role":"user","content":prompt}],
            "max_completion_tokens":6000,"response_format":{"type":"json_object"}}
    req = urllib.request.Request(
        f"{base}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key": key, "Content-Type":"application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    try: o = json.loads(txt)
    except:
        m = re.search(r"\{[\s\S]*\}", txt); o = json.loads(m.group(0)) if m else {}
    return (o.get("eligibility") or "unknown").lower()


def main():
    trials = load_trials()
    # v5's CFs (generated from gpt-4.1's cited rationale, by gpt-5 modifier)
    v5_cfs = []
    for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl").open():
        if not ln.strip(): continue
        r = json.loads(ln)
        if r.get("system") == "v5" and r.get("cf_chart"):
            v5_cfs.append(r)
    print(f"v5 CFs to re-judge with gpt-5: {len(v5_cfs)}")

    out = pathlib.Path("/tmp/falsifier_v5cf_gpt5_judge.jsonl")
    cache = set()
    if out.exists():
        for l in out.open():
            try: o = json.loads(l)
            except: continue
            cache.add(o.get("pair"))
    todo = [r for r in v5_cfs if r["pair"] not in cache]
    print(f"cached: {len(cache)}, todo: {len(todo)}")

    def proc(rec):
        pair = rec["pair"]
        pid, tid = pair.split("__", 1)
        inc, exc = trials.get(tid, ("",""))
        trial_text = f"INCLUSION:\n{inc}\n\nEXCLUSION:\n{exc}"
        try:
            e = judge_v5_with_gpt5(rec["cf_chart"], trial_text)
        except Exception as ex:
            e = f"error:{str(ex)[:100]}"
        return {"pair": pair, "gpt5_verdict_on_v5_cf": e,
                "v5_gpt41_verdict_on_v5_cf": rec.get("cf_eligibility_under_v2",
                    None)}

    done = 0
    with out.open("a") as f, ThreadPoolExecutor(max_workers=8) as ex:
        for fut in as_completed({ex.submit(proc, r): r for r in todo}):
            r = fut.result()
            f.write(json.dumps(r)+"\n"); f.flush()
            done += 1
            if done % 25 == 0 or done == len(todo):
                print(f"  done {done}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
