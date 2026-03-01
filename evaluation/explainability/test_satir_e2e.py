"""Smoke test: can we run SatIR end-to-end on a counterfactual chart?"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from smt_matcher.match_patient_to_trial import run_match_for_side, Config
from smt_core.inference_engine import AzureInferenceEngine


def main() -> None:
    # Pick one CF pair where SatIR was ineligible; try to re-match on CF chart.
    pair = "sigir-20144__NCT01041261"
    pid, tid = pair.split("__")

    # Load the CF chart from our earlier counterfactual_tg_blockers_20 output
    cf_result = json.load(open(
        ROOT / "evaluation/results/counterfactual_tg_blockers_20" / pair / "result.json"
    ))
    cf_chart = cf_result["tg_blocker_cf_chart"]
    orig_chart = cf_result["original_chart"]

    print(f"Testing SatIR end-to-end on {pair}")
    print(f"CF chart (first 200): {cf_chart[:200]}")

    endpoint = os.environ.get("OPENAI_ENDPOINT")
    engine = AzureInferenceEngine(
        endpoint=endpoint, api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-4.1", default_temperature=0.0,
    )
    cfg = Config()

    patient_cf = {"patient_id": pid, "_id": pid, "text": cf_chart, "metadata": {}}

    results = {}
    for side in ("inclusion", "exclusion"):
        print(f"\nRunning SatIR on {side} side with CF chart...")
        try:
            r = run_match_for_side(side, tid, patient_cf, cfg, engine, None)
            print(f"  sat_like: {r.get('sat_like')}")
            status = (r.get("raw") or {}).get("eval_result", {}).get("status", "?")
            print(f"  status: {status}")
            results[side] = r
        except Exception as e:
            print(f"  FAIL: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            return

    inc_sat = results["inclusion"].get("sat_like")
    exc_sat = results["exclusion"].get("sat_like")
    eligible_cf = (inc_sat is not False) and (exc_sat is not False)
    print(f"\nSatIR CF decision: eligible={eligible_cf}")
    print(f"Original SatIR decision: ineligible")
    print(f"Flipped? {eligible_cf}")


if __name__ == "__main__":
    main()
