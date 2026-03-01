"""Noise-injection CF: take each system's CF chart and add 2-3 irrelevant facts
(e.g., hair color, favorite food, random demographic). Rerun all 3 systems.

Hypothesis: a faithful system ignores irrelevant changes and still flips on
the real targeted facts. An unfaithful system might get confused and fail
to flip (false negative) or incorrectly flip (false positive).

Compares flip rates with vs without noise.
"""
from __future__ import annotations
import json, os, pathlib, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from smt_core.inference_engine import AzureInferenceEngine  # noqa: E402
from smt_matcher.judges import run_llm_eligibility_judge, run_trialgpt_judge  # noqa: E402
from smt_matcher.match_patient_to_trial import run_match_for_side, Config  # noqa: E402

ROOT_DIR = ROOT
R = ROOT/'evaluation'/'results'
OUT = R/(os.environ.get('CF_OUT') or 'cf_noise_injection_60')
OUT.mkdir(parents=True, exist_ok=True)


def load_trial(tid, data_root):
    with open(data_root/'sigir'/'corpus.jsonl') as f:
        for line in f:
            obj = json.loads(line)
            if obj.get('_id') == tid:
                md = obj.get('metadata') or {}
                return {'nct_id': tid, 'brief_title': md.get('brief_title',''),
                        'brief_summary': md.get('brief_summary',''),
                        'inclusion_criteria': md.get('inclusion_criteria',''),
                        'exclusion_criteria': md.get('exclusion_criteria',''),
                        'metadata': md}
    return None


NOISE_PROMPT = """Add 3-4 clinically plausible but TRIAL-IRRELEVANT details to this chart.
Examples: favorite foods, hobby, pet ownership, last vacation, handedness,
eye color, family tree details unrelated to medical history.

DO NOT change any medical facts already in the chart. Just interleave
3-4 irrelevant sentences naturally into the existing narrative.

ORIGINAL CHART:
#CHART#

Return the COMPLETE chart with noise added. No preamble.
"""


def add_noise(engine, chart):
    raw = engine(NOISE_PROMPT.replace('#CHART#', chart), temperature=0.0)
    if isinstance(raw, list): raw = raw[0] if raw else ''
    return raw.strip()


def process_one(pair, cf_chart, engine, cfg, data_root, out_dir):
    pid, tid = pair.split('__')
    trial = load_trial(tid, data_root)
    if not trial: return {'pair': pair, 'error': 'no trial'}
    noisy = add_noise(engine, cf_chart)
    patient = {'_id': pid, 'patient_id': pid, 'text': noisy, 'metadata': {}}
    work = out_dir/pair/'_work'; work.mkdir(parents=True, exist_ok=True)
    try:
        llm_res = run_llm_eligibility_judge(
            trial_id=tid, trial_obj=trial, patient=patient, engine=engine,
            prompt_path=ROOT_DIR/'smt_matcher'/'prompts'/'clinical_trial'/'SMTMatcher'/'eligibility.explicit.prompt',
            out_root=work, model_name='gpt-4.1', temperature=0.0)
        tg_res = run_trialgpt_judge(trial_id=tid, trial_obj=trial, patient=patient, engine=engine,
                                    out_root=work, model_name='gpt-4.1', temperature=0.0)
        inc = run_match_for_side('inclusion', tid, patient, cfg, engine, None)
        exc = run_match_for_side('exclusion', tid, patient, cfg, engine, None)
        smt_e = (inc.get('sat_like') is not False) and (exc.get('sat_like') is not False)
    except Exception as e:
        return {'pair': pair, 'error': f'{type(e).__name__}: {e}'}
    llm_e = (llm_res.get('result') or {}).get('eligible')
    tg_e = (tg_res.get('aggregate') or {}).get('eligible')
    result = {
        'pair': pair,
        'noisy_cf_chart': noisy,
        'cf_flips': {'smt': smt_e is True, 'llm_d': llm_e is True, 'tg': tg_e is True},
    }
    (out_dir/pair/'result.json').write_text(json.dumps(result, indent=2, default=str))
    return result


def main():
    data_root = pathlib.Path('/tmp/satir_full_dataset')
    engine = AzureInferenceEngine(
        endpoint=os.environ['OPENAI_ENDPOINT'], api_key_env_var='OPENAI_API_KEY',
        model_name='gpt-4.1', default_temperature=0.0)
    cfg = Config()
    # Use SatIR's v3 minimal CF charts as the base
    SRC = R/'counterfactual_satir_minimal_60_v3'
    pair_charts = []
    for pd in SRC.iterdir():
        if not pd.is_dir(): continue
        try:
            r = json.load(open(pd/'result.json'))
            if r.get('minimal_cf_chart'):
                pair_charts.append((r['pair'], r['minimal_cf_chart']))
        except: pass
    print(f"Noise-injection on {len(pair_charts)} SatIR v3 CFs", file=sys.stderr)
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(process_one, p, c, engine, cfg, data_root, OUT): p for p, c in pair_charts}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                r = fut.result(); results.append(r)
                fl = r.get('cf_flips', {})
                print(f"  [{i}/{len(pair_charts)}] {r['pair']}: smt={fl.get('smt')} llm_d={fl.get('llm_d')} tg={fl.get('tg')}", file=sys.stderr)
            except Exception as e:
                print(f"  FAIL: {e}", file=sys.stderr)
            (OUT/'all_results.json').write_text(json.dumps(results, indent=2, default=str))
    valid = [r for r in results if r.get('cf_flips')]
    for sys_ in ('smt','llm_d','tg'):
        f = sum(1 for r in valid if r['cf_flips'].get(sys_))
        print(f"  {sys_}: {f}/{len(valid)} = {f/max(len(valid),1):.1%}")

if __name__ == '__main__': main()
