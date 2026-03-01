#!/usr/bin/env python3
"""Stanford som-shahlab clinical_trial_patient_matching baseline.
Adapts their `prompt__all_criteria_koopman` to our 538-pair format.
Reports binary eligibility per pair (global_decision >= 1 = "might be eligible" → forward)."""
from __future__ import annotations
import argparse, json, os, pathlib, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def _load_charts():
    p = ROOT/'dataset/clinical_trial/sigir/queries.jsonl'
    return {json.loads(l)['_id']: json.loads(l).get('text','') or '' for l in p.open()}
def _load_inc_exc():
    p = ROOT/'dataset/clinical_trial/sigir/corpus.jsonl'
    out={}
    for l in p.open():
        try: o=json.loads(l)
        except: continue
        nct = o.get('_id') or o.get('id'); text = o.get('text','') or ''
        lo = text.lower()
        i = lo.find('inclusion criteria'); e = lo.find('exclusion criteria')
        inc = text[i:e] if i>=0 and e>i else (text[i:] if i>=0 else '')
        exc = text[e:] if e>=0 else ''
        out[nct] = (inc, exc)
    return out
_CHARTS=None; _INCEXC=None
def get_pair_inputs(pair):
    global _CHARTS, _INCEXC
    if _CHARTS is None: _CHARTS = _load_charts()
    if _INCEXC is None: _INCEXC = _load_inc_exc()
    qid, nct = pair.split('__', 1)
    chart = _CHARTS.get(qid, '')
    parent = re.sub(r'(?<=NCT\d{8})[a-z]+$', '', nct)
    inc, exc = _INCEXC.get(parent, _INCEXC.get(nct, ('','')))
    if not chart: return None, None, None
    return chart, inc, exc
def collect_pairs():
    p = ROOT/'matchers/systems/aegis/aegis_freeform.jsonl'
    seen=set(); pairs=[]
    for l in p.open():
        try: o=json.loads(l)
        except: continue
        pa = o.get('pair')
        if pa and pa not in seen: seen.add(pa); pairs.append(pa)
    return pairs, None


def build_prompt(note: str, inc_text: str, exc_text: str) -> str:
    """Stanford koopman prompt, adapted for our (note, inc_text, exc_text) inputs."""
    # Split criteria into list-of-lines
    def to_list(s):
        parts = re.split(r'\n+', (s or '').strip())
        return [p for p in (l.strip() for l in parts) if len(p) > 5 and 'inclusion criteria' not in p.lower() and 'exclusion criteria' not in p.lower()]
    inc_list = to_list(inc_text)
    exc_list = to_list(exc_text)
    inc_str = "\n".join(f"- inclusion_criteria_{i}: {c}" for i, c in enumerate(inc_list))
    exc_str = "\n".join(f"- exclusion_criteria_{i}: {c}" for i, c in enumerate(exc_list))

    prompt = f"""# Task
Your job is to indicate which of the following inclusion and exclusion criteria are met by the patient.

For an **inclusion** criteria to be "met", the patient must have the condition described in the criteria.
For an **exclusion** criteria to be "met", the patient must NOT have the condition described in the criteria.

# Patient

Below is a clinical note describing the patient's current health status:

```
{note}
```

# Inclusion Criteria

The inclusion criteria being assessed are listed below:
{inc_str}

The exclusion criteria being assessed are listed below:
{exc_str}

# Assessment

For each of the criteria above, use the patient's clinical note to determine whether the patient meets it. Think step by step.

Format your response as a JSON object with:
- "assessments": a list of dicts, each with {{"criterion": str, "rationale": str, "is_met": bool, "confidence": "low|medium|high"}}
- "global_decision": int in {{0, 1, 2}} where 0=definitely ineligible, 1=might be eligible, 2=likely eligible. Be LENIENT: if patient meets most criteria but some are uncertain, default to 2.

Provide ONLY the JSON object."""
    return prompt


def call(engine, prompt, max_tokens=3000):
    out = engine([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=max_tokens)
    text = out[0] if isinstance(out, list) and out else out
    text = text if isinstance(text, str) else str(text)
    jm = re.search(r"\{[\s\S]*\}", text)
    if not jm: return None
    try: return json.loads(jm.group(0))
    except: return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=10)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--model', default='gpt-4.1')
    ap.add_argument('--output', default=None)
    args = ap.parse_args()

    OUT = pathlib.Path(args.output) if args.output else (ROOT / 'overnight' / 'stanford_som_shahlab.jsonl')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if OUT.exists():
        for line in OUT.open():
            try: o = json.loads(line); cache[o['pair']] = o
            except: pass

    import re as _re, urllib.request as _ur
    _ep = os.environ['OPENAI_ENDPOINT']
    _bm = _re.match(r'(https://[^/]+)/openai/deployments/[^/]+', _ep)
    _base = _bm.group(1) if _bm else _ep
    _url = f"{_base}/openai/deployments/{args.model}/chat/completions?api-version=2024-08-01-preview"
    _key = os.environ['OPENAI_API_KEY']
    def engine(messages, temperature=0.0, max_tokens=3000):
        body = {'messages': messages}
        if args.model.startswith('gpt-5') or args.model.startswith('o'):
            body['max_completion_tokens'] = max_tokens
        else:
            body['max_tokens'] = max_tokens; body['temperature'] = temperature
        req = _ur.Request(_url, data=json.dumps(body).encode(),
                          headers={'api-key': _key, 'Content-Type': 'application/json'})
        with _ur.urlopen(req, timeout=180) as r:
            text = json.loads(r.read())['choices'][0]['message']['content'] or ''
        return [text]

    pairs, _ = collect_pairs()
    if args.limit: pairs = pairs[:args.limit]
    todo = [p for p in pairs if p not in cache]
    print(f"[stanford] cached={len(cache)} todo={len(todo)}", flush=True)

    # Load full SIGIR corpus to bypass 1500-char get_pair_inputs cap.
    sigir_corpus = {}
    sigir_path = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT/dataset/clinical_trial/sigir/corpus.jsonl')
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
        # NO TRUNCATION on trial text or note
        prompt = build_prompt(note, inc, exc)
        last = 'all_attempts_returned_none'
        for attempt in range(3):
            try:
                res = call(engine, prompt)
                if not res:
                    time.sleep(1+attempt); continue
                gd = res.get('global_decision')
                if gd is None:
                    # Fallback: binarize from criterion list
                    asmts = res.get('assessments') or []
                    n_inc_met = sum(1 for a in asmts if 'inclusion' in str(a.get('criterion','')).lower() and a.get('is_met'))
                    n_inc_total = sum(1 for a in asmts if 'inclusion' in str(a.get('criterion','')).lower())
                    n_exc_met = sum(1 for a in asmts if 'exclusion' in str(a.get('criterion','')).lower() and a.get('is_met'))
                    eligible = (n_inc_total > 0 and n_inc_met >= n_inc_total // 2) and (n_exc_met == 0)
                    return pair, {'pair': pair, 'eligibility': 'eligible' if eligible else 'ineligible',
                                  'global_decision': None, 'fallback_binarize': True,
                                  'assessments': asmts[:30]}
                eligible = gd >= 1  # 1 or 2 forwards (lenient)
                return pair, {'pair': pair, 'eligibility': 'eligible' if eligible else 'ineligible',
                              'global_decision': gd,
                              'assessments': (res.get('assessments') or [])[:30]}
            except Exception as e:
                last = e; time.sleep(1+attempt)
        return pair, {'pair': pair, 'eligibility': '?', 'error': str(last)[:120]}

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(proc, p) for p in todo]
        with OUT.open('a') as fout:
            for f in as_completed(futs):
                pair, rec = f.result()
                cache[pair] = rec
                fout.write(json.dumps(rec) + '\n'); fout.flush()
                done += 1
                if done % 30 == 0 or done == len(todo): print(f"  [{done}/{len(todo)}]", flush=True)

    n_e = sum(1 for v in cache.values() if v.get('eligibility') == 'eligible')
    n_i = sum(1 for v in cache.values() if v.get('eligibility') == 'ineligible')
    print(f"[stanford] FINAL: eligible={n_e} ineligible={n_i}", flush=True)


if __name__ == '__main__':
    main()
