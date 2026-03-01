#!/usr/bin/env python3
"""Fix the 'leave unspecified' rendering for numeric SMT atoms.

When the SMT modifier sees an atom like 'age=89' against threshold constraints
'age >= 18' AND 'age <= 35', it should emit a concrete in-range target, not
'leave unspecified'. This patches the audit instrument's cited_blocker_text
to show the proper directive.

Strategy:
  • For each ineligible CF audit pair, walk aegis.targets
  • Find numeric atoms with current_value known and target_value=None
  • Look up the threshold constraints in experiments/53_v2_full/threshold_cache/<NCT>.json
  • Resolve the constraint to a concrete satisfying value
  • Emit:  "Specific values to change: <human-readable name>: currently <cv>, must satisfy <constraint> (e.g., set to <concrete>)"
"""
import json, pathlib, re, shutil

ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')
FE = pathlib.Path('<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private')

sf_main = {r['pair']: r for r in [json.loads(l) for l in (ROOT/'experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl').open() if l.strip()]}

def load_thresh(nct):
    base = re.sub(r'[a-z]+$', '', nct)  # strip variant suffix
    p = ROOT/'experiments/53_v2_full/threshold_cache'/f'{base}.json'
    if not p.exists(): return {}
    return json.load(p.open()).get('atoms', {})

def humanize(atom):
    # Mirror the build script's humanization at a basic level
    s = atom.replace('patient_', '').replace('_recorded_', ' ').replace('_value', '').replace('_now_in_', ' (')
    s = s.replace('_withunit_', ' (').replace('_inthehistory', ' history').replace('_', ' ')
    if '(' in s and ')' not in s: s += ')'
    return s.strip()

# Hand-curated humanizations for known atoms
HUMAN = {
    'patient_age_value_recorded_now_in_years': 'age (years)',
    'patient_age_value_recorded_now_in_years_known': 'age (years)',
    'patient_age_value_recorded_at_treatment_initiation_in_years': 'age at treatment initiation (years)',
    'time_since_kawasaki_disease_diagnosis_in_months': 'time since Kawasaki diagnosis (months)',
    'patient_hemoglobin_finding_value_recorded_now_withunit_grams': 'hemoglobin (g/L)',
    'patient_hemoglobin_finding_value_recorded_now_withunit_grams_per_liter': 'hemoglobin (g/L)',
    'patient_disease_value_recorded_inthehistory_withunit_months': 'disease duration history (months)',
}

OP_HUMAN = {'ge':'≥','le':'≤','gt':'>','lt':'<','eq':'=','ne':'≠'}

def resolve_constraint(atom, thresh_atoms):
    """Find min/max bounds for this variable from threshold cache."""
    bounds = {'lo': None, 'hi': None, 'eq': None}
    for tk, ta in thresh_atoms.items():
        if ta.get('var') != atom: continue
        op, th = ta.get('interpreted_op'), ta.get('interpreted_threshold')
        if op in ('ge','gt'):
            new = th if op=='ge' else th+1  # gt → exclusive
            bounds['lo'] = new if bounds['lo'] is None else max(bounds['lo'], new)
        elif op in ('le','lt'):
            new = th if op=='le' else th-1
            bounds['hi'] = new if bounds['hi'] is None else min(bounds['hi'], new)
        elif op == 'eq':
            bounds['eq'] = th
    return bounds

def pick_concrete(bounds):
    """Return a representative satisfying value for the bounds, or None."""
    if bounds['eq'] is not None: return bounds['eq']
    lo, hi = bounds['lo'], bounds['hi']
    if lo is not None and hi is not None and lo <= hi:
        return int((lo + hi) / 2) if (lo+hi)/2 == int((lo+hi)/2) else round((lo+hi)/2, 1)
    if lo is not None: return lo
    if hi is not None: return hi
    return None

def constraint_str(bounds):
    lo, hi = bounds['lo'], bounds['hi']
    if bounds['eq'] is not None: return f"= {bounds['eq']}"
    if lo is not None and hi is not None: return f"in [{lo}, {hi}]"
    if lo is not None: return f"≥ {lo}"
    if hi is not None: return f"≤ {hi}"
    return None

review = json.load((FE/'clinician_review.json').open())
fixed = 0
for t in review['topics']:
    if t.get('sheet') != 'cf_rewrite_review': continue
    pair = f"{t.get('patient_id')}__{t.get('trial_id')}"
    a = (sf_main.get(pair, {}).get('systems') or {}).get('aegis', {})
    targets = a.get('targets', [])
    thresh = load_thresh(t.get('trial_id'))

    # Collect numeric-with-known-cv-and-null-tv atoms that have threshold constraints
    new_setvals = []
    affected = set()
    for tg in targets:
        cv = tg.get('current_value'); tv = tg.get('target_value'); atom = tg.get('atom','')
        if not (isinstance(cv,(int,float)) and tv is None and '__THRESH__' not in atom):
            continue
        # Only fix AGE atoms; other numeric thresholds (hemoglobin, durations) are
        # legitimately context-dependent and should remain "leave unspecified".
        if 'age' not in atom.lower():
            continue
        bounds = resolve_constraint(atom, thresh)
        concrete = pick_concrete(bounds)
        cstr = constraint_str(bounds)
        if concrete is None or cstr is None: continue
        human = HUMAN.get(atom, humanize(atom))
        new_setvals.append(f"  • {human}: currently {cv}, must be {cstr} (e.g., set to {concrete})")
        affected.add(human)

    if not new_setvals: continue

    # Find the aegis rewrite and patch its cited_blocker_text
    for rw in t.get('rewrites', []):
        if str(rw.get('system_blind_id','')).lower() not in ('aegis','smt'): continue
        bl = rw.get('cited_blocker_text','') or ''
        # Strip the "Quantitative facts to leave unspecified" block for affected items
        # and prepend a "Specific values to change" block
        # Replace existing "Specific values to change:" if present
        new_lines = []
        skip_block = None  # tracks which section header we're inside
        for line in bl.split('\n'):
            stripped = line.strip()
            if stripped.startswith('Quantitative facts to leave unspecified'):
                skip_block = 'silent'
                continue
            if skip_block == 'silent':
                if stripped.startswith('•') and any(h in stripped for h in affected):
                    continue  # drop this bullet (now handled in setval)
                elif stripped.startswith('•'):
                    # other silent items not in our fix list — keep them
                    new_lines.append('Quantitative facts to leave unspecified (chart should no longer commit to a value):')
                    new_lines.append(line)
                    skip_block = 'silent_kept'
                    continue
                elif not stripped:
                    skip_block = None
                    continue
                else:
                    skip_block = None
            new_lines.append(line)
        # Insert "Specific values to change" block before any final blocks
        setval_block = "Specific values to change:\n" + "\n".join(new_setvals)
        # Insert before the last empty section or at end
        new_bl = '\n'.join(new_lines).rstrip()
        if 'Specific values to change:' in new_bl:
            # Append to existing
            new_bl = new_bl.replace('Specific values to change:', f'Specific values to change:\n' + '\n'.join(new_setvals).replace('  • ','  • '), 1)
        else:
            new_bl = new_bl + '\n\n' + setval_block
        rw['cited_blocker_text'] = new_bl
        fixed += 1
        print(f'  patched {t["id"]}: {len(new_setvals)} numeric atoms set to concrete values')

(FE/'clinician_review.json').write_text(json.dumps(review, indent=2))
shutil.copy(FE/'clinician_review.json', FE/'clinician_review.full.demo.json')
print(f'\ntotal: patched {fixed} CF audit entries')
