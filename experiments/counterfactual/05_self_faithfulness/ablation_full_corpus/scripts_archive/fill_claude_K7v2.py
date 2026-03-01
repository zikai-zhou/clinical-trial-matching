#!/usr/bin/env python3
"""Rebuild claude_clinician judgments for the new 24-pair K=7 audit (5 systems).

Reuses the previous 72 judgments on topics 1-18 (4 systems each)
and adds new judgments for v5_gpt5 cells + the 6 new pairs.
"""
import json, pathlib
from datetime import datetime, timezone
FE = pathlib.Path("<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private")

# Previous Claude judgments (4-system, topics 1-18) — preserve verbatim
PREVIOUS = {
    1: {"A":("yes","yes","no","Adds chest pain + history; BP normalized."),
        "B":("yes","yes","no","Adds chest pain + ACS admission; BP normalized."),
        "C":("yes","yes","no","Chest pain, tachypnea removed, ACS admit, BP normal."),
        "D":("yes","yes","no","Comprehensive negative workup.")},
    2: {"A":("yes","no","no","Just deletes the GCS line; misses structural-TBI evidence."),
        "B":("yes","yes","no","Adds head CT with hemorrhage."),
        "C":("yes","yes","no","GCS 14, no hemorrhage — non-structural TBI."),
        "D":("yes","yes","no","GCS 14, no hemorrhage, temporal fracture.")},
    3: {"A":("yes","yes","no","Age aged-up + KD history >12mo."),
        "B":("yes","yes","no","Age 8 + KD >12mo."),
        "C":("yes","yes","no","Past tense + KD 12mo ago + age 8."),
        "D":("yes","yes","no","9yo follow-up + KD + negate exclusions.")},
    4: {"A":("yes","yes","no","Adult age, OSA only via symptoms."),
        "B":("yes","yes","no","22yo + documented OSA."),
        "C":("no","no","no","Kept 10yo boy + 'Diagnosed OSA' — incoherent."),
        "D":("yes","yes","no","25yo man + OSA documented.")},
    5: {"A":("yes","yes","no","Removes pulsatile mass + bruit."),
        "B":("yes","yes","no","Minimal removal of pulsatile + bruit."),
        "C":("yes","yes","no","Adds 18-24F access size."),
        "D":("yes","yes","no","Adds 20F + consent.")},
    6: {"A":("yes","yes","no","60s age + subdural + TBI."),
        "B":("yes","no","no","Only age changed; doesn't add subdural/TBI."),
        "C":("yes","yes","no","62yo + GCS 4 + subdural + craniotomy."),
        "D":("yes","yes","no","55yo + subdural + craniotomy timing.")},
    7: {"A":("yes","yes","no","Adds metastatic lung + metastasectomy."),
        "B":("yes","yes","no","Adds resectable lung + lobectomy."),
        "C":("yes","yes","no","Comprehensive lung cancer + lobectomy."),
        "D":("yes","yes","no","Replaces hip with lobectomy.")},
    8: {"A":("yes","yes","no","Removes renal-artery stenosis + dementia."),
        "B":("yes","yes","no","Removes renal-artery stenosis only."),
        "C":("yes","yes","no","Comprehensive ICD + VT ablation + workup."),
        "D":("yes","yes","no","ICD + VT ablation + 'no MI'.")},
    9: {"A":("yes","yes","no","Adds leg weakness + stroke assessment."),
        "B":("yes","yes","no","100 days post-mastectomy + neg malignancy + stroke."),
        "C":("yes","yes","no","Leg weakness + NIHSS + CT stroke."),
        "D":("yes","yes","no","Comprehensive stroke workup.")},
    10: {"A":("yes","yes","no","Removes age + adds congenital nevus."),
         "B":("yes","no","yes","'Suspicious for primary melanoma' ≠ congenital melanocytic nevus, and melanoma could be excluded."),
         "C":("yes","yes","no","Adds >100 nevi >=2mm."),
         "D":("yes","yes","no","Adds outside referring + >100 nevi.")},
    11: {"A":("yes","yes","no","Age and Hb both ambiguous."),
         "B":("yes","yes","no","Age 21 + Hb 9.2 in range."),
         "C":("yes","yes","no","Age 18 + D14 cycle + Hb 9.4."),
         "D":("yes","yes","no","Age 18 + Hb 9.2 + cycle timing.")},
    12: {"A":("yes","yes","no","Comprehensive FTD/Pick + biopsy."),
         "B":("yes","yes","no","Reframes to FTD, removes CJD-suggestive features."),
         "C":("no","no","no","Adds caregiver/MMSE but kept CJD-suggestive findings."),
         "D":("no","yes","no","Adds FTD diagnosis but kept CJD-suggestive findings.")},
    13: {"A":("yes","yes","no","Adolescent girl + SO-JIA diagnosis."),
         "B":("yes","yes","no","Age 19 + 6 months."),
         "C":("yes","yes","no","17yo + 6 months + prednisone failure."),
         "D":("yes","yes","no","17yo + SO-JIA + 7mo + corticosteroid failure.")},
    14: {"A":("yes","yes","no","Age removed + does not smoke."),
         "B":("yes","yes","no","Age 60 + 10 cigs/day (at threshold)."),
         "C":("yes","yes","no","Age 60 only — smoking 1 pack/day still excessive."),
         "D":("yes","yes","no","Age 60 + DXA in range + willing.")},
    15: {"A":("no","yes","no","Surgical narrative becomes incoherent (admitted for abscess but 'reveals no abscess')."),
         "B":("yes","yes","no","'liver' → 'intra-abdominal' subtle rewording."),
         "C":("yes","yes","no","Adds intestinal perforation."),
         "D":("yes","yes","no","Adds consent + intestinal perforation.")},
    16: {"A":("yes","yes","no","Adds remote idiopathic PE history."),
         "B":("yes","yes","no","Removes surgical context, lacks 6mo VKA."),
         "C":("yes","yes","no","Removes surgery + idiopathic PE + 6mo VKA + INR 2-3."),
         "D":("yes","yes","no","Removes surgery + confirmed idiopathic + VKA + consent.")},
    17: {"A":("yes","yes","no","Age made ambiguous ('adult male')."),
         "B":("yes","yes","no","Age 17."),
         "C":("yes","yes","no","Age 28."),
         "D":("yes","yes","no","Age 17.")},
    18: {"A":("yes","yes","no","Removes age + ADL + CJD-suggestive findings."),
         "B":("no","yes","yes","Age 28 only; chart still has CJD-suggestive dementia + myoclonus — likely exclusion."),
         "C":("yes","yes","no","Age 18 + removed ADL deficits."),
         "D":("yes","yes","no","Age 29 + removed CJD findings + consent.")},
}

# New v5_gpt5 cells on topics 1-18 (where v5_gpt5 has data) + all systems on new topics 19-24
NEW_CELLS = {
    # v5_gpt5 cells on existing topics
    2:  {"E": ("yes","yes","no","GCS 6→14, no CT defer; arrived at Bellevue ED.")},
    3:  {"E": ("yes","yes","no","Age 2→8 + Kawasaki 15mo ago + all exclusions negated.")},
    4:  {"E": ("yes","yes","no","22yo man + documented OSA diagnosis.")},
    5:  {"E": ("yes","yes","no","18F access sheath + consent + removes pulsatile mass + bruit.")},
    7:  {"E": ("yes","yes","no","Adds lung cancer + lobectomy + consent + negated exclusions.")},
    11: {"E": ("yes","yes","no","Age 15→23 + Hb 4.2→9.2.")},
    12: {"E": ("yes","yes","no","FTD doc'd 1+yr + frontotemporal atrophy + mild slowing (removes CJD-suggestive sharp waves) + caregiver + ADL.")},
    14: {"E": ("yes","yes","no","Age 61 + controlled HTN + half pack smoking + independent + consent.")},
    15: {"E": ("yes","yes","no","'liver'→'intra-abdominal' + fluids improvement + consent.")},
    16: {"E": ("yes","yes","no","Removes hip surgery + confirms idiopathic PE + 6mo VKA + INR 2-3.")},
    17: {"E": ("yes","yes","no","Age 8→26 + GCS 6 motor 4 post-resus + arrived <2hr.")},

    # All systems on new topics 19-24
    19: {  # sigir-201522__NCT00263315 — pulmonary aspergillosis vs exclusion
        "A": ("yes","yes","no","Removes Aspergillus culture details; chart still suggests cavity lesion but explicit fungal evidence gone."),
        "B": ("yes","yes","no","Deletes Aspergillus culture line; subtle but addresses literal cited fungal-evidence."),
        "C": ("yes","yes","no","Adds AML + chemo + neutropenia + removes Aspergillus."),
        "D": ("yes","yes","no","Adds hematologic malignancy + chemo + neutropenia + negates fungal."),
        "E": ("yes","yes","no","Changes 'moved' to 'did not move' + removes culture details; subtle."),
    },
    20: {  # sigir-201428__NCT00386022 — menopause study
        "A": ("yes","yes","no","Oophorectomy + postsurgical menopause + normal prolactin."),
        "B": ("yes","yes","no","Age 50 + 13mo amenorrhea (meets menopause def). Prolactin still elevated but not v5-cited."),
        "C": ("yes","yes","no","Age 50 + 13mo + neg Factor V Leiden + normal CBC/BUN/Cr."),
        "D": ("yes","yes","no","Age 50 + 13mo + FSH 32 + Factor V + normal labs + prolactin normalized."),
        "E": ("yes","yes","no","Age 50 + 12mo + FSH 30 + prolactin normalized."),
    },
    21: {  # sigir-20148__NCT02387281 — Parkinson FOG vs CJD-suggestive original
        "A": ("no","yes","no","Adds PD + FOG but kept CJD-suggestive biopsy and EEG — clinically contradictory."),
        "B": ("yes","yes","no","Adds PD diagnosis + softens 'severe' to 'mild' cognitive deficits."),
        "C": ("yes","yes","no","Adds PD + UK Brain Bank + FOG observed + levodopa; removes CJD-suggestive findings."),
        "D": ("yes","yes","no","Adds PD + Hoehn-Yahr III + mild slowing instead of sharp waves."),
        "E": ("no","yes","no","Just adds PD diagnosis statement; kept all CJD-suggestive features."),
    },
    22: {  # sigir-201521__NCT00002444 — Cryptosporidium/AIDS study
        "A": ("yes","yes","no","Acid-fast stain showing Cryptosporidium oocysts; replaces Giardia evidence."),
        "B": ("yes","yes","no","Removes Giardia evidence only; doesn't add AIDS or Cryptosporidium but cited reason narrowly addressed."),
        "C": ("yes","yes","no","Adds AIDS + CD4 180 + chronic diarrhea + Cryptosporidium."),
        "D": ("yes","yes","no","Adds AIDS + CD4 120 + ART + chronic + Cryptosporidium."),
        "E": ("yes","yes","no","Removes Giardia evidence; chart now ambiguous (unknown→forward per v5_gpt5)."),
    },
    23: {  # sigir-20143__NCT00894569 — CUP (carcinoma of unknown primary), brain met
        "A": ("yes","yes","no","Removes brain mass."),
        "B": ("yes","yes","no","Removes brain mass mention."),
        "C": ("yes","yes","no","Adds CT-biopsy + adenocarcinoma + postmenopausal + RECIST."),
        "D": ("yes","yes","no","Adds CT-biopsy + adenocarcinoma + no primary identified."),
        "E": ("yes","yes","no","Removes brain mass line."),
    },
    24: {  # sigir-20151__NCT02105532 — AUGIB study, immediate-transfusion exclusion
        "A": ("yes","yes","no","Removes 'packed red blood cell transfusion'."),
        "B": ("yes","yes","no","Same removal."),
        "C": ("yes","yes","no","Same removal."),
        "D": ("yes","yes","no","Adds 'deferred pending initial hemoglobin' — explicit non-immediate framing."),
        "E": ("yes","yes","no","Same removal as A/B/C."),
    },
}

# Merge previous + new
all_judgments = {}
for idx, m in PREVIOUS.items():
    all_judgments.setdefault(idx, {}).update(m)
for idx, m in NEW_CELLS.items():
    all_judgments.setdefault(idx, {}).update(m)

# Apply
review = json.load((FE/"clinician_review.json").open())
cf_topics = {t["display_index"]: t for t in review["topics"] if t.get("sheet")=="cf_rewrite_review"}

evals = json.load((FE/"evaluations.json").open())
evals.setdefault("users", {}).setdefault("claude_clinician", {"clinician_reviews": {}})
# Clear old claude CF entries
revs = evals["users"]["claude_clinician"]["clinician_reviews"]
for k in list(revs.keys()):
    if "cf_rewrite_review" in k or "cf_audit" in k or k.startswith("cf_"):
        del revs[k]

now = datetime.now(timezone.utc).isoformat()
filled = 0
for idx, judgments in all_judgments.items():
    topic = cf_topics.get(idx)
    if not topic: continue
    tid = topic["id"]
    # Filter to labels actually present in this topic
    present_labels = {rw["label"] for rw in topic.get("rewrites", [])}
    rewrites = {}
    for label, (coh, fl, nb, notes) in judgments.items():
        if label not in present_labels: continue
        rewrites[label] = {
            "cf_coherent": coh, "cf_flipped_cited": fl, "cf_new_blocker": nb,
            "cf_notes": f"[claude-sonnet judgment] {notes}",
        }
        filled += 1
    revs[tid] = {
        "relevance": {"clinician_decision":"","clinician_rationale":"","pairwise_winner":"",
                      "cf_coherent":"","cf_flipped_cited":"","cf_new_blocker":"","cf_notes":"",
                      "rewrites":rewrites,"rationale_axes":{}},
        "subcohorts":{},"updated_at":now,
    }

(FE/"evaluations.json").write_text(json.dumps(evals, indent=2))
print(f"filled {filled} cells across {len(all_judgments)} topics as claude_clinician")
print(f"current users in evaluations: {list(evals['users'].keys())}")
