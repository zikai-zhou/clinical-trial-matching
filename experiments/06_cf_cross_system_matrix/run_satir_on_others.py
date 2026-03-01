"""Run SatIR on LLM-d's and TG's existing CF charts to fill 3×3 matrix."""
from __future__ import annotations
import json, os, pathlib, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')
sys.path.insert(0, str(ROOT))
from smt_core.inference_engine import AzureInferenceEngine
from smt_matcher.match_patient_to_trial import run_match_for_side, Config

R = ROOT/'evaluation'/'results'

def process(pair, chart_field_name, cf_dir, engine, cfg):
    pair_path = cf_dir/pair/'result.json'
    if not pair_path.exists():
        # try aggregate
        agg = json.load(open(cf_dir/'all_results.json'))
        row = next((r for r in agg if r['pair'] == pair), None)
        if not row: return None
        cf_chart = row.get(chart_field_name, '')
    else:
        row = json.load(open(pair_path))
        cf_chart = row.get(chart_field_name, '')
    if not cf_chart:
        return {'pair': pair, 'error': 'no CF chart'}
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

    for cf_name, cf_dir, chart_field, out_file in [
        ('LLM-d CF', R/'counterfactual_llm_blockers_60', 'llm_blocker_cf_chart', '/tmp/satir_on_llmd_cf_60.json'),
        ('TG CF',    R/'counterfactual_tg_blockers_60',  'tg_blocker_cf_chart',  '/tmp/satir_on_tg_cf_60.json'),
    ]:
        print(f"\n=== Running SatIR on {cf_name} (60 pairs) ===", file=sys.stderr)
        agg = json.load(open(cf_dir/'all_results.json'))
        pairs = [r['pair'] for r in agg]
        results = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = {pool.submit(process, p, chart_field, cf_dir, engine, cfg): p for p in pairs}
            for i, fut in enumerate(as_completed(futs), 1):
                try:
                    r = fut.result()
                    if r: results.append(r)
                    if r and 'smt_flipped' in r:
                        print(f"  [{i}/{len(pairs)}] {r['pair']}: smt_flipped={r['smt_flipped']}", file=sys.stderr)
                except Exception as e:
                    print(f"  FAIL: {e}", file=sys.stderr)
                pathlib.Path(out_file).write_text(json.dumps(results, indent=2, default=str))
        flipped = sum(1 for r in results if r.get('smt_flipped'))
        n_valid = sum(1 for r in results if 'smt_flipped' in r)
        print(f"SatIR on {cf_name}: {flipped}/{n_valid} = {flipped/max(n_valid,1):.1%}")


if __name__ == '__main__':
    main()
