"""Run LLM-d and TG on SatIR's v3 minimal-edit CF charts.

Completes the CF matrix: minimal edits + shared chart across all 3 systems.
SatIR's flip rate on these charts is known (85%); this measures whether
LLM-d/TG also flip on the *same* minimal-edit charts.
"""
from __future__ import annotations
import json, os, pathlib, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine import AzureInferenceEngine  # noqa: E402
from smt_matcher.judges import run_llm_eligibility_judge, run_trialgpt_judge  # noqa: E402

RESULTS = ROOT / "evaluation" / "results"
V3 = RESULTS / "verbalize_judge_235_v3"
SRC = RESULTS / "counterfactual_satir_minimal_60_v3"
OUT = RESULTS / "llm_tg_on_satir_minimal_60"
OUT.mkdir(parents=True, exist_ok=True)


def load_trial(tid, data_root):
    with open(data_root / "sigir" / "corpus.jsonl") as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("_id") == tid:
                md = obj.get("metadata") or {}
                return {
                    "nct_id": tid, "brief_title": md.get("brief_title", ""),
                    "brief_summary": md.get("brief_summary", ""),
                    "inclusion_criteria": md.get("inclusion_criteria", ""),
                    "exclusion_criteria": md.get("exclusion_criteria", ""),
                    "metadata": md,
                }
    return None


def process_one(pair_dir, engine, data_root, out_dir):
    r = json.load(open(pair_dir / "result.json"))
    if "minimal_cf_chart" not in r:
        return {"pair": pair_dir.name, "error": "no minimal_cf_chart"}
    pair = r["pair"]
    pid, tid = pair.split("__")
    cf_chart = r["minimal_cf_chart"]

    # Original decisions from v3 shards (for reference)
    orig = None
    for shard in V3.glob("shard_*"):
        pd = shard / pair
        if pd.exists():
            llmd = json.load(open(pd / "llm_direct_decision.json"))
            tg = json.load(open(pd / "trialgpt_decision.json"))
            orig = {
                "llm_d": (llmd.get("result") or {}).get("eligible"),
                "tg": (tg.get("aggregate") or {}).get("eligible"),
            }
            break
    if orig is None:
        return {"pair": pair, "error": "no pair_dir"}

    trial = load_trial(tid, data_root)
    patient_cf = {"_id": pid, "patient_id": pid, "text": cf_chart, "metadata": {}}
    work = out_dir / pair / "_work"; work.mkdir(parents=True, exist_ok=True)

    llm_cf = run_llm_eligibility_judge(
        trial_id=tid, trial_obj=trial, patient=patient_cf, engine=engine,
        prompt_path=ROOT/"smt_matcher"/"prompts"/"clinical_trial"/"SMTMatcher"/"eligibility.explicit.prompt",
        out_root=work, model_name="gpt-4.1", temperature=0.0,
    )
    tg_cf = run_trialgpt_judge(
        trial_id=tid, trial_obj=trial, patient=patient_cf, engine=engine,
        out_root=work, model_name="gpt-4.1", temperature=0.0,
    )

    def lbl(e):
        return "eligible" if e is True else ("ineligible" if e is False else "unknown")

    llm_cf_e = (llm_cf.get("result") or {}).get("eligible")
    tg_cf_e = (tg_cf.get("aggregate") or {}).get("eligible")
    satir_flip = r.get("flipped")

    result = {
        "pair": pair,
        "original_labels": {
            "satir": "ineligible",  # from CF candidate selection
            "llm_d": lbl(orig["llm_d"]),
            "tg": lbl(orig["tg"]),
        },
        "cf_labels": {
            "satir": "eligible" if satir_flip else "ineligible",
            "llm_d": lbl(llm_cf_e),
            "tg": lbl(tg_cf_e),
        },
        "cf_flips": {
            "satir": bool(satir_flip),
            "llm_d": lbl(orig["llm_d"]) != lbl(llm_cf_e),
            "tg": lbl(orig["tg"]) != lbl(tg_cf_e),
        },
        "n_targets": r.get("n_targets"),
    }
    (out_dir/pair/"result.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def main():
    data_root = pathlib.Path("/tmp/satir_full_dataset")
    engine = AzureInferenceEngine(
        endpoint=os.environ["OPENAI_ENDPOINT"], api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-4.1", default_temperature=0.0,
    )

    pair_dirs = [p for p in SRC.iterdir() if p.is_dir() and (p/"result.json").exists()]
    print(f"Running LLM-d + TG on {len(pair_dirs)} SatIR v3 minimal CF charts → {OUT}", file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(process_one, pd, engine, data_root, OUT): pd for pd in pair_dirs}
        for i, fut in enumerate(as_completed(futs), 1):
            pd = futs[fut]
            try:
                r = fut.result()
                results.append(r)
                fl = r.get("cf_flips", {})
                nt = r.get("n_targets", "?")
                print(f"  [{i}/{len(pair_dirs)}] {r['pair']} (t={nt}): satir={fl.get('satir')} llm_d={fl.get('llm_d')} tg={fl.get('tg')}", file=sys.stderr)
            except Exception as e:
                print(f"  FAIL {pd.name}: {type(e).__name__}: {e}", file=sys.stderr)
            (OUT/"all_results.json").write_text(json.dumps(results, indent=2, default=str))

    valid = [r for r in results if r.get("cf_flips")]
    s = sum(1 for r in valid if r["cf_flips"].get("satir"))
    l = sum(1 for r in valid if r["cf_flips"].get("llm_d"))
    t = sum(1 for r in valid if r["cf_flips"].get("tg"))
    n = len(valid)
    print()
    print(f"MINIMAL-EDIT SHARED-CHART CF (SatIR's v3 CFs applied to all 3), n={n}")
    print(f"  SatIR:  {s}/{n} = {s/n:.1%}  (known)")
    print(f"  LLM-d:  {l}/{n} = {l/n:.1%}")
    print(f"  TG:     {t}/{n} = {t/n:.1%}")


if __name__ == "__main__":
    main()
