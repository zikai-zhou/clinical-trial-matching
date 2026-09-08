"""Run SatIR on TG's CF charts (60 pairs)."""
from __future__ import annotations
import json, os, pathlib, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')
sys.path.insert(0, str(ROOT))
from smt_core.inference_engine import AzureInferenceEngine
from smt_matcher.match_patient_to_trial import run_match_for_side, Config

R = ROOT/'evaluation'/'results'

def process(pair, cf_chart, engine, cfg):
    pid, tid = pair.split('__')
    patient_cf = {'_id': pid, 'patient_id': pid, 'text': cf_chart, 'metadata': {}}
    try:
        inc = run_match_for_side('inclusion', tid, patient_cf, cfg, engine, None)
        exc = run_match_for_side('exclusion', tid, patient_cf, cfg, engine, None)
        inc_sat = inc.get('sat_like'); exc_sat = exc.get('sat_like')
        elig = (inc_sat is not False) and (exc_sat is not False)
        return {'pair': pair, 'smt_flipped': elig, 'inc_sat': inc_sat, 'exc_sat': exc_sat}
    except Exception as e:
        return {'pair': pair, 'error': f'{type(e).__name__}: {e}'}


def main():
    engine = AzureInferenceEngine(
        endpoint=os.environ['OPENAI_ENDPOINT'], api_key_env_var='OPENAI_API_KEY',
        model_name='gpt-4.1', default_temperature=0.0,
    )
    cfg = Config()

    agg = json.load(open(R/'counterfactual_tg_blockers_60'/'all_results.json'))
    pairs_with_charts = [(r['pair'], r.get('tg_blocker_cf_chart', '')) for r in agg if r.get('tg_blocker_cf_chart')]
    print(f"Running SatIR on {len(pairs_with_charts)} TG CFs", file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(process, p, c, engine, cfg): p for p, c in pairs_with_charts}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                r = fut.result()
                results.append(r)
                if 'smt_flipped' in r:
                    print(f"  [{i}/{len(pairs_with_charts)}] {r['pair']}: smt_flipped={r['smt_flipped']}", file=sys.stderr)
                else:
                    print(f"  [{i}/{len(pairs_with_charts)}] {r['pair']}: ERROR {r.get('error')}", file=sys.stderr)
            except Exception as e:
                print(f"  FAIL: {e}", file=sys.stderr)
            pathlib.Path('/tmp/satir_on_tg_cf_60.json').write_text(json.dumps(results, indent=2, default=str))

    flipped = sum(1 for r in results if r.get('smt_flipped'))
    valid = sum(1 for r in results if 'smt_flipped' in r)
    print(f"\nSatIR on TG CFs: {flipped}/{valid} = {flipped/max(valid,1):.1%}")


if __name__ == '__main__':
    main()
