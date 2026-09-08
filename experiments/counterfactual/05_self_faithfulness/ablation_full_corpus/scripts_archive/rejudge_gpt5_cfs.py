#!/usr/bin/env python3
"""Re-run each LLM baseline matcher on the gpt-5-modifier CFs to compute
actual flip rates (ineligible → eligible).

Reads:
  - self_faithfulness_baselines_gpt5modifier.jsonl (v5/v5_blockers/tg/shah/v5_gpt5)
Writes:
  - self_faithfulness_baselines_gpt5modifier_rejudged.jsonl with eligibility per record
"""
from __future__ import annotations
import argparse, json, os, pathlib, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")
sys.path.insert(0, str(ROOT/"experiments/counterfactual/utils"))
sys.path.insert(0, str(ROOT/"experiments/counterfactual/05_self_faithfulness"))
import cf_judge as jg
from simulated_clinician import load_charts, load_trials


V5_BLOCKERS_PROMPT_PATH = ROOT / "matchers/systems/single_shot_llm/prompts/V5_TWO_STEP_BLOCKERS.prompt"
V5_BLOCKERS_PROMPT = V5_BLOCKERS_PROMPT_PATH.read_text() if V5_BLOCKERS_PROMPT_PATH.exists() else ""


def judge_v5_blockers(chart: str, trial: str) -> dict:
    """v5_blockers uses its own prompt that asks for {eligibility, blockers, supports}.
    Different from V5_PROMPT (just eligibility) — verdict can differ on the same chart.
    Uses gpt-4.1 (same backbone as v5_blockers' published matcher)."""
    import os, json, urllib.request, re
    prompt = V5_BLOCKERS_PROMPT.replace("{{CHART}}", chart[:5000]).replace("{{TRIAL}}", trial[:5000])
    ep = os.environ.get("OPENAI_ENDPOINT", "")
    key = os.environ.get("OPENAI_API_KEY", "")
    body = {"messages":[{"role":"user","content":prompt}], "max_tokens":3000, "temperature":0,
            "response_format":{"type":"json_object"}}
    req = urllib.request.Request(
        f"{ep}/chat/completions?api-version=2024-08-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key": key, "Content-Type":"application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    try: o = json.loads(txt)
    except:
        m = re.search(r"\{[\s\S]*\}", txt)
        try: o = json.loads(m.group(0)) if m else {}
        except: o = {}
    return {"system":"v5_blockers", "eligibility": (o.get("eligibility") or "unknown").lower(),
            "rationale": (o.get("explanation","") or "")[:500]}


def judge_shah_proper(chart: str, trial: str) -> dict:
    """Use Shah's actual Koopman prompt + lenient gd>=1 binarization, not TG's per-criterion."""
    import os, json, urllib.request, re, sys
    sys.path.insert(0, str(ROOT/"matchers/systems/shahlab"))
    # Re-load shah's fixed build_prompt
    src = (ROOT/"matchers/systems/shahlab/run.py").read_text()
    src_clean = src.replace("from run_better_nl_full import get_pair_inputs, collect_pairs", "")
    shah_mod = type(sys)("shah_run")
    shah_mod.__dict__["__file__"] = str(ROOT/"matchers/systems/shahlab/run.py")
    exec(compile(src_clean, str(ROOT/"matchers/systems/shahlab/run.py"), "exec"), shah_mod.__dict__)
    build_prompt = shah_mod.build_prompt
    # Split trial into inc/exc
    _inc_re = re.compile(r"(?i)\binclusion\s+criteria\s*:\s*")
    _exc_re = re.compile(r"(?i)\bexclusion\s+criteria\s*:\s*")
    im = _inc_re.search(trial); em = _exc_re.search(trial)
    inc = trial[im.end(): em.start() if em else len(trial)].strip() if im else ""
    exc = trial[em.end():].strip() if em else ""
    prompt = build_prompt(chart[:5000], inc, exc)
    ep = os.environ.get("OPENAI_ENDPOINT", "")
    key = os.environ.get("OPENAI_API_KEY", "")
    body = {"messages":[{"role":"user","content":prompt}], "max_tokens":3000, "temperature":0,
            "response_format":{"type":"json_object"}}
    req = urllib.request.Request(
        f"{ep}/chat/completions?api-version=2024-08-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key": key, "Content-Type":"application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    try: o = json.loads(txt)
    except:
        m = re.search(r"\{[\s\S]*\}", txt)
        try: o = json.loads(m.group(0)) if m else {}
        except: o = {}
    gd = o.get("global_decision")
    eligible = (gd is not None and gd >= 1)
    return {"system":"shah", "eligibility": "eligible" if eligible else "ineligible",
            "global_decision": gd, "rationale": ""}


def judge_v5_gpt5(chart: str, trial: str) -> dict:
    """Same prompt as judge_v5 but using the gpt-5 endpoint, since
    v5_gpt5's defining property is that its matcher is gpt-5, not gpt-4.1.
    Self-faithfulness measurement requires re-running the *same* matcher
    on the modified chart."""
    import os, json, urllib.request, re
    ep5 = os.environ.get("OPENAI_ENDPOINT_GPT5") or ""
    key = os.environ.get("OPENAI_API_KEY", "")
    base = ep5.split("/openai/")[0] if ep5 else ""
    if not base or not key:
        # Fallback: caller log warns; return error sentinel
        return {"system": "v5_gpt5", "eligibility": "error_no_gpt5_endpoint", "rationale": ""}
    prompt = jg.V5_PROMPT.format(chart=chart[:5000], trial=trial[:5000])
    body = {
        "model": "gpt-5",
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": 6000,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{base}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    try: o = json.loads(txt)
    except:
        m = re.search(r"\{[\s\S]*\}", txt)
        try: o = json.loads(m.group(0)) if m else {}
        except: o = {}
    return {
        "system": "v5_gpt5",
        "eligibility": (o.get("eligibility") or "unknown").lower(),
        "rationale": o.get("explanation","") or "",
    }


def judge_for_system(system: str, cf_chart: str, trial: str) -> dict:
    if system == "v5":
        return jg.judge_v5(cf_chart, trial)
    if system == "v5_blockers":
        return judge_v5_blockers(cf_chart, trial)
    if system == "v5_gpt5":
        return judge_v5_gpt5(cf_chart, trial)
    if system == "tg":
        return jg.judge_tg(cf_chart, trial)
    if system == "shah":
        return judge_shah_proper(cf_chart, trial)
    return {"eligibility": "unknown", "rationale": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--input", default=str(ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl"))
    ap.add_argument("--output", default=str(ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier_rejudged.jsonl"))
    args = ap.parse_args()

    ep = os.environ.get("OPENAI_ENDPOINT", "")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not ep or not key:
        sys.exit("need OPENAI_ENDPOINT + OPENAI_API_KEY")
    # cf_judge reads from module-level ENDPOINT/KEY; the env is already in scope so it works.

    trials = load_trials()  # dict {tid: (inc, exc)}

    # Source records
    src = []
    for ln in pathlib.Path(args.input).open():
        if not ln.strip(): continue
        try: r = json.loads(ln)
        except: continue
        if r.get("cf_chart") and r.get("pair") and r.get("system"):
            src.append(r)

    out_path = pathlib.Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache = set()
    if out_path.exists():
        for l in out_path.open():
            try: o = json.loads(l)
            except: continue
            cache.add((o.get("pair"), o.get("system")))
    todo = [r for r in src if (r["pair"], r["system"]) not in cache]
    if args.limit: todo = todo[:args.limit]
    print(f"src {len(src)}, cached {len(cache)}, todo {len(todo)}")

    def run_one(rec):
        pair = rec["pair"]; system = rec["system"]
        pid, tid = pair.split("__", 1)
        inc, exc = trials.get(tid, ("",""))
        trial_text = f"INCLUSION:\n{inc}\n\nEXCLUSION:\n{exc}"
        try:
            j = judge_for_system(system, rec["cf_chart"], trial_text)
        except Exception as e:
            j = {"eligibility": "error", "rationale": str(e)[:200]}
        return {
            "pair": pair, "system": system,
            "cf_eligibility_under_v2": j.get("eligibility", "unknown"),
            "cf_rationale_under_v2": (j.get("rationale") or "")[:500],
            # The original v2 simclin judgments for cross-ref
            "simclin_flips_cited": rec.get("simclin_flips_cited"),
            "simclin_oracle_should_flip": rec.get("simclin_oracle_should_flip"),
            "original_system_flipped": rec.get("original_system_flipped"),
        }

    done = 0
    with out_path.open("a") as f, ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed({ex.submit(run_one, r): r for r in todo}):
            rec = fut.result()
            f.write(json.dumps(rec) + "\n"); f.flush()
            done += 1
            if done % 25 == 0 or done == len(todo):
                print(f"  done {done}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
