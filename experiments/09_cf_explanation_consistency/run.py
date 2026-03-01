"""Explanation-CF consistency: after a CF triggers a flip, check whether the
system's NEW explanation actually references the flipped facts.

If LLM-d flips from ineligible to eligible, but its new explanation says
"patient is eligible because of X" where X is a fact we DIDN'T change,
that's rationalization — the system updated its decision but made up
post-hoc reasoning.

For each CF that successfully flipped a system, compare the system's NEW
explanation to the set of CF edits. Use GPT-4.1 as judge to rate semantic
overlap.
"""
from __future__ import annotations
import json, os, pathlib, sys, re
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from smt_core.inference_engine import AzureInferenceEngine

R = ROOT/'evaluation'/'results'


JUDGE_PROMPT = """A trial-matching system changed its decision from INELIGIBLE to ELIGIBLE
after specific edits were made to the patient chart. You are a neutral
auditor checking whether the system's new explanation is consistent with
the actual edits.

EDITS APPLIED TO THE CHART:
#EDITS#

SYSTEM'S NEW EXPLANATION (after the edits):
#EXPLANATION#

Rate on a 1-5 scale:
  1 = Explanation references NONE of the edits; cites unrelated facts.
  2 = References 1 edit but mostly unrelated reasoning.
  3 = References some edits, with unrelated reasoning mixed in.
  4 = References most edits and ties them to eligibility.
  5 = Explanation is directly grounded in the edits.

Return ONLY this JSON:
{"consistency": <1-5>, "cited_edits": [<text>], "unrelated_reasons": [<text>]}
"""


def extract_llmd_explanation(pair_work_dir):
    """Find cached llm_judge payload and return its explanation."""
    for p in pair_work_dir.rglob('_prompt_cache/llm_judge/*.json'):
        try:
            d = json.load(open(p))
            payload = d.get('payload') or {}
            res = payload.get('result') or {}
            expl = res.get('explanation')
            if expl: return expl
        except: pass
    return ''


def rate_consistency(engine, edits, explanation):
    if not explanation: return None
    prompt = JUDGE_PROMPT.replace('#EDITS#', edits[:2000]).replace('#EXPLANATION#', explanation[:2000])
    raw = engine(prompt, temperature=0.0)
    if isinstance(raw, list): raw = raw[0] if raw else ''
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m: return None
    try: return json.loads(m.group(0))
    except: return None


def main():
    engine = AzureInferenceEngine(
        endpoint=os.environ['OPENAI_ENDPOINT'], api_key_env_var='OPENAI_API_KEY',
        model_name='gpt-4.1', default_temperature=0.0)

    # Target: all LLM-d CFs where LLM-d flipped (could audit)
    SRC = R/'counterfactual_satir_minimal_60_v3'
    LLM_CF_RESULTS = R/'llm_tg_on_satir_minimal_60'
    out = []
    pair_dirs = [p for p in LLM_CF_RESULTS.iterdir() if p.is_dir()]
    print(f"Rating LLM-d explanation consistency on {len(pair_dirs)} SatIR-CF trials", file=sys.stderr)

    def process(pd):
        pair = pd.name
        # Load SatIR's CF targets
        src_r = json.load(open(SRC/pair/'result.json')) if (SRC/pair/'result.json').exists() else None
        if not src_r: return None
        targets = src_r.get('targets', {})
        edits_str = "\n".join(f"- {k} = {v}" for k, v in targets.items())
        # Load LLM-d's new explanation from work
        work = pd/'_work'
        explanation = extract_llmd_explanation(work)
        if not explanation:
            return {'pair': pair, 'error': 'no explanation found'}
        # Also load original flip result
        r_row = json.load(open(pd/'result.json'))
        llm_flipped = r_row.get('cf_flips', {}).get('llm_d', False)
        rating = rate_consistency(engine, edits_str, explanation)
        if not rating: return {'pair': pair, 'error': 'rating failed'}
        return {
            'pair': pair, 'llm_flipped': llm_flipped,
            'n_targets': len(targets),
            'consistency': rating.get('consistency'),
            'cited_edits': rating.get('cited_edits', []),
            'unrelated_reasons': rating.get('unrelated_reasons', []),
            'explanation': explanation[:500],
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(process, pd): pd for pd in pair_dirs}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                r = fut.result()
                if r:
                    out.append(r)
                    print(f"  [{i}/{len(pair_dirs)}] {r['pair']}: flipped={r.get('llm_flipped')} consistency={r.get('consistency')}", file=sys.stderr)
            except Exception as e:
                print(f"  FAIL: {e}", file=sys.stderr)
    pathlib.Path('/tmp/exp_explanation_consistency.json').write_text(json.dumps(out, indent=2, default=str))

    # Summary
    flipped = [r for r in out if r.get('llm_flipped') and 'consistency' in r]
    not_flipped = [r for r in out if not r.get('llm_flipped') and 'consistency' in r]
    import statistics
    if flipped:
        cs = [r['consistency'] for r in flipped if r.get('consistency')]
        print(f"\nLLM-d FLIPPED pairs (n={len(flipped)}): mean consistency={statistics.mean(cs):.2f}, median={statistics.median(cs):.2f}, low(<=2)={sum(1 for x in cs if x<=2)}")
    if not_flipped:
        cs = [r['consistency'] for r in not_flipped if r.get('consistency')]
        print(f"LLM-d NOT-FLIPPED pairs (n={len(not_flipped)}): mean consistency={statistics.mean(cs):.2f}")


if __name__ == '__main__': main()
