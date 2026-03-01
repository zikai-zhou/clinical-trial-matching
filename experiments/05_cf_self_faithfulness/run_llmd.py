"""LLM-direct minimal-blockers CF (pure NL reasoning).

For each INELIGIBLE pair where LLM-direct marked ineligible, extract the
blocking facts from LLM-d's own natural-language explanation. Rewrite the
chart to flip exactly those facts. Rerun LLM-d.

Key point: no structured extraction. We use GPT-4.1 to read LLM-d's prose
rationale and produce a rewritten chart that flips only what LLM-d cited.
This tests causal faithfulness on LLM-d's own natural-language reasoning.

Usage:
    python -m evaluation.explainability.counterfactual_llm_direct_blockers
"""
from __future__ import annotations
import json
import os
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine import AzureInferenceEngine  # noqa: E402
from smt_matcher.judges import run_llm_eligibility_judge, run_trialgpt_judge  # noqa: E402

RESULTS = ROOT / "evaluation" / "results"
V3 = RESULTS / "verbalize_judge_235_v3"
OUT = RESULTS / (os.environ.get("CF_OUT") or "counterfactual_llm_blockers_20")
OUT.mkdir(parents=True, exist_ok=True)


def load_patient(pid, data_root):
    with open(data_root / "sigir" / "queries.jsonl") as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("_id") == pid:
                return obj.get("text", "")
    return ""


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


LLM_BLOCKER_CF_PROMPT = """You are rewriting a clinical patient chart to flip specific facts a
clinical trial screening system cited as the reason for ineligibility.

ORIGINAL CHART:
#CHART#

TRIAL CRITERIA (for context):
#TRIAL_TEXT#

THE SYSTEM'S NATURAL-LANGUAGE EXPLANATION (why it said ineligible):
#LLM_EXPLANATION#

Your task:
1. Identify EVERY chart fact the system cites in its explanation as a reason
   for ineligibility.
2. Rewrite the chart so that each such fact is flipped to the opposite value
   (e.g., "58-year-old" → within the trial's required range; "African-American"
   → the trial's required race; "woman" → the trial's required sex; "nonsmoker"
   → "smokes"; "no diabetes" → "has diabetes"; etc.).
3. Change ONLY the facts the system cited. Keep everything else identical
   (or make minimal phrasing adjustments for flow).

The goal is to produce a chart that explicitly addresses every concern
raised in the system's explanation. If the system's explanation cites 4
distinct facts as reasons for ineligibility, flip all 4.

Return the COMPLETE rewritten chart. No preamble, no commentary.
"""


def gen_llm_cf(engine, chart, trial, llm_explanation):
    trial_text = (trial.get("inclusion_criteria", "")[:1500]
                  + "\n\nExclusion:\n" + trial.get("exclusion_criteria", "")[:800])
    prompt = (LLM_BLOCKER_CF_PROMPT
              .replace("#CHART#", chart)
              .replace("#TRIAL_TEXT#", trial_text)
              .replace("#LLM_EXPLANATION#", llm_explanation[:1000]))
    raw = engine(prompt, temperature=0.0)
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return raw.strip()


def process_one(candidate, engine, data_root, out_dir):
    pair = candidate["pair"]
    pid, tid = pair.split("__")
    pd = None
    for shard in V3.glob("shard_*"):
        cand = shard / pair
        if cand.exists():
            pd = cand; break
    if pd is None:
        return {"pair": pair, "error": "no pair_dir"}

    llm_dec = json.load(open(pd / "llm_direct_decision.json"))
    result_obj = llm_dec.get("result") or {}
    llm_orig_e = result_obj.get("eligible")
    llm_explanation = result_obj.get("explanation") or result_obj.get("reasoning") or ""
    if not llm_explanation:
        return {"pair": pair, "error": "no llm explanation"}
    if llm_orig_e is True:
        return {"pair": pair, "error": "llm_d was already eligible"}

    chart = load_patient(pid, data_root)
    trial = load_trial(tid, data_root)
    if not chart or not trial:
        return {"pair": pair, "error": "missing data"}

    tg_orig = (json.load(open(pd / "trialgpt_decision.json")).get("aggregate") or {}).get("eligible")

    def lbl(e):
        return "eligible" if e is True else ("ineligible" if e is False else "unknown")

    cf_chart = gen_llm_cf(engine, chart, trial, llm_explanation)

    patient_cf = {"_id": pid, "patient_id": pid, "text": cf_chart, "metadata": {}}
    work = out_dir / pair / "_work"
    work.mkdir(parents=True, exist_ok=True)

    llm_cf = run_llm_eligibility_judge(
        trial_id=tid, trial_obj=trial, patient=patient_cf, engine=engine,
        prompt_path=ROOT / "smt_matcher" / "prompts" / "clinical_trial" / "SMTMatcher" / "eligibility.explicit.prompt",
        out_root=work, model_name="gpt-4.1", temperature=0.0,
    )
    tg_cf = run_trialgpt_judge(
        trial_id=tid, trial_obj=trial, patient=patient_cf, engine=engine,
        out_root=work, model_name="gpt-4.1", temperature=0.0,
    )

    llm_cf_e = (llm_cf.get("result") or {}).get("eligible")
    tg_cf_e = (tg_cf.get("aggregate") or {}).get("eligible")

    result = {
        "pair": pair,
        "llm_original_explanation": llm_explanation,
        "original_chart": chart,
        "llm_blocker_cf_chart": cf_chart,
        "llm_post_cf_explanation": (llm_cf.get("result") or {}).get("explanation", ""),
        "original_labels": {"llm_d": lbl(llm_orig_e), "tg": lbl(tg_orig)},
        "cf_labels": {"llm_d": lbl(llm_cf_e), "tg": lbl(tg_cf_e)},
        "cf_flips": {
            "llm_d": lbl(llm_orig_e) != lbl(llm_cf_e),
            "tg": lbl(tg_orig) != lbl(tg_cf_e),
        },
    }
    (out_dir / pair / "result.json").write_text(json.dumps(result, indent=2))
    return result


def main() -> None:
    data_root = pathlib.Path("/tmp/satir_full_dataset")
    endpoint = os.environ.get("OPENAI_ENDPOINT")
    if not endpoint:
        print("FATAL: OPENAI_ENDPOINT must be set", file=sys.stderr); sys.exit(2)

    engine = AzureInferenceEngine(
        endpoint=endpoint, api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-4.1", default_temperature=0.0,
    )

    _cf = os.environ.get("CF_CANDIDATES", "/tmp/counterfactual_candidates.json"); candidates = json.load(open(_cf))
    print(f"LLM-d-blockers CF probe on {len(candidates)} pairs → {OUT}", file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(process_one, c, engine, data_root, OUT): c for c in candidates}
        for i, fut in enumerate(as_completed(futs), 1):
            c = futs[fut]
            try:
                r = fut.result()
                results.append(r)
                flips = r.get("cf_flips", {})
                print(f"  [{i}/{len(candidates)}] {r['pair']}: llm_d_flip={flips.get('llm_d')} tg_flip={flips.get('tg')}",
                      file=sys.stderr)
            except Exception as e:
                print(f"  FAIL {c['pair']}: {type(e).__name__}: {e}", file=sys.stderr)

    (OUT / "all_results.json").write_text(json.dumps(results, indent=2))

    valid = [r for r in results if "cf_labels" in r]
    llm_flip = sum(1 for r in valid if r.get("cf_flips", {}).get("llm_d"))
    tg_flip = sum(1 for r in valid if r.get("cf_flips", {}).get("tg"))
    print()
    print(f"LLM-D-BLOCKERS CF PROBE (n={len(valid)}):")
    print(f"  LLM-d flipped: {llm_flip}/{len(valid)} = {llm_flip/len(valid):.1%}" if valid else "n=0")
    print(f"  TG flipped:    {tg_flip}/{len(valid)} = {tg_flip/len(valid):.1%}" if valid else "n=0")
    print()
    print("Interpretation: we flipped exactly the chart facts LLM-direct cited in")
    print("its own natural-language explanation. If LLM-d is causally faithful to")
    print("its own reasoning, its flip rate should be 100%.")


if __name__ == "__main__":
    main()
