#!/usr/bin/env python3
"""Claude (Sonnet) independent judgments on the 18-topic K=7 audit.

Each cell: cf_coherent / cf_flipped_cited / cf_new_blocker (yes|no)
Independent from gpt-5 simclin oracle.
"""
import json, pathlib
from datetime import datetime, timezone

FE_PRIVATE = pathlib.Path("<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private")

# Indexed by display_index (1..18), then by label (A/B/C/D)
JUDGMENTS = {
    1: {  # sigir-201411__NCT00990262, ACS rule-out
        "A": ("yes","yes","no","Adds chest pain + history; BP normalized; tachypnea/tachycardia removed."),
        "B": ("yes","yes","no","Adds chest pain + ACS admission; BP normalized. Residual tachy/tachypneic could be acute syndrome."),
        "C": ("yes","yes","no","Chest pain, tachypneic/tachycardic removed, ACS admit, BP normal — clean modification."),
        "D": ("yes","yes","no","Comprehensive: chest+arm radiating, normal ECG/troponin, all exclusions explicitly negated."),
    },
    2: {  # sigir-201425__NCT02119533, TBI registry (8yo boy)
        "A": ("yes","no","no","Just deletes the GCS line; doesn't add the subdural/structural-TBI evidence the trial needs."),
        "B": ("yes","yes","no","Adds emergent head CT with intracranial hemorrhage — structural TBI evidence."),
        "C": ("yes","yes","no","GCS 14/15, no hemorrhage — flips to non-structural TBI subgroup with normal imaging."),
        "D": ("yes","yes","no","GCS 14, no hemorrhage, temporal fracture — non-structural TBI with mild fracture."),
    },
    3: {  # sigir-20144__NCT00305201, Kawasaki history
        "A": ("yes","yes","no","Age aged-up + KD history >12mo added. Slight tension: still describes acute KD presentation."),
        "B": ("yes","yes","no","Age 8yo + KD diagnosed >12mo prior — both inclusion criteria met."),
        "C": ("yes","yes","no","Past tense rewrite; KD diagnosed >12mo ago; age 8."),
        "D": ("yes","yes","no","Most thorough — 9yo follow-up with documented KD history + explicit negation of every exclusion."),
    },
    4: {  # sigir-20158__NCT00832026, OSA in adults
        "A": ("yes","yes","no","Age adult, but OSA diagnosis not explicitly added — chart has symptoms only."),
        "B": ("yes","yes","no","22yo + documented OSA diagnosis — clean fix."),
        "C": ("no","no","no","Contradicts itself: kept '10 yo boy' but added 'Diagnosed OSA'. Modifier failure — incoherent age."),
        "D": ("yes","yes","no","25yo man + documented OSA — clean."),
    },
    5: {  # sigir-201410__NCT02241642, femoral access
        "A": ("yes","yes","no","Removes pulsatile mass and bruit — neutralizes pseudoaneurysm/AVF."),
        "B": ("yes","yes","no","Subtle removal of 'pulsatile' and 'bruit' — adequate but minimal."),
        "C": ("yes","yes","no","Adds 18-24F access size; preserves vascular findings (some tension since pseudoaneurysm still implied)."),
        "D": ("yes","yes","no","Adds 20F access size + consent — kept pseudoaneurysm/bruit which is still an exclusion. Modifier missed the pseudoaneurysm cell."),
    },
    6: {  # sigir-201414__NCT02064959, TBI age 22-65
        "A": ("yes","yes","no","Age in 60s + subdural hematoma + non-penetrating TBI added."),
        "B": ("yes","no","no","Only age changed (85→55); didn't add the required acute subdural / craniotomy / TBI evidence."),
        "C": ("yes","yes","no","Age 62 + GCS 4 + acute subdural + emergent craniotomy + temp <35°C timing — all inclusion criteria."),
        "D": ("yes","yes","no","Age 55 + acute subdural + craniotomy timing. Slight tension: 3-day decline before showing subdural is odd."),
    },
    7: {  # sigir-20153__NCT02258958, lung cancer surgery
        "A": ("yes","yes","no","Adds metastatic lung disease + metastasectomy eligibility."),
        "B": ("yes","yes","no","Adds resectable lung cancer + scheduled lobectomy."),
        "C": ("yes","yes","no","Resectable lung cancer + right upper lobectomy + past-tense framing — comprehensive."),
        "D": ("yes","yes","no","Replaces hip replacement with lobectomy; symptoms now post-thoracic-surgery (plausible)."),
    },
    8: {  # sigir-20154__NCT01858194, VT ablation, renal access
        "A": ("yes","yes","no","Removes renal-artery stenosis + dementia (consent issue) — neutralizes both cited atoms."),
        "B": ("yes","yes","no","Removes 'renal-artery stenosis with'. Cited reason narrowly addressed; acute MI exclusion remains."),
        "C": ("yes","yes","no","Adds ICD, normalizes troponin/CK, removes ST-elevation, adds renal angiography, plans VT ablation — comprehensive."),
        "D": ("yes","yes","no","Adds ICD + planned VT ablation + 'no MI was diagnosed' (tension with elevated troponin)."),
    },
    9: {  # sigir-20145__NCT00077805, acute ischemic stroke
        "A": ("yes","yes","no","Adds leg weakness, neurological exam, explicit acute ischemic stroke assessment."),
        "B": ("yes","yes","no","100 days post-mastectomy + no malignancy + comprehensive stroke workup with MRI."),
        "C": ("yes","yes","no","Leg weakness + NIHSS 2 + CT acute non-hemorrhagic stroke + D-dimer changed to normal (removes DVT concern)."),
        "D": ("yes","yes","no","Comprehensive: leg weakness + NIHSS + head CT positive + removed right calf finding (DVT)."),
    },
    10: {  # sigir-201415__NCT00288938, melanocytic nevus / nevi count
        "A": ("yes","yes","no","Removes age + adds congenital pigmented melanocytic nevus."),
        "B": ("yes","no","yes","Adds 'pigmented lesion suspicious for primary melanoma' — that's not a melanocytic nevus, and 'suspicious for melanoma' could itself be a cancer-related exclusion."),
        "C": ("yes","yes","no","Adds >100 nevi >=2mm with at least one >=4mm — matches inclusion."),
        "D": ("yes","yes","no","Adds outside referring physician + >100 nevi description — matches multiple inclusion criteria."),
    },
    11: {  # sigir-201527__NCT01757119, IDA women 18-45
        "A": ("yes","yes","no","Age and Hb both made ambiguous — both cited symbolic atoms neutralized."),
        "B": ("yes","yes","no","Age 21 + Hb 9.2 (92 g/L, in range 85-105) — clean two-token fix."),
        "C": ("yes","yes","no","Age 18 + D14 cycle info + Hb 9.4 (94 g/L) — comprehensive."),
        "D": ("yes","yes","no","Age 18 + Hb 9.2 + pharmacokinetic timing — most complete."),
    },
    12: {  # sigir-20148__NCT00416169, FTD
        "A": ("yes","yes","no","Documents FTD + Pick's diagnosis 1+ year + MRI atrophy + Pick cells on biopsy — comprehensive."),
        "B": ("yes","yes","no","Reframes from CJD-suggestive features to FTD picture; removes periodic sharp waves + vacuolar changes."),
        "C": ("no","no","no","Adds caregiver/MMSE info but kept original generalized periodic sharp waves + vacuolar changes (CJD pattern) — internal contradiction with FTD claim."),
        "D": ("no","yes","no","Adds FTD diagnosis + caregiver info, but kept periodic sharp waves + vacuolar changes — same CJD/FTD contradiction."),
    },
    13: {  # sigir-20155__NCT00339157, SO-JIA age 2-20
        "A": ("yes","yes","no","'Adolescent girl' + 'recent joint pain' + SO-JIA diagnosis — age made ambiguous."),
        "B": ("yes","yes","no","Age 19 + symptoms 6 months — meets duration and age."),
        "C": ("yes","yes","no","Age 17 + 6 months + prednisone failure + comprehensive rheum assessment + SO-JIA diagnosis."),
        "D": ("yes","yes","no","Age 17 + SO-JIA diagnosis + 7 months + corticosteroid failure + comprehensive assessment."),
    },
    14: {  # sigir-201429__NCT00000430, women 60+, osteoporosis prevention
        "A": ("yes","yes","no","Age removed + 'does not smoke' — both cited atoms neutralized."),
        "B": ("yes","yes","no","Age 60 + 10 cigarettes/day (at threshold; exclusion is >10, so 10 is OK)."),
        "C": ("yes","yes","no","Age 60 only — but smoking 1 pack/day still exceeds the exclusion threshold. Matcher correctly didn't flip."),
        "D": ("yes","yes","no","Age 60 + DXA T-score in range + willing to participate."),
    },
    15: {  # sigir-201417__NCT00492726, intra-abdominal infection
        "A": ("no","yes","no","Surgical narrative becomes incoherent: admitted with ruptured liver abscess, then 'reveals no liver abscess' on laparotomy."),
        "B": ("yes","yes","no","'liver abscess'→'intra-abdominal abscess' — subtle rewording moves it out of the named exclusion."),
        "C": ("yes","yes","no","Adds macroscopic intestinal perforation — matches the TG-cited missing inclusion criterion."),
        "D": ("yes","yes","no","Adds consent + intestinal perforation."),
    },
    16: {  # sigir-20153__NCT00740883, recurrent idiopathic PE
        "A": ("yes","yes","no","Adds remote idiopathic PE history. Acute presentation still provoked by surgery (residual exclusion)."),
        "B": ("yes","yes","no","Removes the post-surgical context — PE now appears unprovoked. But lacks the 6mo VKA history needed."),
        "C": ("yes","yes","no","Removes surgical history + adds first idiopathic PE + 6mo VKA + INR 2-3 — meets full inclusion."),
        "D": ("yes","yes","no","Removes surgery + confirms idiopathic PE + 6mo VKA + consent — comprehensive."),
    },
    17: {  # sigir-201425__NCT00178711, TBI age >16 and <45
        "A": ("yes","yes","no","Age made ambiguous ('adult male'). Symbolic atom for age >16 not strictly satisfied (None ≠ >16)."),
        "B": ("yes","yes","no","Age 17 — meets >16 inclusion."),
        "C": ("yes","yes","no","Age 28 — solidly in range."),
        "D": ("yes","yes","no","Age 17 — same as v5."),
    },
    18: {  # sigir-201520__NCT01463475, age 18-35 (bone marrow study)
        "A": ("yes","yes","no","Removes age + ADL deficits + paratonic rigidity + myoclonic jerks (CJD/spongiform encephalopathy signs)."),
        "B": ("no","yes","yes","Age 28 only; chart still has rapidly progressive dementia + myoclonic jerks (suspicious for CJD — exclusion: transmissible spongiform encephalopathy)."),
        "C": ("yes","yes","no","Age 18 + removed ADL deficits. Myoclonic jerks + paratonic rigidity retained (residual CJD signs)."),
        "D": ("yes","yes","no","Age 29 + removed all CJD-suggestive findings + consent statement — clean."),
    },
}


review = json.load((FE_PRIVATE/"clinician_review.json").open())
topics = review["topics"]
cf_topics = {t["display_index"]: t for t in topics if t.get("sheet")=="cf_rewrite_review"}

# Build evaluations entry for claude_clinician
evals = json.load((FE_PRIVATE/"evaluations.json").open())
evals.setdefault("users", {}).setdefault("claude_clinician", {"clinician_reviews": {}})
reviews = evals["users"]["claude_clinician"]["clinician_reviews"]

now_iso = datetime.now(timezone.utc).isoformat()
n_filled = 0
for idx, judgments in JUDGMENTS.items():
    topic = cf_topics.get(idx)
    if not topic:
        print(f"  WARN: topic #{idx} not found"); continue
    tid = topic["id"]
    rewrites = {}
    for label, (coh, flipped, new_blocker, notes) in judgments.items():
        rewrites[label] = {
            "cf_coherent": coh,
            "cf_flipped_cited": flipped,
            "cf_new_blocker": new_blocker,
            "cf_notes": f"[claude-sonnet judgment] {notes}",
        }
        n_filled += 1
    reviews[tid] = {
        "relevance": {
            "clinician_decision": "",
            "clinician_rationale": "",
            "pairwise_winner": "",
            "cf_coherent": "",
            "cf_flipped_cited": "",
            "cf_new_blocker": "",
            "cf_notes": "",
            "rewrites": rewrites,
            "rationale_axes": {},
        },
        "subcohorts": {},
        "updated_at": now_iso,
    }

(FE_PRIVATE/"evaluations.json").write_text(json.dumps(evals, indent=2))
print(f"filled {n_filled} cells across {len(JUDGMENTS)} topics as 'claude_clinician'")
print(f"evaluations.json users: {list(evals['users'].keys())}")
