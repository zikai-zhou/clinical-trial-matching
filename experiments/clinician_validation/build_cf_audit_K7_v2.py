#!/usr/bin/env python3
"""Build a CF-modifier-quality audit sample for clinician validation.

Source: cell 3 of the 3-cell ablation (gpt-5 modifier + gpt-5 v3 simclin validator),
post-truncation-fix, AEGIS_COHORT=random seed=2 with deterministic md5 hash.

Design (mirrors scripts_archive/build_cf_audit_K7.py but updated):
  - 7 systems: aegis, v5, v5_cot, v5_cot_gpt5, v5_blockers, tg, shah_binary
  - 2 buckets per system: flipped (valid CF + rejudged eligible), not_flipped (valid CF + rejudged ineligible)
  - K=7 per (system, bucket) → 7 × 2 × 7 = 98 audit cells
  - Greedy max-reuse: chosen pairs maximally fill multiple buckets at once
  - Per topic: original_chart + trial_listing + rewrites[] (one per system)
  - Each rewrite: cf_chart, cited_blocker_text, system_verdict, diff_segments (word-level vs original)

Output: experiments/clinician_validation/cf_audit_K7_cell3.json
"""
from __future__ import annotations
import json, pathlib, re, difflib, random
from collections import defaultdict

ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')
OUT_BASE = ROOT/'experiments/counterfactual/05_self_faithfulness/ablation_full_corpus/out'
CELL = 'cell3_5m_v3v_filt'
OUT_FILE = ROOT/'experiments/clinician_validation/cf_audit_K7_cell3.json'

# Systems and display labels (the audit is BLINDED — system_blind_id rotated)
SYSTEMS = [
    # (system_id, display_label, short_name)
    ('aegis',        'SMT matcher (Z3 MaxSat min-flip set)',         'aegis'),
    ('v5',           'Single-shot LLM (gpt-4.1)',                    'v5'),
    ('v5_cot',       'Single-shot LLM + chain-of-thought (gpt-4.1)', 'v5_cot'),
    ('v5_cot_gpt5',  'Single-shot LLM + chain-of-thought (gpt-5)',   'v5_cot_gpt5'),
    ('v5_blockers',  'Two-step blockers+supports LLM (gpt-4.1)',     'v5_blockers'),
    ('tg',           'TrialGPT per-criterion',                       'tg'),
    ('shah',         'Shah lab Koopman (binary-forced)',             'shah'),
]

K = 7  # per (system, bucket)


# ── Load data ──────────────────────────────────────────────────────────────
def load_pair_records(d, system):
    by_pair = {}
    for fp in d.glob('records.*of*.jsonl'):
        if 'merged' in fp.name: continue
        for ln in fp.open():
            try: r = json.loads(ln)
            except: continue
            p = r['pair']
            sd = (r.get('systems') or {}).get(system)
            if not sd: continue
            existing = by_pair.get(p)
            if existing and isinstance(existing, dict) and not existing.get('error'):
                continue
            by_pair[p] = sd
    return by_pair

def is_valid(v):
    if 'valid' in v: return bool(v.get('valid'))
    return bool(v.get('coherent') and v.get('flips_target_atom') and v.get('keeps_other_facts'))

def bucket(sd):
    """flipped | not_flipped | None (invalid_cf, skipped, errored)"""
    if not sd or sd.get('error') or sd.get('skipped'): return None
    v = sd.get('cf_validation') or {}
    if not is_valid(v): return None  # exclude invalid_cf
    return 'flipped' if sd.get('rejudged',{}).get('eligibility') == 'eligible' else 'not_flipped'

# Build per-system bucket map
system_buckets = {}
for system_id, _, _ in SYSTEMS:
    src = OUT_BASE/(CELL + ('_v5cot5' if system_id == 'v5_cot_gpt5' else ''))
    recs = load_pair_records(src, system_id)
    sb = {}
    for p, sd in recs.items():
        b = bucket(sd)
        if b: sb[p] = b
    system_buckets[system_id] = sb
    n_flip = sum(1 for b in sb.values() if b == 'flipped')
    n_nf = sum(1 for b in sb.values() if b == 'not_flipped')
    print(f'{system_id:14}: flipped={n_flip:3} not_flipped={n_nf:3}')

union = set().union(*(set(sb) for sb in system_buckets.values()))
print(f'\nunion of pairs with any-system valid data: {len(union)}')


# ── Greedy max-reuse selection ─────────────────────────────────────────────
target = {(sid, b): K for sid, _, _ in SYSTEMS for b in ('flipped','not_flipped')}
filled = {bk: 0 for bk in target}
# pair_buckets only lists systems that have a bucket on this pair
pair_buckets = {p: [(sid, system_buckets[sid][p]) for sid, _, _ in SYSTEMS if p in system_buckets[sid]]
                for p in union}

rng = random.Random(42)
chosen = []
avail = set(union)
while True:
    needed = {bk: target[bk] - filled[bk] for bk in target if filled[bk] < target[bk]}
    if not needed: break
    # For each candidate pair, count how many *needed* buckets it fills
    scored = []
    for p in avail:
        score = sum(1 for bk in pair_buckets[p] if bk in needed)
        if score > 0: scored.append((score, p))
    if not scored: break
    scored.sort(key=lambda x: (-x[0], rng.random()))  # max score, randomize ties
    score, best = scored[0]
    chosen.append(best); avail.discard(best)
    for bk in pair_buckets[best]:
        if filled[bk] < target[bk]: filled[bk] += 1

print(f'\nselected {len(chosen)} pairs. coverage:')
for sid, _, _ in SYSTEMS:
    f, n = filled[(sid,'flipped')], filled[(sid,'not_flipped')]
    print(f'  {sid:14} F={f}/{K} NF={n}/{K}')


# ── Load auxiliary data (charts, trials) ───────────────────────────────────
charts = {}
for ln in (ROOT/'dataset/clinical_trial/sigir/queries.jsonl').open():
    r = json.loads(ln); charts[r['_id']] = r.get('text','') or ''
trials = {}
for ln in (ROOT/'dataset/clinical_trial/sigir/corpus.jsonl').open():
    r = json.loads(ln); trials[r['_id']] = r.get('text','') or ''

INC_RE = re.compile(r'(?i)\binclusion\s+criteria\s*:\s*')
EXC_RE = re.compile(r'(?i)\bexclusion\s+criteria\s*:\s*')
def split_inc_exc(text):
    im = INC_RE.search(text); em = EXC_RE.search(text)
    inc = text[im.end(): em.start() if em else len(text)].strip() if im else ''
    exc = text[em.end():].strip() if em else ''
    return inc, exc
def trial_info(tid):
    base = re.sub(r'(?<=NCT\d{8})[a-z]+$', '', tid)
    text = trials.get(base, '') or trials.get(tid, '')
    if not text: return '', '', ''
    summary = text.split('Inclusion criteria:')[0].replace('Summary:','').strip() if 'Inclusion criteria:' in text else text[:1000]
    inc, exc = split_inc_exc(text)
    return summary, inc, exc


# ── Word-level diff between original and CF chart ──────────────────────────
def word_diff(orig, cf):
    toks_a = re.findall(r"\S+|\s+", orig or "")
    toks_b = re.findall(r"\S+|\s+", cf or "")
    sm = difflib.SequenceMatcher(a=toks_a, b=toks_b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':   out.append({'type':'equal','text':''.join(toks_a[i1:i2])})
        elif tag == 'delete': out.append({'type':'del','text':''.join(toks_a[i1:i2])})
        elif tag == 'insert': out.append({'type':'add','text':''.join(toks_b[j1:j2])})
        elif tag == 'replace':
            out.append({'type':'del','text':''.join(toks_a[i1:i2])})
            out.append({'type':'add','text':''.join(toks_b[j1:j2])})
    return out


# ── Extract cited blockers per system per pair ─────────────────────────────
def cited_for(pair, system_id, sd):
    if system_id == 'aegis':
        targets = sd.get('targets', [])
        if not targets: return '(no MaxSat targets)'
        lines = ['Cited symbolic atoms (MaxSat min-flip set):']
        for t in targets:
            atom = t.get('atom','')
            cur = t.get('current_value'); tgt = t.get('target_value')
            ev = t.get('evidence','') or ''
            lines.append(f"- atom `{atom}`: current={cur} → target={tgt}")
            if ev: lines.append(f"  evidence: \"{ev}\"")
        return '\n'.join(lines)
    return sd.get('cited_rationale','') or '(no cited rationale stored)'


# ── Build per-pair topics with all 7 system rewrites ───────────────────────
topics = []
for idx, pair in enumerate(chosen, 1):
    pid, tid = pair.split('__', 1)
    orig_chart = charts.get(pid, '')
    if not orig_chart: continue
    summary, inc, exc = trial_info(tid)
    rewrites = []
    for sys_id, display, short in SYSTEMS:
        src = OUT_BASE/(CELL + ('_v5cot5' if sys_id == 'v5_cot_gpt5' else ''))
        sd = load_pair_records(src, sys_id).get(pair)
        if not sd: continue
        b = bucket(sd)
        if not b: continue
        cf_chart = sd.get('cf_chart','') or ''
        rej = sd.get('rejudged',{}) or {}
        rej_elig = rej.get('eligibility', '?')
        verdict = f"ineligible→{rej_elig} (CF passed validator)"
        rewrites.append({
            'system_blind_id': sys_id,
            'system_display':  display,
            'short_name':      short,
            'bucket':          b,
            'system_verdict':  verdict,
            'cited_blocker_text': cited_for(pair, sys_id, sd),
            'cf_chart': cf_chart,
            'diff_segments': word_diff(orig_chart, cf_chart),
        })
    if not rewrites: continue

    # Blinding: shuffle the rewrite order per topic with a deterministic per-pair seed
    import hashlib
    seed = int(hashlib.md5(pair.encode()).hexdigest()[:8], 16)
    topic_rng = random.Random(seed)
    topic_rng.shuffle(rewrites)
    # Assign A/B/C/D/E/F/G after shuffling — field name `label` matches frontend schema
    for i, r in enumerate(rewrites):
        r['label'] = chr(ord('A') + i)

    topics.append({
        'id': f'cf_audit_K7_cell3__{idx:02d}__{pair}',
        'sheet': 'cf_rewrite_review',
        'task_id': 'cf_rewrite_review',
        'display_index': idx,
        'patient_id': pid,
        'trial_id': tid,
        'original_chart': orig_chart,
        'trial_listing': f'Summary: {summary}\nInclusion criteria: {inc}\nExclusion criteria: {exc}',
        'trial_inclusion': inc,
        'trial_exclusion': exc,
        'rewrites': rewrites,
    })

print(f'\nbuilt {len(topics)} topics with {sum(len(t["rewrites"]) for t in topics)} audit cells')
print(f'avg rewrites/pair: {sum(len(t["rewrites"]) for t in topics)/max(1,len(topics)):.1f}')

OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
payload = json.dumps({
    'meta': {
        'source_cell': CELL,
        'source_dir': str(OUT_BASE),
        'aegis_cohort_mode': 'random_seed2_md5_deterministic',
        'truncation_fix_applied': True,
        'systems': [{'id': s[0], 'display': s[1], 'short': s[2]} for s in SYSTEMS],
        'k_per_bucket': K,
        'bucket_coverage': {f'{sid}_{b}': filled[(sid,b)] for sid,_,_ in SYSTEMS for b in ('flipped','not_flipped')},
        'n_pairs': len(topics),
        'n_audit_cells': sum(len(t['rewrites']) for t in topics),
        'note': 'Blinded: rewrites within each topic are shuffled (seeded by pair hash) and assigned A,B,C,... labels.',
    },
    'topics': topics,
}, indent=2)
OUT_FILE.write_text(payload)
print(f'\nwrote {OUT_FILE}')

# Also publish to the frontend's private dir as clinician_review.json
import shutil
FE_PRIVATE = pathlib.Path('<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private')
if FE_PRIVATE.exists():
    target = FE_PRIVATE/'clinician_review.json'
    backup = FE_PRIVATE/'clinician_review.pre_cf_audit_K7_cell3.json.bak'
    if target.exists() and not backup.exists():
        shutil.copy(target, backup); print(f'backup: {backup}')
    target.write_text(payload)
    print(f'published to {target}')
else:
    print(f'(frontend private dir not found at {FE_PRIVATE} — UI publish skipped)')
