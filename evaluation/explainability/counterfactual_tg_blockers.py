"""TG-blocker-targeted counterfactual probe.

For each INELIGIBLE pair (per TG), extract TG's per-criterion verdicts and
identify the criteria TG marked as "not included" or "excluded". Build a
counterfactual chart that flips exactly those criteria to "included" or
"not excluded". Rerun TG.

Under correct causal faithfulness, TG's decision should flip 100%. Non-flips
mean TG's ineligible verdict wasn't actually driven by the criteria it
cited as failing.

Usage:
    python -m evaluation.explainability.counterfactual_tg_blockers
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
from smt_matcher.judges import run_trialgpt_judge, run_llm_eligibility_judge  # noqa: E402

RESULTS = ROOT / "evaluation" / "results"
V3 = RESULTS / "verbalize_judge_235_v3"
OUT = RESULTS / (os.environ.get("CF_OUT") or "counterfactual_tg_blockers_20")
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


def extract_tg_blockers(pd):
    """Return list of blocker criteria from TG's per-criterion output."""
    tg = json.load(open(pd / "trialgpt_decision.json"))
    blockers = []
    for side in ("inclusion", "exclusion"):
        x = tg.get(side, {}) or {}
        criteria_list = x.get("criteria", []) or []
        rows = x.get("rows", []) or []
        for row in rows:
            label = (row.get("label") or "").lower()
            cid = row.get("criterion_id")
            crit_text = criteria_list[cid] if cid is not None and cid < len(criteria_list) else ""
            reasoning = row.get("reasoning", "")
            is_blocker = False
            target = ""
            if side == "inclusion" and label == "not included":
                is_blocker = True
                target = "include (make chart explicitly satisfy this criterion)"
            elif side == "exclusion" and label == "excluded":
                is_blocker = True
                target = "not excluded (make chart explicitly not trigger this exclusion)"
            if is_blocker:
                blockers.append({
                    "side": side,
                    "criterion_id": cid,
                    "criterion": crit_text,
                    "current_label": label,
                    "target": target,
                    "tg_reasoning": reasoning[:300],
                })
    return blockers


TG_BLOCKER_CF_PROMPT = """You are rewriting a clinical patient chart to flip specific criteria a
clinical trial screening system (TrialGPT) marked as failing.

ORIGINAL CHART:
#CHART#

FULL TRIAL (for context):
#TRIAL_TEXT#

CRITERIA TG MARKED AS BLOCKERS (flip each to resolve):
#BLOCKERS#

Your task: rewrite the chart so that each listed blocker is resolved —
inclusion criteria are now explicitly satisfied by a chart statement, and
exclusion criteria are now explicitly not triggered by the chart. Make
minimal edits; keep everything else identical.

Return the COMPLETE rewritten chart. No preamble, no commentary.
"""


def gen_tg_cf(engine, chart, trial, blockers):
    blockers_str = "\n".join(
        f"- [{b['side']}] '{b['criterion']}' (TG said: {b['current_label']}). "
        f"Target: {b['target']}."
        for b in blockers[:15]
    )
    trial_text = (trial.get("inclusion_criteria", "")[:2000]
                  + "\n\nExclusion:\n" + trial.get("exclusion_criteria", "")[:1000])
    prompt = (TG_BLOCKER_CF_PROMPT
              .replace("#CHART#", chart)
              .replace("#TRIAL_TEXT#", trial_text)
              .replace("#BLOCKERS#", blockers_str))
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

    blockers = extract_tg_blockers(pd)
    if not blockers:
        return {"pair": pair, "error": "no tg blockers"}

    chart = load_patient(pid, data_root)
    trial = load_trial(tid, data_root)
    if not chart or not trial:
        return {"pair": pair, "error": "missing data"}

    tg_orig = (json.load(open(pd / "trialgpt_decision.json")).get("aggregate") or {}).get("eligible")
    llm_orig = (json.load(open(pd / "llm_direct_decision.json")).get("result") or {}).get("eligible")

    def lbl(e):
        return "eligible" if e is True else ("ineligible" if e is False else "unknown")

    cf_chart = gen_tg_cf(engine, chart, trial, blockers)

    patient_cf = {"_id": pid, "patient_id": pid, "text": cf_chart, "metadata": {}}
    work = out_dir / pair / "_work"
    work.mkdir(parents=True, exist_ok=True)

    tg_cf = run_trialgpt_judge(
        trial_id=tid, trial_obj=trial, patient=patient_cf, engine=engine,
        out_root=work, model_name="gpt-4.1", temperature=0.0,
    )
    llm_cf = run_llm_eligibility_judge(
        trial_id=tid, trial_obj=trial, patient=patient_cf, engine=engine,
        prompt_path=ROOT / "smt_matcher" / "prompts" / "clinical_trial" / "SMTMatcher" / "eligibility.explicit.prompt",
        out_root=work, model_name="gpt-4.1", temperature=0.0,
    )

    tg_cf_e = (tg_cf.get("aggregate") or {}).get("eligible")
    llm_cf_e = (llm_cf.get("result") or {}).get("eligible")

    result = {
        "pair": pair,
        "n_tg_blockers": len(blockers),
        "blockers_sample": blockers[:5],
        "original_chart": chart,
        "tg_blocker_cf_chart": cf_chart,
        "original_labels": {"tg": lbl(tg_orig), "llm_d": lbl(llm_orig)},
        "cf_labels": {"tg": lbl(tg_cf_e), "llm_d": lbl(llm_cf_e)},
        "cf_flips": {
            "tg": lbl(tg_orig) != lbl(tg_cf_e),
            "llm_d": lbl(llm_orig) != lbl(llm_cf_e),
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
    print(f"TG-blockers CF probe on {len(candidates)} pairs → {OUT}", file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(process_one, c, engine, data_root, OUT): c for c in candidates}
        for i, fut in enumerate(as_completed(futs), 1):
            c = futs[fut]
            try:
                r = fut.result()
                results.append(r)
                flips = r.get("cf_flips", {})
                nb = r.get("n_tg_blockers", "?")
                print(f"  [{i}/{len(candidates)}] {r['pair']} (TG had {nb} blockers): "
                      f"tg_flip={flips.get('tg')} llm_d_flip={flips.get('llm_d')}",
                      file=sys.stderr)
            except Exception as e:
                print(f"  FAIL {c['pair']}: {type(e).__name__}: {e}", file=sys.stderr)

    (OUT / "all_results.json").write_text(json.dumps(results, indent=2))

    # Aggregate only over pairs where TG was originally ineligible (could flip)
    tg_could = [r for r in results if r.get("original_labels", {}).get("tg") == "ineligible"]
    tg_flipped = [r for r in tg_could if r.get("cf_flips", {}).get("tg")]
    llm_could = [r for r in results if r.get("original_labels", {}).get("llm_d") == "ineligible"]
    llm_flipped = [r for r in llm_could if r.get("cf_flips", {}).get("llm_d")]

    print()
    print(f"TG-BLOCKERS CF PROBE:")
    if tg_could:
        print(f"  TG flipped: {len(tg_flipped)}/{len(tg_could)} = {len(tg_flipped)/len(tg_could):.1%}")
    if llm_could:
        print(f"  LLM-d flipped: {len(llm_flipped)}/{len(llm_could)} = {len(llm_flipped)/len(llm_could):.1%}")
    print()
    print("Interpretation: we flipped exactly the criteria TG marked as blockers.")
    print("If TG's decision flipped, TG was causally using those criteria. If not,")
    print("TG was applying silence-as-failure on OTHER unstated criteria or some")
    print("non-causal default.")


if __name__ == "__main__":
    main()
