"""Run SatIR end-to-end on the maximal-positive CF charts.

The maximal-positive CFs are constructed to satisfy every trial criterion
explicitly. Under correct SatIR behavior (miner extracts chart facts; solver
applies the deterministic prescreen decision rule), SatIR should flip 100%.

Any non-flip reveals either:
  - Miner failure on the CF chart (didn't extract explicit facts)
  - Constraint compilation mismatch (trial constraint narrower than criterion text)
"""
from __future__ import annotations
import json, os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine import AzureInferenceEngine
from smt_matcher.match_patient_to_trial import run_match_for_side, Config

RESULTS = ROOT / "evaluation" / "results"
V3 = RESULTS / "verbalize_judge_235_v3"
SRC = RESULTS / "counterfactual_maximal_20"  # 20-pair maximal CFs
OUT = RESULTS / "satir_on_maximal_cf_20"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    endpoint = os.environ.get("OPENAI_ENDPOINT")
    engine = AzureInferenceEngine(
        endpoint=endpoint, api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-4.1", default_temperature=0.0,
    )
    cfg = Config()

    source = json.load(open(SRC / "all_results.json"))
    valid = [r for r in source if "maximal_chart" in r]
    print(f"Running SatIR E2E on {len(valid)} maximal-positive CF charts", file=sys.stderr)

    results = []
    for i, r in enumerate(valid, 1):
        pair = r["pair"]
        pid, tid = pair.split("__")
        cf_chart = r["maximal_chart"]
        patient_cf = {"patient_id": pid, "_id": pid, "text": cf_chart, "metadata": {}}

        try:
            inc = run_match_for_side("inclusion", tid, patient_cf, cfg, engine, None)
            exc = run_match_for_side("exclusion", tid, patient_cf, cfg, engine, None)
            inc_sat = inc.get("sat_like")
            exc_sat = exc.get("sat_like")
            eligible = (inc_sat is not False) and (exc_sat is not False)
            status_inc = (inc.get("raw") or {}).get("eval_result", {}).get("status", "?")
            status_exc = (exc.get("raw") or {}).get("eval_result", {}).get("status", "?")
            out = {
                "pair": pair,
                "cf_inc_sat_like": inc_sat, "cf_exc_sat_like": exc_sat,
                "cf_inc_status": status_inc, "cf_exc_status": status_exc,
                "original_satir_decision": "ineligible",
                "cf_satir_decision": "eligible" if eligible else "ineligible",
                "flipped": eligible,
            }
            results.append(out)
            (OUT / "all_results.json").write_text(json.dumps(results, indent=2, default=str))
            print(f"  [{i}/{len(valid)}] {pair}: inc={status_inc} exc={status_exc} flipped={eligible}",
                  file=sys.stderr)
        except Exception as e:
            print(f"  FAIL {pair}: {type(e).__name__}: {e}", file=sys.stderr)
            results.append({"pair": pair, "error": str(e)})

    flipped = sum(1 for r in results if r.get("flipped"))
    valid_n = sum(1 for r in results if "flipped" in r)
    print(f"\nSatIR on maximal-positive CF: {flipped}/{valid_n} = {flipped/valid_n:.1%}")


if __name__ == "__main__":
    main()
