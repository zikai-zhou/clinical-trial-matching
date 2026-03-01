#!/usr/bin/env python3
"""Full-corpus (178-pair) 3-cell ablation:
  Cell 1: gpt-4.1 modifier + gpt-4.1 validator + blocker-filtered rationales
  Cell 2: gpt-4.1 modifier + v3 gpt-5 simclin validator + blocker-filtered rationales
  Cell 3: gpt-5 modifier + v3 gpt-5 simclin validator + blocker-filtered rationales

Systems: aegis, v5, tg, shah (binary), v5_cot, v5_blockers
- Shah uses koopman_binary.prompt + min-flip extractor (no ternary gd≥1).
- V5_cot is a new baseline (V5 + CoT reasoning + structured decisive_blockers).

Each cell is a SLICE of the work; can be run in parallel.
Set env vars:
  CELL=1|2|3        which cell to run (selects modifier/validator combo)
  SLICE=N/M         this process handles pairs[N::M]
  SYSTEMS=...       comma-separated system list (default: all)

All prompts and raw responses are saved to the output jsonl per cell.
"""
import argparse, json, os, pathlib, re, subprocess, sys, tempfile, shutil, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'experiments/counterfactual/utils'))
sys.path.insert(0, str(ROOT/'experiments/counterfactual/05_self_faithfulness'))

# ── Config ──────────────────────────────────────────────────────────────
CELL_CONFIGS = {
    1: {'modifier': 'gpt-4.1', 'validator': 'gpt-4.1', 'tag': 'cell1_4.1m_4.1v_filt'},
    2: {'modifier': 'gpt-4.1', 'validator': 'gpt-5',   'tag': 'cell2_4.1m_v3v_filt'},
    3: {'modifier': 'gpt-5',   'validator': 'gpt-5',   'tag': 'cell3_5m_v3v_filt'},
}
CELL = int(os.environ.get('CELL', '1'))
CFG = CELL_CONFIGS[CELL]
SLICE = os.environ.get('SLICE', '0/1')
SLICE_TAG = SLICE.replace('/','of')
ALL_SYSTEMS = ['aegis', 'v5', 'v5_cot', 'v5_cot_gpt5', 'v5_blockers', 'tg', 'shah']
SYSTEMS = (os.environ.get('SYSTEMS') or ','.join(ALL_SYSTEMS)).split(',')

ENDPOINT = os.environ['OPENAI_ENDPOINT']
ENDPOINT_GPT5 = os.environ.get('OPENAI_ENDPOINT_GPT5') or ENDPOINT
KEY = os.environ['OPENAI_API_KEY']

OUT_SUFFIX = os.environ.get('OUT_SUFFIX', '')  # e.g. '_joint' to write to cell1_..._joint/
OUT_DIR = HERE/'out'/(CFG['tag'] + OUT_SUFFIX)
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT = OUT_DIR/f'records.{SLICE_TAG}.jsonl'

# ── LLM helpers ─────────────────────────────────────────────────────────
def call_gpt41(prompt: str, max_tokens: int = 4000, json_mode: bool = True) -> str:
    body = {'messages':[{'role':'user','content':prompt}],
            'max_tokens': max_tokens, 'temperature': 0}
    if json_mode: body['response_format'] = {'type': 'json_object'}
    req = urllib.request.Request(
        f'{ENDPOINT}/chat/completions?api-version=2024-08-01-preview',
        data=json.dumps(body).encode(),
        headers={'api-key': KEY, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    return resp['choices'][0]['message']['content'] or ''

def call_gpt5(prompt: str, max_tokens: int = 3000, json_mode: bool = True) -> str:
    base = ENDPOINT_GPT5.split('/openai/')[0]
    body = {'model': 'gpt-5',
            'messages': [{'role': 'user', 'content': prompt}],
            'max_completion_tokens': max_tokens + 4000}
    if json_mode: body['response_format'] = {'type': 'json_object'}
    req = urllib.request.Request(
        f'{base}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview',
        data=json.dumps(body).encode(),
        headers={'api-key': KEY, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read())
    return resp['choices'][0]['message']['content'] or ''

def parse_json(txt):
    try: return json.loads(txt)
    except:
        m = re.search(r'\{[\s\S]*\}', txt)
        if m:
            try: return json.loads(m.group(0))
            except: return {}
        return {}

# ── Prompts ─────────────────────────────────────────────────────────────
SHAH_BINARY_PROMPT = (ROOT/'matchers/systems/shahlab/prompts/koopman_binary.prompt').read_text()
V5_COT_PROMPT = (ROOT/'matchers/systems/single_shot_llm/prompts/V5_COT.prompt').read_text()
V5_BLOCKERS_PROMPT = (ROOT/'matchers/systems/single_shot_llm/prompts/V5_TWO_STEP_BLOCKERS.prompt').read_text()

from cf_judge import V5_PROMPT, TG_PER_CRIT_PROMPT, _split_trial_criteria

# v3 simclin prompt (for validator)
V3_SIMCLIN_PROMPT = """You are a senior clinical-trial coordinator at the PRESCREEN stage.

ORIGINAL CHART:
{original_chart}

MODIFIED CHART (counterfactual):
{cf_chart}

INCLUSION CRITERIA:
{inclusion}

EXCLUSION CRITERIA:
{exclusion}

CITED BLOCKERS the modifier was asked to address:
{cited_block}

Return strict JSON:
{{
  "coherent":          true|false
  "flips_target_atom": true|false
  "keeps_other_facts": true|false
  "oracle_should_flip":true|false
  "explanation":       "<one sentence justification>"
}}
"""

# Shah min-flip extractor prompt
SHAH_MINFLIP_PROMPT = """You are auditing a clinical-trial matcher's per-criterion assessments. The matcher determined the patient is INELIGIBLE. You will identify the MINIMUM set of chart facts that, if changed, would flip the matcher's verdict to ELIGIBLE.

PATIENT CHART:
{chart}

TRIAL CRITERIA:
{trial}

MATCHER'S PER-CRITERION ASSESSMENTS:
{assessments}

For each criterion the matcher marked as not_met, classify it:
  - DETERMINISTIC: chart explicitly contradicts the criterion (or fires it on exclusion side)
  - DEFERRAL: chart is silent; matcher defaulted to not_met but a visit could resolve it

Identify the smallest set of DETERMINISTIC blockers whose chart facts, if changed, would result in the matcher re-running and outputting eligible=true. Include each blocker's current chart fact and a proposed target chart fact.

Output STRICT JSON:
{{
  "min_flip_blockers": [
    {{"criterion": "<criterion text>",
      "current_chart_fact": "<what the chart says now>",
      "target_chart_fact": "<what the chart would need to say to flip>",
      "side": "inclusion|exclusion"}}
  ],
  "deferred_at_visit": [
    {{"criterion": "<criterion text>", "reason": "<why deferred>"}}
  ]
}}
"""

# ── Matcher functions (first-round + rejudge use same code) ─────────────
import cf_dataset as ds
charts = ds.load_charts()
trials = ds.load_trial_text()
mine = ds.load_v9_mine()
arbiter = ds.load_arbiter_cache()
aegis_recs = ds.load_aegis_v9_arbiter()

def split_trial(trial):
    inc_re = re.compile(r'(?i)\binclusion\s+criteria\s*:\s*')
    exc_re = re.compile(r'(?i)\bexclusion\s+criteria\s*:\s*')
    im = inc_re.search(trial); em = exc_re.search(trial)
    inc = trial[im.end(): em.start() if em else len(trial)].strip() if im else ''
    exc = trial[em.end():].strip() if em else ''
    return inc, exc

def to_list(s):
    parts = re.split(r'\n+', (s or '').strip())
    out = []
    for p in parts:
        p = p.strip()
        while re.match(r'(?i)^(in|ex)clusion\s+criteria\s*:?\s*', p):
            p = re.sub(r'(?i)^(in|ex)clusion\s+criteria\s*:?\s*', '', p).strip()
        p = re.sub(r'^[:\-\s]+', '', p).strip()
        if len(p) > 5: out.append(p)
    return out

def run_shah_binary(chart, trial):
    """Run Shah Koopman with binary prompt (no ternary gd)."""
    inc, exc = split_trial(trial)
    inc_str = '\n'.join(f'- inclusion_criteria_{i}: {c}' for i,c in enumerate(to_list(inc)))
    exc_str = '\n'.join(f'- exclusion_criteria_{i}: {c}' for i,c in enumerate(to_list(exc)))
    prompt = (SHAH_BINARY_PROMPT
              .replace('{note}', chart)
              .replace('{inc_str}', inc_str)
              .replace('{exc_str}', exc_str))
    raw = call_gpt41(prompt)
    parsed = parse_json(raw)
    elig = bool(parsed.get('eligible', False))
    return {'eligibility': 'eligible' if elig else 'ineligible',
            'eligible': elig,
            'assessments': parsed.get('assessments', []),
            'raw_response': parsed,
            'prompt': prompt}

def run_v5_cot_gpt5(chart, trial):
    """V5 + CoT structured reasoning, using gpt-5 as the matcher. The
    `decisive_blockers` list IS the model's claimed minimum-flip set."""
    prompt = V5_COT_PROMPT.replace('{chart}', chart).replace('{trial}', trial)
    raw = call_gpt5(prompt, max_tokens=2500)
    parsed = parse_json(raw)
    return {'eligibility': (parsed.get('eligibility') or 'unknown').lower(),
            'reasoning': parsed.get('reasoning', ''),
            'decisive_blockers': parsed.get('decisive_blockers', []),
            'inclusion_supports': parsed.get('inclusion_supports', []),
            'deferred_at_visit': parsed.get('deferred_at_visit', []),
            'explanation': parsed.get('explanation', ''),
            'raw_response': parsed,
            'prompt': prompt}

def run_v5_cot(chart, trial):
    """V5 + CoT structured reasoning."""
    prompt = V5_COT_PROMPT.replace('{chart}', chart).replace('{trial}', trial)
    raw = call_gpt41(prompt)
    parsed = parse_json(raw)
    return {'eligibility': (parsed.get('eligibility') or 'unknown').lower(),
            'reasoning': parsed.get('reasoning', ''),
            'decisive_blockers': parsed.get('decisive_blockers', []),
            'inclusion_supports': parsed.get('inclusion_supports', []),
            'deferred_at_visit': parsed.get('deferred_at_visit', []),
            'explanation': parsed.get('explanation', ''),
            'raw_response': parsed,
            'prompt': prompt}

def run_v5(chart, trial):
    prompt = V5_PROMPT.format(chart=chart, trial=trial)
    raw = call_gpt41(prompt)
    parsed = parse_json(raw)
    return {'eligibility': (parsed.get('eligibility') or 'unknown').lower(),
            'explanation': parsed.get('explanation', ''),
            'raw_response': parsed,
            'prompt': prompt}

def run_v5_blockers(chart, trial):
    prompt = V5_BLOCKERS_PROMPT.replace('{{CHART}}', chart).replace('{{TRIAL}}', trial)
    raw = call_gpt41(prompt)
    parsed = parse_json(raw)
    return {'eligibility': (parsed.get('eligibility') or 'unknown').lower(),
            'explanation': parsed.get('explanation', ''),
            'blockers': parsed.get('blockers', []),
            'supports': parsed.get('supports', []),
            'raw_response': parsed,
            'prompt': prompt}

def run_tg(chart, trial):
    inc, exc = _split_trial_criteria(trial)
    inc_rows = []; exc_rows = []
    inc_fail = exc_fail = False
    sample_prompts = []
    for c in inc:
        prompt = TG_PER_CRIT_PROMPT.format(chart=chart, inc_or_exc='inclusion', criterion=c)
        if not sample_prompts: sample_prompts.append(prompt)
        try: o = parse_json(call_gpt41(prompt))
        except: o = {}
        lbl = (o.get('label') or '').lower()
        inc_rows.append({'criterion': c, 'label': lbl, 'reasoning': o.get('reasoning','')})
        if lbl == 'not included': inc_fail = True
    for c in exc:
        prompt = TG_PER_CRIT_PROMPT.format(chart=chart, inc_or_exc='exclusion', criterion=c)
        try: o = parse_json(call_gpt41(prompt))
        except: o = {}
        lbl = (o.get('label') or '').lower()
        if lbl == 'included': lbl = 'excluded'
        elif lbl == 'not included': lbl = 'not excluded'
        exc_rows.append({'criterion': c, 'label': lbl, 'reasoning': o.get('reasoning','')})
        if lbl == 'excluded': exc_fail = True
    elig = 'eligible' if not (inc_fail or exc_fail) else 'ineligible'
    rationale_lines = ['Inclusion criteria (per-criterion TrialGPT verdict):']
    for r in inc_rows: rationale_lines.append(f"  - [{r['label']}] {r['reasoning']}")
    rationale_lines.append('Exclusion criteria (per-criterion TrialGPT verdict):')
    for r in exc_rows: rationale_lines.append(f"  - [{r['label']}] {r['reasoning']}")
    return {'eligibility': elig,
            'rationale': '\n'.join(rationale_lines),
            'inc_rows': inc_rows, 'exc_rows': exc_rows,
            'sample_prompt': sample_prompts[0] if sample_prompts else ''}

# ── Rationale generators (blocker-only, deterministic) ──────────────────
def filter_shah_blockers(shah_out):
    """Min-flip extractor: from Shah binary output, identify deterministic blockers."""
    assessments = shah_out.get('assessments', [])
    inc_blockers, exc_blockers, deferred = [], [], []
    for a in assessments:
        crit = str(a.get('criterion','')).lower()
        is_met = a.get('is_met')
        evid = a.get('evidence_status', '')
        rat = a.get('rationale', '')
        line = f"  - [{a.get('confidence','?')}] {rat}"
        if 'inclusion' in crit:
            if is_met is False:
                if 'silent' in evid or 'defaulted' in evid: deferred.append(line)
                else: inc_blockers.append(line)
        elif 'exclusion' in crit:
            if is_met is False:
                if 'silent' in evid or 'defaulted' in evid: deferred.append(line)
                else: exc_blockers.append(line)
    parts = []
    if inc_blockers: parts.append('Inclusion criteria the patient FAILS (deterministic):\n' + '\n'.join(inc_blockers))
    if exc_blockers: parts.append('Exclusion criteria the patient TRIGGERS (deterministic):\n' + '\n'.join(exc_blockers))
    rationale = '\n\n'.join(parts) or '(no deterministic blockers)'
    return rationale, deferred

def filter_tg_blockers(tg_out):
    """TG blockers: inclusion [not included] + exclusion [excluded]. Full-length reasoning."""
    inc_block = [f'  - [not included] {r["reasoning"]}'
                 for r in tg_out.get('inc_rows', []) if (r.get('label') or '').lower() == 'not included']
    exc_block = [f'  - [excluded] {r["reasoning"]}'
                 for r in tg_out.get('exc_rows', []) if (r.get('label') or '').lower() == 'excluded']
    parts = []
    if inc_block: parts.append('Inclusion criteria the patient FAILS:\n' + '\n'.join(inc_block))
    if exc_block: parts.append('Exclusion criteria the patient TRIGGERS:\n' + '\n'.join(exc_block))
    return '\n\n'.join(parts) or '(no TG blockers)'

def filter_v5_cot_blockers(v5cot_out):
    """V5-CoT already produces a `decisive_blockers` list — use it directly.
    Criterion + chart_fact are full-length (no truncation) so the modifier
    sees the complete clinical context."""
    blockers = v5cot_out.get('decisive_blockers', [])
    if not blockers: return v5cot_out.get('explanation', '')
    lines = []
    for b in blockers:
        side = b.get('side','?')
        crit = b.get('criterion','')
        fact = b.get('chart_fact','')
        lines.append(f"  - [{side}] {crit}\n    chart fact: {fact}")
    return 'Patient decisive blockers:\n' + '\n'.join(lines)

# ── Modifier (uses CF_MODIFIER_MODEL env var via cf_generator) ──────────
os.environ['CF_MODIFIER_MODEL'] = CFG['modifier']
import importlib
import cf_generator
importlib.reload(cf_generator)

# ── Validator ───────────────────────────────────────────────────────────
def validate_4_1(chart, cf, cited, preserve=''):
    import cf_validator
    importlib.reload(cf_validator)
    return cf_validator.validate(chart, cf, cited, preserve_facts_text=preserve)

def validate_v3(chart, cf, trial, cited):
    inc, exc = split_trial(trial)
    prompt = V3_SIMCLIN_PROMPT.format(
        original_chart=chart, cf_chart=cf,
        inclusion=inc, exclusion=exc,
        cited_block=str(cited))
    for _ in range(3):
        try:
            r = parse_json(call_gpt5(prompt, max_tokens=2500))
            if isinstance(r, dict) and 'coherent' in r:
                return {**r, 'prompt': prompt}
        except Exception as e:
            err = str(e)[:160]
    return {'error': 'empty after retries', 'prompt': prompt}

# ── Aegis judge (cmsrc subprocess) ──────────────────────────────────────
CMSRC_DIR = pathlib.Path(os.environ.get('CMSRC_DIR', '<local-path>/Desktop/llm-smt/TrialGPT-SMT/cmsrc'))
CMSRC_PY  = pathlib.Path(os.environ.get('CMSRC_PY',  '<local-path>/.pyenv/versions/3.11.9/bin/python'))
PR_ROOT   = ROOT/'experiments/53_v2_full/inputs/prompt_root'
PR_MAP    = PR_ROOT/'prompt_out/prompt_map.json'

def judge_aegis_cmsrc(pair, deciding_variant, cf_chart):
    pid = pair.split('__')[0]
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td); (td/'sigir').mkdir(parents=True)
        shutil.copy(ROOT/'dataset/clinical_trial/sigir/corpus.jsonl', td/'sigir/corpus.jsonl')
        with (td/'sigir/queries.jsonl').open('w') as fout:
            for line in (ROOT/'dataset/clinical_trial/sigir/queries.jsonl').open():
                try: o=json.loads(line)
                except: fout.write(line); continue
                if o.get('_id')==pid: o['text']=cf_chart
                fout.write(json.dumps(o)+'\n')
        out_root = td/'cmsrc_out'
        cmd = [str(CMSRC_PY), str(CMSRC_DIR/'match_patient_to_trial.py'),
               deciding_variant, pid, '--out-root', str(out_root),
               '--prompt-root', str(PR_ROOT), '--prompt-map', str(PR_MAP),
               '--miner-mode','infer','--data-root', str(td),'--build-root','../build']
        env = dict(os.environ); env['OPENAI_ENDPOINT']=ENDPOINT; env['OPENAI_API_KEY']=KEY
        try:
            r = subprocess.run(cmd, cwd=str(CMSRC_DIR), capture_output=True, text=True, timeout=300, env=env)
            if r.returncode != 0:
                return {'eligibility':'error','rationale':f'rc={r.returncode}: {r.stderr[-200:]}'}
        except Exception as e:
            return {'eligibility':'error','rationale':f'subprocess: {str(e)[:200]}'}
        full_fp = out_root/pid/f'{deciding_variant}__full.json'
        if not full_fp.exists():
            return {'eligibility':'error','rationale':'no full.json'}
        try: o = json.loads(full_fp.read_text())
        except Exception as e: return {'eligibility':'error','rationale':f'json: {e}'}
        inc_sat = o.get('inclusion',{}).get('sat_like')
        exc_sat = o.get('exclusion',{}).get('sat_like')
        elig = 'eligible' if (inc_sat is not False) and (exc_sat is not False) else 'ineligible'
        return {'eligibility': elig, 'inclusion_sat_like': inc_sat, 'exclusion_sat_like': exc_sat,
                'raw_full': o}

# ── Per-pair driver ─────────────────────────────────────────────────────
def process_pair(pair):
    pid, nct = pair.split('__', 1)
    chart = charts.get(pid, '')
    base_nct = re.sub(r'[a-z]+$', '', nct)
    trial = trials.get(base_nct, '')
    if not chart or not trial:
        return {'pair': pair, 'error': 'missing chart/trial'}

    rec = {'pair': pair, 'cell': CFG['tag'], 'systems': {}}

    for system in SYSTEMS:
        try:
            sd = _process_one_system(system, pair, chart, trial)
            rec['systems'][system] = sd
        except Exception as e:
            rec['systems'][system] = {'error': str(e)[:300]}
    return rec

import cf_blockers as bl
import cf_generator as gen

def _process_one_system(system, pair, chart, trial):
    """Run first-round (or load), generate CF, validate, rejudge."""
    inc, exc = split_trial(trial)

    if system == 'aegis':
        vs = mine.get(pair, {})
        if not vs: return {'skipped': 'no v9 mine'}
        deciding = None; blk = None
        # Use joint maxsat (inc ∧ exc) when AEGIS_JOINT=1
        use_joint = os.environ.get('AEGIS_JOINT', '0') == '1'
        # Cohort selection: AEGIS_COHORT=first (default, filesystem-order; legacy),
        # AEGIS_COHORT=random (seeded shuffle; set AEGIS_COHORT_SEED). Both still
        # require the chosen cohort to have non-empty blockers; if not, we fall
        # through to the next cohort in the chosen order.
        cohort_mode = os.environ.get('AEGIS_COHORT', 'first')
        variants = list(vs.items())
        if cohort_mode == 'random':
            # Deterministic per-pair seed: salted by AEGIS_COHORT_SEED + md5(pair).
            # Avoids Python's randomized string hash (PYTHONHASHSEED).
            import random as _rand, hashlib as _h
            base = int(os.environ.get('AEGIS_COHORT_SEED', '0'))
            pair_hash = int(_h.md5(pair.encode()).hexdigest()[:8], 16)
            _rand.Random(base + pair_hash).shuffle(variants)
        cohort_choices = []  # for debugging — record what we tried
        for key, (full_tid, full_json) in variants:
            if use_joint:
                b = bl.aegis_blockers_joint(pair, full_tid, full_json, arbiter)
            else:
                b = bl.aegis_blockers(pair, full_tid, full_json, arbiter)
            cohort_choices.append({'full_tid': full_tid,
                                   'n_inc': len(b.get('inc_blockers') or []),
                                   'n_exc': len(b.get('exc_blockers') or [])})
            if b['inc_blockers'] or b['exc_blockers']:
                deciding = full_tid; blk = b; break
        if not deciding: return {'skipped': 'no blockers', 'cohort_choices': cohort_choices}
        targets = (blk['inc_blockers'] + blk['exc_blockers'])
        preserve = ((blk.get('inc_preserve') or []) + (blk.get('exc_preserve') or []))
        other_atoms = ((blk.get('inc_all_atoms') or []) + (blk.get('exc_all_atoms') or []))
        cf = gen.generate_cf_from_targets(chart, targets, preserve=preserve, other_atoms=other_atoms)
        cited_for_validator = json.dumps(targets, indent=2)
        if CFG['validator'] == 'gpt-4.1':
            cf_val = validate_4_1(chart, cf, cited_for_validator, preserve=json.dumps(preserve))
        else:
            cf_val = validate_v3(chart, cf, trial, cited_for_validator)
        rejudged = judge_aegis_cmsrc(pair, deciding, cf)
        return {'deciding_variant': deciding, 'targets': targets, 'preserve': preserve,
                'other_atoms': other_atoms[:20],  # cap to keep jsonl compact
                'cf_chart': cf, 'cf_validation': cf_val,
                'rejudged': rejudged,
                'first_verdict': 'ineligible',
                'use_joint_maxsat': use_joint,
                'joint_verified': blk.get('joint_verified'),
                'co_present_atoms': blk.get('co_present_atoms', []),
                'cohort_mode': cohort_mode,
                'cohort_choices': cohort_choices}
    if system in ('v5', 'v5_cot', 'v5_cot_gpt5', 'v5_blockers', 'tg', 'shah'):
        runners = {'v5': run_v5, 'v5_cot': run_v5_cot, 'v5_cot_gpt5': run_v5_cot_gpt5,
                   'v5_blockers': run_v5_blockers, 'tg': run_tg, 'shah': run_shah_binary}
        out = runners[system](chart, trial)
        if out['eligibility'] != 'ineligible':
            return {'skipped': f'first-round not ineligible: {out["eligibility"]}',
                    'first_round': out}
        # Build blocker-only rationale
        if system == 'shah':           rationale, _ = filter_shah_blockers(out); supports = ''
        elif system == 'tg':            rationale = filter_tg_blockers(out); supports = ''
        elif system == 'v5_cot':        rationale = filter_v5_cot_blockers(out); supports = ''
        elif system == 'v5_cot_gpt5':   rationale = filter_v5_cot_blockers(out); supports = ''
        elif system == 'v5_blockers':
            blockers = out.get('blockers', [])
            rationale = ('Patient blockers:\n' + '\n'.join(
                f"  - [{b.get('side','?')}] {b.get('criterion','')}\n    chart fact: {b.get('fact','')}"
                for b in blockers)) if blockers else out.get('explanation','')
            supports = '\n'.join(f"  - {s.get('fact','')}" for s in out.get('supports',[]))
        else:  # v5
            rationale = out.get('explanation','')
            supports = ''
        cf = gen.generate_cf_from_rationale(chart, trial, rationale, supports=supports)
        # Re-judge using the same matcher
        rejudge_out = runners[system](cf, trial)
        if CFG['validator'] == 'gpt-4.1':
            import cf_validator
            cf_val = cf_validator.validate(chart, cf, rationale, preserve_facts_text=supports)
        else:
            cf_val = validate_v3(chart, cf, trial, rationale)
        return {'first_round': out,
                'cited_rationale': rationale, 'supports': supports,
                'cf_chart': cf, 'cf_validation': cf_val,
                'rejudged': rejudge_out,
                'first_verdict': 'ineligible'}
    return {'skipped': f'unknown system {system}'}


# ── Main loop ───────────────────────────────────────────────────────────
def per_system_ineligible():
    """Map system -> set of pairs that system marked ineligible in published runs.
    v5_cot has no published verdicts; treat as "candidate every pair where any system was ineligible"
    and let the matcher itself filter eligibles at runtime."""
    loaders = {'aegis': ds.load_aegis_v9_arbiter, 'v5': ds.load_v5,
               'v5_blockers': ds.load_v5_blockers, 'tg': ds.load_tg,
               'shah': ds.load_shahlab}
    out = {}
    union_ineli = set()
    for s, ld in loaders.items():
        d = ld()
        ineli = {p for p, v in d.items() if v.get('eligibility') == 'ineligible'}
        out[s] = ineli
        union_ineli |= ineli
    out['v5_cot'] = union_ineli  # let runtime filter
    out['v5_cot_gpt5'] = union_ineli  # ditto — no published verdicts
    return out

def process_pair_filtered(pair, systems_for_pair):
    """Same as process_pair but only runs the systems listed."""
    pid, nct = pair.split('__', 1)
    chart = charts.get(pid, '')
    base_nct = re.sub(r'[a-z]+$', '', nct)
    trial = trials.get(base_nct, '')
    if not chart or not trial:
        return {'pair': pair, 'error': 'missing chart/trial'}
    rec = {'pair': pair, 'cell': CFG['tag'], 'systems': {}}
    for system in systems_for_pair:
        try:
            sd = _process_one_system(system, pair, chart, trial)
            rec['systems'][system] = sd
        except Exception as e:
            rec['systems'][system] = {'error': f'{type(e).__name__}: {str(e)[:300]}'}
    return rec

def main():
    sys_ineli = per_system_ineligible()
    # Build (pair, [systems]) work list — only systems that flagged this pair ineligible
    all_pairs = sorted(set().union(*[sys_ineli[s] for s in SYSTEMS if s in sys_ineli]))
    si, sn = map(int, SLICE.split('/'))
    pairs = all_pairs[si::sn]
    print(f'CELL {CELL} ({CFG["tag"]}) SLICE {SLICE}: {len(pairs)} pairs', flush=True)
    print(f'systems: {SYSTEMS}', flush=True)
    print(f'modifier={CFG["modifier"]} validator={CFG["validator"]}', flush=True)

    # Per (pair, system) resume: completed cells in existing file
    completed = {}   # pair -> set(system) already done
    if OUT.exists():
        for ln in OUT.open():
            try:
                r = json.loads(ln)
                completed.setdefault(r['pair'], set()).update(r.get('systems', {}).keys())
            except: pass

    n = 0
    with OUT.open('a') as f:
        for p in pairs:
            wanted = [s for s in SYSTEMS if p in sys_ineli.get(s, set())]
            todo_sys = [s for s in wanted if s not in completed.get(p, set())]
            if not todo_sys: continue
            try:
                rec = process_pair_filtered(p, todo_sys)
            except Exception as e:
                rec = {'pair': p, 'error': f'{type(e).__name__}: {str(e)[:200]}'}
            f.write(json.dumps(rec)+'\n'); f.flush(); n += 1
            sys_flips = {s: rec.get('systems',{}).get(s,{}).get('rejudged',{}).get('eligibility','?')[:5] for s in todo_sys}
            print(f'  [{SLICE_TAG}][{n}/{len(pairs)}] {p}  {sys_flips}', flush=True)

if __name__ == '__main__':
    main()
