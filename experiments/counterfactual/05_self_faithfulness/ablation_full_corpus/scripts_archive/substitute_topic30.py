#!/usr/bin/env python3
"""Substitute pairwise topic 30 (sigir-20143__NCT00771563) with sigir-201428__NCT01280292
because the original has an upstream cohort-processing issue. The substitute is
the same cell (both_wrong, aegis vs shahlab, gold=eligible, both said ineligible).
"""
import json, os, pathlib, re, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path('<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored')
FE   = pathlib.Path('<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private')
NEW  = 'sigir-201428__NCT01280292'
OLD_ID = 'formatch_pairwise_review__32__sigir-20159__NCT00883493'  # current topic 30 after prev sub
# Actually find the current topic 30 dynamically
import sys
_review_tmp = json.load((FE/'clinician_review.json').open())
for _t in _review_tmp['topics']:
    if str(_t.get('sample_id'))=='32' and _t.get('sheet')=='formatch_pairwise_review':
        OLD_ID = _t['id']; break
print(f'current topic 30 id: {OLD_ID}')

# Load disagreements row for note + criteria
note_text, inc, exc = None, None, None
for ln in (ROOT/'backup/overnight/disagreements_full.jsonl').open():
    if f'"{NEW}"' in ln:
        o = json.loads(ln)
        note_text = json.loads(o['note_excerpt']) if isinstance(o['note_excerpt'], str) else o['note_excerpt']
        note_text = note_text['text']
        inc = o['inc_excerpt']; exc = o['exc_excerpt']
        break
assert note_text and inc and exc

def grab(p, pair, key):
    for ln in p.open():
        if f'"{pair}"' in ln:
            return json.loads(ln).get(key)
    return None

aegis_rat = grab(ROOT/'matchers/systems/aegis/rationales.jsonl', NEW, 'verbalized_rationale')
shah_rat  = grab(ROOT/'matchers/systems/shahlab/rationales.jsonl', NEW, 'rationale')
assert aegis_rat and shah_rat

trial_listing = f"Inclusion criteria: {inc.strip()}\nExclusion criteria: {exc.strip()}"
trial_inc = f"Inclusion criteria: {inc.strip()}"
trial_exc = f"Exclusion criteria: {exc.strip()}"

new_topic = {
  "id": "formatch_pairwise_review__32__sigir-201428__NCT01280292",
  "sheet": "formatch_pairwise_review",
  "sample_id": "30",
  "retrieval_objective": "Rationale-comparison clinician validation",
  "retrieval_objective_description": "Compare two blinded prescreen rationales for the same patient-trial pair. Form your own verdict first, then pick which rationale you'd prefer.",
  "patient_id": "sigir-201428",
  "trial_id": "NCT01280292",
  "cell": "both_wrong",
  "cell_description": "Cell: Both systems' verdicts disagree with gold (concordant-wrong).",
  "patient_note": note_text,
  "trial_listing": trial_listing,
  "trial_inclusion": trial_inc,
  "trial_exclusion": trial_exc,
  "rationale_A_system_blind_id": "aegis",
  "rationale_A_verdict": "ineligible",
  "rationale_A_text": aegis_rat,
  "rationale_B_system_blind_id": "shahlab",
  "rationale_B_verdict": "ineligible",
  "rationale_B_text": shah_rat,
  "gold_indep": "ineligible",
  "gold_balanced": "eligible",
  "relevance_definition": "This is a prescreen task: you are deciding whether to send this patient on to an in-person screening visit, not enrolling them today.",
  "relevance_instructions": "Read the chart and trial criteria first and form your own prescreen verdict. Then compare the two rationales (blinded — you do not see which system produced which).",
  "union_bucket_used_for_sampling": "",
  "any_trial_relevant_from_llm_judge": "",
  "relevant_subcohorts": [],
  "any_trial_relevant_and_eligible_from_llm_judge": "",
  "relevant_subcohorts_eligibility_determination": [],
  "relevance_rationale": "",
  "display_index": 30
}

review = json.load((FE/'clinician_review.json').open())
for i,t in enumerate(review['topics']):
    if t.get('id') == OLD_ID:
        review['topics'][i] = new_topic
        print(f'replaced topic at idx {i}: {OLD_ID} -> {new_topic["id"]}')
        break
else:
    raise SystemExit('old topic not found')
(FE/'clinician_review.json').write_text(json.dumps(review, indent=2))

# Migrate any review entries keyed under the old id (e.g., simclin_demo); we'll re-run gpt-5 below
evals = json.load((FE/'evaluations.json').open())
for user, ub in evals.get('users',{}).items():
    revs = ub.get('clinician_reviews',{})
    if OLD_ID in revs:
        del revs[OLD_ID]
        print(f"  cleared stale review for user={user}")
(FE/'evaluations.json').write_text(json.dumps(evals, indent=2))

# Now re-run simclin gpt-5 for this single topic: forced binary verdict + 5-axis ratings + actionability
PROMPT_VERDICT = """You are a clinical-trial prescreening clinician. Independently decide whether this patient is likely eligible for this trial. You MUST choose accept or reject — "uncertain" is not allowed.

PATIENT CHART:
{chart}

TRIAL:
{trial}

OUTPUT (STRICT JSON):
{{"verdict":"accept"|"reject","brief_reasoning":"<1-2 sentences>","what_to_check_at_visit":"<one sentence>"}}
"""
PROMPT_PW = """You are a prescreening clinician. Compare two blinded rationales for the same patient-trial pair. For EACH rationale rate 1-5 on five axes:
  logical_consistency (does the reasoning support its verdict?)
  chart_traceability (are cited chart facts present + correctly read?)
  criterion_completeness (does it cover the decisive criteria?)
  support_score (overall credibility of the rationale)
  actionability (does it tell the visiting clinician what to verify?)

Also pick a winner: A, B, or tie.

PATIENT CHART:
{chart}

TRIAL:
{trial}

RATIONALE A:
{a}

RATIONALE B:
{b}

OUTPUT (STRICT JSON):
{{
  "pairwise_winner":"a"|"b"|"tie",
  "rationale_axes":{{
    "A":{{"logical_consistency":1-5,"chart_traceability":1-5,"criterion_completeness":1-5,"support_score":1-5,"actionability":1-5}},
    "B":{{"logical_consistency":1-5,"chart_traceability":1-5,"criterion_completeness":1-5,"support_score":1-5,"actionability":1-5}}
  }}
}}
"""
def gpt5(prompt):
    ep = os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ["OPENAI_ENDPOINT"]
    base = ep.split("/openai/")[0]
    key = os.environ["OPENAI_API_KEY"]
    body = {"model":"gpt-5","messages":[{"role":"user","content":prompt}],
            "max_completion_tokens":3000,"response_format":{"type":"json_object"}}
    req = urllib.request.Request(
        f"{base}/openai/deployments/gpt-5/chat/completions?api-version=2024-12-01-preview",
        data=json.dumps(body).encode(),
        headers={"api-key":key,"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    txt = resp["choices"][0]["message"]["content"] or ""
    try: return json.loads(txt)
    except:
        m = re.search(r"\{[\s\S]*\}", txt); return json.loads(m.group(0)) if m else {}

chart = note_text[:5000]; trial = trial_listing[:3000]
v = gpt5(PROMPT_VERDICT.format(chart=chart, trial=trial))
p = gpt5(PROMPT_PW.format(chart=chart, trial=trial, a=aegis_rat[:2500], b=shah_rat[:2500]))

verdict = str(v.get('verdict','')).lower()
if verdict not in ('accept','reject'): verdict = 'reject'
rel = {
  'clinician_decision': verdict,
  'clinician_rationale': (v.get('brief_reasoning','') or '').strip(),
  'uncertain_reason': (v.get('what_to_check_at_visit','') or '').strip(),
  'pairwise_winner': (str(p.get('pairwise_winner','')).lower() if str(p.get('pairwise_winner','')).lower() in ('a','b','tie') else 'tie'),
  'rationale_axes': p.get('rationale_axes',{})
}
evals = json.load((FE/'evaluations.json').open())
evals['users']['simclin_demo']['clinician_reviews'][new_topic['id']] = {'relevance': rel}
(FE/'evaluations.json').write_text(json.dumps(evals, indent=2))
print('simclin updated:', json.dumps(rel, indent=2)[:600])
