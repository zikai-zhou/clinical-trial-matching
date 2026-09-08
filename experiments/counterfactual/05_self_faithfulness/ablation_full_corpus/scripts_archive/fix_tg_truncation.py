#!/usr/bin/env python3
"""Re-populate TG cited_blocker_text in K7v2 CF audit with full untruncated
rationales (sentence-safe truncation, not mid-word)."""
import json, pathlib, re

ROOT = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored')
FE   = pathlib.Path('/Users/xyrus/Desktop/llm-smt/clinical-trial-annotation-frontend/private')

# Load TG rationales
tg_rat = {}
for ln in (ROOT/'matchers/systems/trialgpt/rationales.jsonl').open():
    o = json.loads(ln); tg_rat[o['pair']] = o.get('rationale') or o.get('cited_rationale') or ''

def safe_truncate(text, max_chars=8000):
    """Truncate at the last sentence boundary before max_chars; never mid-word."""
    s = (text or '').strip()
    if len(s) <= max_chars: return s
    cut = s[:max_chars]
    # Prefer last full sentence
    m = re.search(r'(.*[.!?])\s', cut, re.DOTALL)
    if m and len(m.group(1)) > max_chars * 0.5:
        return m.group(1).strip()
    # Fall back to last word boundary
    return cut.rsplit(' ', 1)[0].rstrip(',;:-') + ' …'

review = json.load((FE/'clinician_review.json').open())
n_fixed = 0; n_missing = 0
for t in review['topics']:
    if t.get('sheet') != 'cf_rewrite_review': continue
    pair = f"{t.get('patient_id')}__{t.get('trial_id')}"
    for rw in t.get('rewrites', []):
        if str(rw.get('system_blind_id','')).lower() not in ('tg','trialgpt'): continue
        rat = tg_rat.get(pair, '')
        if not rat:
            n_missing += 1
            continue
        old = rw.get('cited_blocker_text','')
        new = safe_truncate(rat, 8000)
        if new != old:
            rw['cited_blocker_text'] = new
            n_fixed += 1
print(f'fixed {n_fixed} TG cited_blocker_text fields; {n_missing} pairs missing TG rationale')

(FE/'clinician_review.json').write_text(json.dumps(review, indent=2))
# also sync demo
import shutil
shutil.copy(FE/'clinician_review.json', FE/'clinician_review.full.demo.json')
print('synced clinician_review.full.demo.json')

# Verify: any TG cited_blocker_text now ending mid-word?
import re
bad = 0
for t in review['topics']:
    if t.get('sheet') != 'cf_rewrite_review': continue
    for rw in t.get('rewrites', []):
        if str(rw.get('system_blind_id','')).lower() not in ('tg','trialgpt'): continue
        bl = (rw.get('cited_blocker_text','') or '').rstrip()
        last_word = re.split(r'\s+', bl)[-1] if bl else ''
        if re.match(r'^[a-z]+$', last_word) and bl[-1] not in '.!?;:)]"':
            bad += 1
            print(f'  STILL BAD: {t["id"]}  tail={bl[-60:]!r}')
print(f'verification: {bad} TG entries still ending mid-word')
