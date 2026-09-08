#!/usr/bin/env python3
"""Append a clear "[…upstream truncation]" marker on TG cited_blocker_text
entries whose source TG rationale was itself truncated mid-word by the
matcher's 3000-token cap. Avoids the impression that the UI is at fault."""
import json, pathlib, re, shutil

FE = pathlib.Path('/Users/xyrus/Desktop/llm-smt/clinical-trial-annotation-frontend/private')
review = json.load((FE/'clinician_review.json').open())
SUFFIX = "\n\n[…rationale truncated at the upstream TrialGPT matcher's 3000-token cap; remaining per-criterion verdicts could not be obtained.]"

n_marked = 0
for t in review['topics']:
    if t.get('sheet') != 'cf_rewrite_review': continue
    for rw in t.get('rewrites', []):
        if str(rw.get('system_blind_id','')).lower() not in ('tg','trialgpt'): continue
        bl = (rw.get('cited_blocker_text','') or '').rstrip()
        if not bl: continue
        last_word = re.split(r'\s+', bl)[-1]
        # mid-word truncation = lowercase word with no terminator AND already-long body
        if (re.match(r'^[a-z]+$', last_word)
            and bl[-1] not in '.!?;:)]"'
            and len(bl) > 1500):
            rw['cited_blocker_text'] = bl + SUFFIX
            n_marked += 1
print(f'marked {n_marked} TG entries as upstream-truncated')

(FE/'clinician_review.json').write_text(json.dumps(review, indent=2))
shutil.copy(FE/'clinician_review.json', FE/'clinician_review.full.demo.json')
print('synced demo file')
