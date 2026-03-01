#!/usr/bin/env python3
"""Merge the original accuracy (formatch_pairwise_review) topics with the new
cf_rewrite_review topics into one clinician_review.json.

Sources:
  - Accuracy/formatch topics: clinician_review.pre_cf_audit_K7_cell3.json.bak
    (filter to topics with sheet=='formatch_pairwise_review' so we don't pick
     up stale cf topics)
  - New CF topics: experiments/clinician_validation/cf_audit_K7_cell3.json
"""
import json, pathlib, shutil

FE = pathlib.Path('<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private')
ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')

backup = FE/'clinician_review.pre_cf_audit_K7_cell3.json.bak'
cf_new = ROOT/'experiments/clinician_validation/cf_audit_K7_cell3.json'

bdata = json.loads(backup.read_text())
cf_payload = json.loads(cf_new.read_text())
cf_topics = cf_payload['topics']

# Keep only formatch topics from the backup (drop old cf topics, which we replace)
formatch_topics = []
for t in bdata['topics']:
    if t.get('task_id') == 'cf_rewrite_review': continue
    if t.get('sheet') == 'cf_rewrite_review': continue
    # Ensure task_id is set so the frontend grouping picks it up
    if not t.get('task_id'):
        # Infer from sheet or id prefix
        if t.get('sheet'): t['task_id'] = t['sheet']
        elif 'formatch_pairwise_review' in (t.get('id','') or ''): t['task_id'] = 'formatch_pairwise_review'
    formatch_topics.append(t)

print(f'kept {len(formatch_topics)} accuracy/formatch topics from backup')
print(f'adding {len(cf_topics)} new cf topics')

# Build merged payload
merged = {
    'meta': cf_payload.get('meta', {}),
    'topics': formatch_topics + cf_topics,
}
print(f'total topics: {len(merged["topics"])}')

# Group counts
from collections import Counter
print('  by task_id:', dict(Counter(t.get('task_id','?') for t in merged['topics'])))

target = FE/'clinician_review.json'
target.write_text(json.dumps(merged, indent=2))
print(f'wrote {target}')

# Also write the merged version to the experiments dir for traceability
ROOT_OUT = ROOT/'experiments/clinician_validation/clinician_review_merged.json'
ROOT_OUT.write_text(json.dumps(merged, indent=2))
print(f'archived: {ROOT_OUT}')
