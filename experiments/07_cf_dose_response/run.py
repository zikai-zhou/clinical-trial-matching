"""Dose-response CF: vary the number of target facts flipped in the CF chart.
For each pair, generate CFs at dose levels: 1, 2, 3, all. Run all 3 systems.

Hypothesis: a faithful system should show monotonically increasing flip rate
with dose. Anchored systems may step-function. Rationalizing systems may
respond noisily.
"""
from __future__ import annotations
import json, os, pathlib, sys, random
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from smt_core.inference_engine import AzureInferenceEngine  # noqa: E402
from smt_matcher.judges import run_llm_eligibility_judge, run_trialgpt_judge  # noqa: E402
from smt_matcher.match_patient_to_trial import run_match_for_side, Config  # noqa: E402

R = ROOT/'evaluation'/'results'
SRC = R/'counterfactual_satir_minimal_60_v3'
OUT = R/(os.environ.get('CF_OUT') or 'cf_dose_response_60')
OUT.mkdir(parents=True, exist_ok=True)

DOSES = [1, 2, 3, 'all']

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


DOSE_PROMPT = """Rewrite the patient chart to flip EXACTLY these specific facts:

#TARGETS#

Keep all other content identical. Make only the minimum edits necessary
to assert each listed fact. Do NOT assert or flip any other facts.

ORIGINAL CHART:
#CHART#

Return the COMPLETE rewritten chart. No preamble.
"""


def gen_dose_cf(engine, chart, targets_subset):
    tgt_str = "\n".join(f"- {k} = {v}" for k, v in targets_subset.items())
    prompt = DOSE_PROMPT.replace('#CHART#', chart).replace('#TARGETS#', tgt_str)
    raw = engine(prompt, temperature=0.0)
    if isinstance(raw, list): raw = raw[0] if raw else ''
    return raw.strip()


def process_one_dose(pair, original_chart, all_targets, dose, engine, cfg, data_root, out_dir):
    pid, tid = pair.split('__')
    trial = load_trial(tid, data_root)
    if not trial: return {'pair': pair, 'dose': dose, 'error': 'no trial'}

    # Select targets based on dose (deterministic order)
    sorted_targets = list(all_targets.items())
    n = len(sorted_targets) if dose == 'all' else min(dose, len(sorted_targets))
    subset = dict(sorted_targets[:n])
    if not subset:
        return {'pair': pair, 'dose': dose, 'error': 'no targets'}

    cf_chart = gen_dose_cf(engine, original_chart, subset)
    patient = {'_id': pid, 'patient_id': pid, 'text': cf_chart, 'metadata': {}}
    work = out_dir/pair/f"dose_{dose}"; work.mkdir(parents=True, exist_ok=True)
    try:
        llm_res = run_llm_eligibility_judge(
            trial_id=tid, trial_obj=trial, patient=patient, engine=engine,
            prompt_path=ROOT/'smt_matcher'/'prompts'/'clinical_trial'/'SMTMatcher'/'eligibility.explicit.prompt',
            out_root=work, model_name='gpt-4.1', temperature=0.0)
        tg_res = run_trialgpt_judge(trial_id=tid, trial_obj=trial, patient=patient, engine=engine,
                                    out_root=work, model_name='gpt-4.1', temperature=0.0)
        inc = run_match_for_side('inclusion', tid, patient, cfg, engine, None)
        exc = run_match_for_side('exclusion', tid, patient, cfg, engine, None)
        smt_e = (inc.get('sat_like') is not False) and (exc.get('sat_like') is not False)
    except Exception as e:
        return {'pair': pair, 'dose': dose, 'error': f'{type(e).__name__}: {e}'}
    llm_e = (llm_res.get('result') or {}).get('eligible')
    tg_e = (tg_res.get('aggregate') or {}).get('eligible')
    return {
        'pair': pair, 'dose': dose, 'n_targets_applied': n, 'n_total_targets': len(all_targets),
        'cf_chart': cf_chart,
        'cf_flips': {'smt': smt_e is True, 'llm_d': llm_e is True, 'tg': tg_e is True},
    }


def main():
    data_root = pathlib.Path('/tmp/satir_full_dataset')
    engine = AzureInferenceEngine(
        endpoint=os.environ['OPENAI_ENDPOINT'], api_key_env_var='OPENAI_API_KEY',
        model_name='gpt-4.1', default_temperature=0.0)
    cfg = Config()

    # Load SatIR v3 pairs + targets + original chart
    pair_data = []
    for pd in SRC.iterdir():
        if not pd.is_dir(): continue
        try:
            r = json.load(open(pd/'result.json'))
            if r.get('targets') and r.get('original_chart'):
                pair_data.append((r['pair'], r['original_chart'], r['targets']))
        except: pass
    print(f"Dose-response on {len(pair_data)} pairs × {len(DOSES)} doses = {len(pair_data)*len(DOSES)} trials", file=sys.stderr)

    results = []
    tasks = [(p, c, t, d) for p, c, t in pair_data for d in DOSES]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(process_one_dose, p, c, t, d, engine, cfg, data_root, OUT): (p, d) for p, c, t, d in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            pair, dose = futs[fut]
            try:
                r = fut.result(); results.append(r)
                if 'cf_flips' in r:
                    fl = r['cf_flips']
                    print(f"  [{i}/{len(tasks)}] {pair} dose={dose}: smt={fl.get('smt')} llm_d={fl.get('llm_d')} tg={fl.get('tg')}", file=sys.stderr)
            except Exception as e:
                print(f"  FAIL {pair} dose={dose}: {e}", file=sys.stderr)
            (OUT/'all_results.json').write_text(json.dumps(results, indent=2, default=str))

    # Tally by dose
    print("\nBy dose:")
    for d in DOSES:
        subset = [r for r in results if r.get('dose') == d and 'cf_flips' in r]
        if not subset: continue
        for sys_ in ('smt','llm_d','tg'):
            f = sum(1 for r in subset if r['cf_flips'].get(sys_))
            print(f"  dose={d} {sys_}: {f}/{len(subset)} = {f/len(subset):.1%}")


if __name__ == '__main__': main()
