#!/usr/bin/env python3
"""Re-aggregate TrialGPT verdicts with NA / not-enough-information treated as NULL.

Original cmsrc TG aggregation rule (per match_patient_to_trial.py): treat
'not applicable' / 'not enough information' as ineligibility-contributing.
This is over-conservative.

Corrected rule:
  - INCLUSION sat iff no row has label 'not included'.
    NA / not-enough-information rows are NULL (don't fail eligibility).
  - EXCLUSION sat iff no row has label 'excluded'.
    NA / not-enough-information rows are NULL (don't fire).
  - eligible = inclusion_sat AND exclusion_sat
"""
from __future__ import annotations
import json, pathlib, re
from collections import defaultdict
ROOT = pathlib.Path(__file__).resolve().parents[1]


def aggregate(rows_inc, rows_exc):
    """Return (eligible, inc_sat, exc_sat) under NA/NEI=null rule."""
    inc_labels = [r.get('label') for r in rows_inc]
    exc_labels = [r.get('label') for r in rows_exc]
    # inclusion fails iff any 'not included' row
    inc_fail = any(l == 'not included' for l in inc_labels)
    # exclusion fires iff any 'excluded' row
    exc_fail = any(l == 'excluded' for l in exc_labels)
    inc_sat = not inc_fail
    exc_sat = not exc_fail
    eligible = inc_sat and exc_sat
    return eligible, inc_sat, exc_sat


def render_rationale(rows_inc, rows_exc, max_rows=8):
    """Build a compact NL summary of TG per-criterion findings for judges."""
    parts = []
    parts.append("Inclusion criteria (per-criterion TrialGPT verdict):")
    for r in rows_inc[:max_rows]:
        parts.append(f"  - [{r.get('label','?')}] {(r.get('reasoning') or '')[:200]}")
    parts.append("Exclusion criteria (per-criterion TrialGPT verdict):")
    for r in rows_exc[:max_rows]:
        parts.append(f"  - [{r.get('label','?')}] {(r.get('reasoning') or '')[:200]}")
    return '\n'.join(parts)


def main():
    OUT = ROOT/'overnight/trialgpt_corrected.jsonl'
    cmsrc_root = ROOT/'experiments/53_v2_full/cmsrc_out'
    n_pairs = 0
    n_changed = 0  # verdicts that changed from old aggregation
    with OUT.open('w') as fout:
        # group by base pair (any-positive across subcohort variants)
        pair_data = defaultdict(list)
        for fp in cmsrc_root.rglob('*__trialgpt_judge.json'):
            try: o = json.load(open(fp))
            except: continue
            tid = fp.name.replace('__trialgpt_judge.json','')
            m = re.match(r'^(NCT\d+)([a-z]?)$', tid)
            if not m: continue
            pid = fp.parent.name; nct = m.group(1); v = m.group(2) or '_'
            inc_rows = (o.get('inclusion') or {}).get('rows') or []
            exc_rows = (o.get('exclusion') or {}).get('rows') or []
            elig_new, inc_sat, exc_sat = aggregate(inc_rows, exc_rows)
            elig_old = (o.get('aggregate') or {}).get('eligible')
            pair_data[f'{pid}__{nct}'].append({
                'variant': v, 'eligible_new': elig_new, 'inc_sat': inc_sat, 'exc_sat': exc_sat,
                'eligible_old': elig_old,
                'rationale': render_rationale(inc_rows, exc_rows)
            })
        # any-positive aggregation across variants for final pair-level decision
        for pair, variants in pair_data.items():
            elig_new = any(v['eligible_new'] for v in variants)
            elig_old = any(v['eligible_old'] is True for v in variants)
            if elig_new != elig_old:
                n_changed += 1
            # combine rationale across variants (use first variant's)
            rationale = variants[0]['rationale']
            fout.write(json.dumps({
                'pair': pair,
                'eligibility': 'eligible' if elig_new else 'ineligible',
                'eligibility_old_aggregation': 'eligible' if elig_old else 'ineligible',
                'rationale': rationale,
                'n_variants': len(variants),
            }) + '\n')
            n_pairs += 1
    print(f'TrialGPT corrected: {n_pairs} pairs')
    print(f'Verdicts changed under new aggregation: {n_changed}')
    print(f'  → {OUT}')


if __name__ == '__main__':
    main()
