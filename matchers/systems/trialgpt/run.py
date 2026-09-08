#!/usr/bin/env python3
"""Run TrialGPT-Matching (Jin/Yang et al. 2024) on the 538-pair eval set.
Adapts our pair format to TrialGPT's expected input. Aggregates criterion-level
decisions to a binary eligibility per pair via:
  eligible iff (no inclusion criterion is "not_included" AND no exclusion criterion is "excluded")
This matches the spirit of TrialGPT-Aggregation (which assigns positive scores to "included"/"not_excluded")."""
from __future__ import annotations
import os
import argparse, json, os, pathlib, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, '/tmp/TrialGPT/trialgpt_matching')
sys.path.insert(0, '.')
sys.path.insert(0, 'experiments/99_counterfactual_lm')
try:
    from run_better_nl_full import get_pair_inputs, collect_pairs
except ModuleNotFoundError as _e:  # pragma: no cover
    raise ModuleNotFoundError(
        "run_better_nl_full is not part of this repository -- this batch driver "
        "depends on an internal module that was not released. The "
        "supported entry points are the `verdict` command and the "
        "`verdict` Python package; see README."
    ) from _e

ROOT = pathlib.Path('.')
OUT = ROOT / 'overnight' / 'trialgpt_matching.jsonl'

# Use TrialGPT's prompt structure but call via our engine
from openai import AzureOpenAI

def get_matching_prompt(inc_or_exc: str, criteria: str, patient: str):
    """TrialGPT-Matching prompt, adapted from /tmp/TrialGPT/trialgpt_matching/TrialGPT.py."""
    sys_p = f"You are a helpful assistant for clinical trial recruitment. Your task is to compare a given patient note and the {inc_or_exc} criteria of a clinical trial to determine the patient's eligibility at the criterion level.\n"
    if inc_or_exc == "inclusion":
        sys_p += "The factors that allow someone to participate in a clinical study are called inclusion criteria.\n"
    else:
        sys_p += "The factors that disqualify someone from participating are called exclusion criteria.\n"
    sys_p += f"You should check the {inc_or_exc} criteria one-by-one, and output the following three elements for each criterion:\n"
    sys_p += "\tElement 1: brief reasoning.\n\tElement 2: list of relevant sentence IDs (or empty list).\n"
    if inc_or_exc == "inclusion":
        sys_p += '\tElement 3: label from {"not applicable", "not enough information", "included", "not included"}.\n'
    else:
        sys_p += '\tElement 3: label from {"not applicable", "not enough information", "excluded", "not excluded"}.\n'
    sys_p += "Output ONLY a JSON dict: {criterion_idx: [reasoning, [sent_ids], label]}."
    user_p = f"Patient note (sentences numbered):\n{patient}\n\n{inc_or_exc.title()} Criteria:\n{criteria}\n\nJSON:"
    return sys_p, user_p


def parse_to_lines(criteria: str):
    parts = [c.strip() for c in re.split(r'\n+', criteria) if c.strip()]
    out = []
    for p in parts:
        if 'inclusion criteria' in p.lower() or 'exclusion criteria' in p.lower(): continue
        if len(p) < 5: continue
        out.append(p)
    return out


def number_patient(note: str):
    sents = re.split(r'(?<=[.!?])\s+', note.strip())
    return '\n'.join(f'{i}. {s}' for i, s in enumerate(sents) if s.strip())


def run_one_side(client, model, inc_or_exc, criteria, patient_numbered):
    if not criteria.strip(): return {}
    sys_p, user_p = get_matching_prompt(inc_or_exc, criteria, patient_numbered)
    last_err = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0,
                messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                max_tokens=3000,
            )
            text = resp.choices[0].message.content.strip().strip('`').strip('json').strip()
            jm = re.search(r'\{[\s\S]*\}', text)
            if not jm: raise ValueError('no_json')
            return json.loads(jm.group(0))
        except Exception as e:
            last_err = e; time.sleep(1 + attempt)
    return {'error': str(last_err)[:120]}


def aggregate_decision(inc_results: dict, exc_results: dict):
    """Binary eligibility from criterion-level labels."""
    # Skip "error" keys
    if 'error' in inc_results: inc_results = {k: v for k, v in inc_results.items() if k != 'error'}
    if 'error' in exc_results: exc_results = {k: v for k, v in exc_results.items() if k != 'error'}

    def get_label(v):
        if isinstance(v, list) and len(v) >= 3: return str(v[2]).lower()
        return str(v).lower()

    for v in inc_results.values():
        if 'not included' in get_label(v): return 'ineligible'
    for v in exc_results.values():
        if 'excluded' in get_label(v) and 'not excluded' not in get_label(v): return 'ineligible'
    return 'eligible'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='gpt-4.1')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    # Set up Azure client
    api_version = os.environ.get('AZURE_OPENAI_API_VERSION', '2024-08-01-preview')
    endpoint = os.environ['OPENAI_ENDPOINT'].rsplit('/openai/deployments/', 1)[0]
    client = AzureOpenAI(
        api_version=api_version, azure_endpoint=endpoint,
        api_key=os.environ['AZURE_OPENAI_API_KEY'],
    )

    cache = {}
    if OUT.exists():
        for line in OUT.open():
            try: o = json.loads(line); cache[o['pair']] = o
            except: pass

    pairs, _ = collect_pairs()
    if args.limit: pairs = pairs[:args.limit]
    todo = [p for p in pairs if p not in cache]
    print(f"[trialgpt-matching] cached={len(cache)} todo={len(todo)} total={len(pairs)}", flush=True)

    # Load full SIGIR corpus to bypass 1500-char get_pair_inputs cap.
    sigir_corpus = {}
    sigir_path = pathlib.Path(os.environ.get('VERDICT_ROOT',
    pathlib.Path(__file__).resolve().parents[3]))
    if sigir_path.exists():
        for ln in sigir_path.open():
            r = json.loads(ln); sigir_corpus[r['_id']] = r
    _inc_re = re.compile(r'(?i)\binclusion\s+criteria\s*:\s*')
    _exc_re = re.compile(r'(?i)\bexclusion\s+criteria\s*:\s*')
    def _split_inc_exc(text):
        if not text: return '', ''
        im = _inc_re.search(text); em = _exc_re.search(text)
        i = e = ''
        if im: i = text[im.end(): em.start() if em else len(text)].strip()
        if em: e = text[em.end():].strip()
        return i, e
    def _full_trial_text(pair):
        _, nct = pair.split('__', 1)
        if nct in sigir_corpus:
            return _split_inc_exc(sigir_corpus[nct].get('text',''))
        parent = re.sub(r'(?<=NCT\d{8})[a-z]+$', '', nct)
        if parent in sigir_corpus:
            return _split_inc_exc(sigir_corpus[parent].get('text',''))
        return None, None

    def proc(pair):
        note, inc_v6, exc_v6 = get_pair_inputs(pair)
        if note is None: return pair, {'pair': pair, 'eligibility': '?', 'error': 'no_input'}
        inc_full, exc_full = _full_trial_text(pair)
        inc = inc_full if inc_full else inc_v6
        exc = exc_full if exc_full is not None else exc_v6
        patient_numbered = number_patient(note)
        inc_lines = '\n'.join(f'{i}. {l}' for i, l in enumerate(parse_to_lines(inc)))
        exc_lines = '\n'.join(f'{i}. {l}' for i, l in enumerate(parse_to_lines(exc)))
        inc_res = run_one_side(client, args.model, 'inclusion', inc_lines, patient_numbered)
        exc_res = run_one_side(client, args.model, 'exclusion', exc_lines, patient_numbered)
        decision = aggregate_decision(inc_res if isinstance(inc_res, dict) else {},
                                       exc_res if isinstance(exc_res, dict) else {})
        return pair, {
            'pair': pair, 'eligibility': decision, 'model': args.model,
            'inc_results': inc_res if isinstance(inc_res, dict) else None,
            'exc_results': exc_res if isinstance(exc_res, dict) else None,
        }

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(proc, p) for p in todo]
        with OUT.open('a') as fout:
            for f in as_completed(futs):
                pair, rec = f.result()
                cache[pair] = rec
                # Trim verbose fields before writing
                fout.write(json.dumps({k: v for k, v in rec.items() if k != 'inc_results' and k != 'exc_results'}) + '\n')
                fout.flush()
                done += 1
                if done % 20 == 0 or done == len(todo):
                    print(f"  [{done}/{len(todo)}]", flush=True)

    n_e = sum(1 for v in cache.values() if v.get('eligibility') == 'eligible')
    n_i = sum(1 for v in cache.values() if v.get('eligibility') == 'ineligible')
    n_q = sum(1 for v in cache.values() if v.get('eligibility') not in ('eligible', 'ineligible'))
    print(f"[trialgpt-matching] FINAL: eligible={n_e} ineligible={n_i} unknown={n_q}", flush=True)


if __name__ == '__main__':
    main()
