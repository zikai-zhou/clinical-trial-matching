"""Re-judge a (CF chart, trial) pair on each system.

For V5 / TrialGPT / Stanford: a single LLM eligibility call with the new chart.
For AEGIS: re-mine atoms (cmsrc) + Z3 solve. Most expensive.

Each judger returns a uniform record: {eligibility, rationale, system, ...}
"""
from __future__ import annotations
import json, os, pathlib, subprocess, tempfile, urllib.request, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'experiments/accuracy/scripts'))
from repro_headline import quote_smt, smt_number, thresh_binding_lines
import z3

ENDPOINT = os.environ.get('OPENAI_ENDPOINT', '')
KEY      = os.environ.get('OPENAI_API_KEY', '')


# ---------- LLM-judge helpers ----------

V5_PROMPT = """You are an expert clinician determining patient eligibility for a clinical trial.

PATIENT CHART:
{chart}

TRIAL ELIGIBILITY:
{trial}

Evaluate whether the patient is eligible. Output STRICT JSON:
{{
  "eligibility": "eligible" | "ineligible",
  "explanation": "<1-3 sentence rationale citing chart evidence>"
}}"""


TG_PER_CRIT_PROMPT = """You are an expert clinician judging whether a patient meets a single criterion.

PATIENT CHART:
{chart}

TRIAL CRITERION ({inc_or_exc}):
{criterion}

Output STRICT JSON:
{{
  "label": "included" | "not included" | "not applicable" | "not enough information",
  "reasoning": "<short rationale>"
}}

(For exclusion criteria, "included" means the patient triggers the exclusion; "not included" means they do not.)"""


def _llm(prompt, max_tokens=600, json_mode=True):
    body_obj = {
        'messages': [{'role':'user','content':prompt}],
        'max_tokens': max_tokens, 'temperature': 0,
    }
    if json_mode: body_obj['response_format'] = {'type': 'json_object'}
    req = urllib.request.Request(
        f"{ENDPOINT}/chat/completions?api-version=2024-08-01-preview",
        data=json.dumps(body_obj).encode(),
        headers={'api-key': KEY, 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    return resp['choices'][0]['message']['content'] or ''


def judge_v5(chart: str, trial: str) -> dict:
    txt = _llm(V5_PROMPT.format(chart=chart[:5000], trial=trial[:5000]))
    try: o = json.loads(txt)
    except: o = {}
    return {
        'system': 'v5',
        'eligibility': (o.get('eligibility') or 'unknown').lower(),
        'rationale': o.get('explanation','') or '',
    }


def _split_trial_criteria(trial_text: str) -> tuple[list, list]:
    """Heuristically split into inclusion/exclusion bullet lines."""
    lines = trial_text.replace('\r','').split('\n')
    inc, exc, mode = [], [], 'inc'
    for ln in lines:
        l = ln.strip()
        if not l: continue
        ll = l.lower()
        if 'exclusion' in ll: mode = 'exc'; continue
        if 'inclusion' in ll: mode = 'inc'; continue
        if l.startswith(('-', '*', '•')) or re.match(r'^[A-Z\d]', l):
            (inc if mode == 'inc' else exc).append(l[:600])
    return inc[:8], exc[:10]


def judge_tg(chart: str, trial: str) -> dict:
    """Per-criterion judgment (same shape as cmsrc TG output)."""
    inc, exc = _split_trial_criteria(trial)
    inc_rows = []; exc_rows = []
    inc_fail = exc_fail = False
    for c in inc:
        try:
            o = json.loads(_llm(TG_PER_CRIT_PROMPT.format(chart=chart[:3000], inc_or_exc='inclusion', criterion=c)))
        except: o = {}
        lbl = (o.get('label') or '').lower()
        inc_rows.append({'criterion': c, 'label': lbl, 'reasoning': o.get('reasoning','')})
        if lbl == 'not included': inc_fail = True
    for c in exc:
        try:
            o = json.loads(_llm(TG_PER_CRIT_PROMPT.format(chart=chart[:3000], inc_or_exc='exclusion', criterion=c)))
        except: o = {}
        # Stanford cmsrc convention: "included" on exclusion side = exclusion fires (excluded)
        lbl = (o.get('label') or '').lower()
        if lbl == 'included': lbl = 'excluded'
        elif lbl == 'not included': lbl = 'not excluded'
        exc_rows.append({'criterion': c, 'label': lbl, 'reasoning': o.get('reasoning','')})
        if lbl == 'excluded': exc_fail = True
    elig = 'eligible' if not (inc_fail or exc_fail) else 'ineligible'
    rationale_lines = ['Inclusion criteria (per-criterion TrialGPT verdict):']
    for r in inc_rows: rationale_lines.append(f"  - [{r['label']}] {r['reasoning'][:200]}")
    rationale_lines.append('Exclusion criteria (per-criterion TrialGPT verdict):')
    for r in exc_rows: rationale_lines.append(f"  - [{r['label']}] {r['reasoning'][:200]}")
    return {'system': 'tg', 'eligibility': elig, 'rationale': '\n'.join(rationale_lines)}


def judge_shah(chart: str, trial: str) -> dict:
    """Stanford uses similar per-criterion approach; reuse TG plumbing."""
    res = judge_tg(chart, trial)
    res['system'] = 'shah'
    return res


# ---------- AEGIS re-mine + solve ----------

CMSRC_DIR = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT/cmsrc')
CMSRC_PY  = pathlib.Path('<local-path>/.pyenv/versions/3.11.9/bin/python')


def judge_aegis(pair: str, full_tid: str, cf_chart: str,
                prompt_root: pathlib.Path, prompt_map_path: pathlib.Path) -> dict:
    """Re-run AEGIS on a CF chart for a single (pair, full_tid) variant.

    Strategy: write the CF chart to a temp queries.jsonl override, run cmsrc
    match_patient_to_trial.py with --queries-override (if supported) or by
    pointing --data-root to a temporary dir. Returns SAT/UNSAT + atom values.
    """
    pid = pair.split('__')[0]
    # Build a temporary dataset dir with overridden patient chart for this pid only
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        (td/'sigir').mkdir(parents=True)
        # Copy corpus.jsonl as-is
        import shutil
        shutil.copy(ROOT/'dataset/clinical_trial/sigir/corpus.jsonl', td/'sigir/corpus.jsonl')
        # Write patched queries.jsonl with cf_chart for this pid
        out_q = (td/'sigir/queries.jsonl').open('w')
        for line in open(ROOT/'dataset/clinical_trial/sigir/queries.jsonl'):
            try: o = json.loads(line)
            except: out_q.write(line); continue
            if o.get('_id') == pid: o['text'] = cf_chart
            out_q.write(json.dumps(o)+'\n')
        out_q.close()
        # Output dir
        out_root = td/'cmsrc_out'
        cmd = [
            str(CMSRC_PY), str(CMSRC_DIR/'match_patient_to_trial.py'),
            full_tid, pid,
            '--out-root', str(out_root),
            '--prompt-root', str(prompt_root),
            '--prompt-map', str(prompt_map_path),
            '--miner-mode', 'infer',
            '--data-root', str(td),
            '--build-root', '../build',
        ]
        env = dict(os.environ); env['OPENAI_ENDPOINT'] = ENDPOINT; env['OPENAI_API_KEY'] = KEY
        try:
            res = subprocess.run(cmd, cwd=str(CMSRC_DIR), capture_output=True, text=True,
                                 timeout=180, env=env)
            if res.returncode != 0:
                return {'system':'aegis', 'eligibility':'error',
                        'rationale': f'cmsrc rc={res.returncode}: {res.stderr[-300:]}'}
        except Exception as e:
            return {'system':'aegis', 'eligibility':'error',
                    'rationale': f'subprocess err: {str(e)[:300]}'}
        # Read full.json
        full_fp = out_root / pid / f'{full_tid}__full.json'
        if not full_fp.exists():
            return {'system':'aegis', 'eligibility':'error',
                    'rationale': f'no output at {full_fp}'}
        try: o = json.loads(full_fp.read_text())
        except Exception as e:
            return {'system':'aegis', 'eligibility':'error', 'rationale': f'json err: {e}'}
        inc_sat = o.get('inclusion',{}).get('sat_like')
        exc_sat = o.get('exclusion',{}).get('sat_like')
        elig = (inc_sat is not False) and (exc_sat is not False)
        return {
            'system': 'aegis',
            'eligibility': 'eligible' if elig else 'ineligible',
            'inclusion_sat_like': inc_sat,
            'exclusion_sat_like': exc_sat,
            'full_json_path': str(full_fp),  # caller may discard
        }
