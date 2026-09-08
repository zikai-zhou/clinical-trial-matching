#!/usr/bin/env python3
"""Claude (this session) judgments on the K=7 CF audit for cell 3.

For each topic, each rewrite (A-G) is rated:
  cf_coherent       : yes | no | partial  - is the modified chart clinically coherent?
  cf_flipped_cited  : yes | no | partial  - does it address ALL cited blockers?
  cf_new_blocker    : yes | no            - introduces NEW ineligibility grounds?
  cf_notes          : 1-2 sentence reasoning

Keyed by topic id (cf_audit_K7_cell3__NN__pair).
"""
JUDGMENTS = {
    # ──────────────────────────────────────────────────────────────────
    # Topic 01: sigir-201423__NCT02092675 — COPD stable-condition trial
    # Original: 63yo heavy smoker w/ acute COPD exacerbation (purulent sputum,
    # home O2, cyanotic, tachypneic), psoriasis (autoimmune), diabetes.
    # Cited blockers should address: acute COPD exacerbation + autoimmune (psoriasis).
    # ──────────────────────────────────────────────────────────────────
    "cf_audit_K7_cell3__01__sigir-201423__NCT02092675": {
        "A": ("yes","yes","no","v5_cot_gpt5: Cleanly removes acute exacerbation (stable months, no O2, no cyanosis/tachypnea) and psoriasis. Coherent."),
        "B": ("yes","yes","no","shah: Comprehensive removal — stable months, no exacerbations, no psoriasis, replaces rales with decreased breath sounds."),
        "C": ("yes","yes","no","tg: Addresses cited exacerbation cleanly; only cited blocker was exacerbation. (Note: psoriasis not cited but kept.)"),
        "D": ("no","no","no","v5_blockers: Internal contradiction — narrative says baseline/stable >1mo but kept cyanotic/tachypneic/diffuse rales on exam."),
        "E": ("yes","yes","no","v5_cot: Cited blocker (exacerbation) addressed. Awkward 'not cyanotic, tachypneic' phrasing but interpretable as stable."),
        "F": ("no","yes","no","aegis: Atom-level explicit denial ('not felt to represent acute exacerbation') contradicts the kept acute symptom narrative (purulent sputum, home O2, cyanotic, tachypneic)."),
        "G": ("yes","yes","no","v5: Clean stable baseline. Cited blocker (acute exacerbation) addressed cleanly. (Psoriasis kept but wasn't cited.)"),
    },
    # Topic 02: sigir-201526__NCT00967746 — IUD contraception trial. Original: 28yo G1P0A0 w/ ectopic pregnancy, infertility, adhesions.
    "cf_audit_K7_cell3__02__sigir-201526__NCT00967746": {
        "A": ("yes","yes","no","shah: G1P1, contraception counseling, normal pelvis, no adhesions, βhCG<5, LMP 4d ago. Comprehensive removal of all blockers."),
        "B": ("yes","yes","no","v5_blockers: G1P1, contraception, no infertility, no adhesions, βhCG<5. Addresses parity + pregnancy + ectopic predispositions."),
        "C": ("yes","yes","no","tg: G2P1, contraception, healthy BMI, no infertility, normal tubes, βhCG negative."),
        "D": ("yes","yes","no","v5: G1P1, βhCG<5, no infertility, no adhesions; explicitly notes 'no anatomic or infectious risk factors'."),
        "E": ("yes","yes","no","aegis: G2P1, intact uterus, contraception in use, fit and well, no ectopic/predispositions — explicit atom-level negations, very clean."),
        "F": ("yes","yes","no","v5_cot: G1P1, not pregnant, βhCG 2, normal tubes; explicit 'no ectopic pregnancy and no tubal disease'."),
        "G": ("yes","yes","no","v5_cot_gpt5: G2P1, contraception, normal pelvis, no ectopic, βhCG<5, AND adds uterine length 7.5cm (the most thorough — even meets length inclusion criterion)."),
    },
    # Topic 03: sigir-201428__NCT02551367 — PCOS infertility trial. Original: amenorrhea + hyperprolactinemia + galactorrhea.
    "cf_audit_K7_cell3__03__sigir-201428__NCT02551367": {
        "A": ("yes","yes","no","shah: Removes nipple discharge, normalizes prolactin, adds PCOS via TVUS w/ Rotterdam criteria. Comprehensive."),
        "B": ("yes","yes","no","v5_blockers: Only normalizes prolactin (the cited blocker). PCOS inclusion remains unmet but only hyperprolactinemia was cited — narrow but complete on cited."),
        "C": ("yes","yes","no","aegis: Adds PCOS via TVUS + Rotterdam + hirsutism + elevated testosterone + normal prolactin + no discharge. Atom-level very clean."),
        "D": ("yes","yes","no","v5_cot_gpt5: Adds PCOS via ultrasound + Rotterdam + normal prolactin + no discharge + mild hirsutism + acne. Thorough."),
        "E": ("yes","yes","no","tg: Adds PCOS w/ Rotterdam + normal prolactin + hirsutism + acne + acanthosis-like features. Comprehensive."),
        "F": ("yes","yes","no","v5: Changes to irregular menses + hirsutism + acne + normal prolactin + PCOS ultrasound. Comprehensive."),
        "G": ("yes","yes","no","v5_cot: Only normalizes prolactin + removes discharge (cited blocker addressed). PCOS inclusion still fails but only hyperprolactinemia was cited."),
    },
    # Topic 04: sigir-20155__NCT00948610 — RA trial. Original: 2 weeks joint pain (need ≥4 weeks + ACR criteria).
    "cf_audit_K7_cell3__04__sigir-20155__NCT00948610": {
        "A": ("yes","no","no","tg: Adds ACR features (morning stiffness, hand MCP/PIP, symmetric, nodules, RF+) but kept '2 weeks' duration — duration requirement not addressed."),
        "B": ("yes","yes","no","shah: 5 weeks + hand involvement + RF+ + nodules + radiographic changes. Comprehensive ACR-meeting CF."),
        "C": ("yes","yes","no","v5_cot: Cited blocker was duration only ('criteria 1-4 must be present for at least four weeks'); extending 2→5 weeks addresses it."),
        "D": ("yes","yes","no","v5: Cited blocker was duration only; extending to 6 weeks addresses it."),
        "E": ("no","no","no","aegis: Awkward 'on top of earlier joint symptoms that began just over four weeks ago' while keeping '2 weeks' — contradictory."),
        "F": ("yes","yes","no","v5_cot_gpt5: 6 weeks + hand joints (MCP/PIP) + symmetric + morning stiffness >1h + RF+. Comprehensive."),
        "G": ("yes","yes","no","v5_blockers: 5 weeks + hand joints + symmetric + persistent morning stiffness + RF+ + multiple joints. Most ACR-complete."),
    },
    # Topic 05: sigir-201517__NCT00119509 — Pap smear trial. Original: cytology negative + HPV+ (need low-grade abnormality).
    "cf_audit_K7_cell3__05__sigir-201517__NCT00119509": {
        "A": ("yes","yes","no","tg: Adds 'minor squamous atypia (low grade epithelial abnormality)' w/ HPV effect + HPV+. Clean."),
        "B": ("yes","yes","no","v5: Adds low-grade abnormality + minor squamous changes + papillomavirus effect. Direct."),
        "C": ("yes","yes","no","shah: Same essential edit — low-grade abnormality + minor changes + HPV effect."),
        "D": ("yes","yes","no","v5_cot_gpt5: Same direct addition of low-grade + minor changes + HPV effect."),
        "E": ("yes","yes","no","aegis: 'minor changes in squamous cells without low-grade epithelial abnormality' + HPV+. Atom-precise (minor_changes_in_squamous_cell), valid per criterion's OR."),
        "F": ("yes","yes","no","v5_cot: Same direct addition; clean rewrite."),
        "G": ("yes","yes","no","v5_blockers: Same direct addition; clean rewrite."),
    },
    # Topic 06: sigir-201428__NCT02442999 — PCOS+sleep apnea trial. Same patient as topic 3.
    "cf_audit_K7_cell3__06__sigir-201428__NCT02442999": {
        "A": ("yes","yes","no","tg: PCOS w/ Rotterdam + normalizes prolactin + hirsutism + acne + testosterone elevated. Comprehensive."),
        "B": ("yes","yes","no","v5_blockers: Cited only prolactin excess; normalized. Cited addressed."),
        "C": ("yes","yes","no","v5_cot_gpt5: Cited only prolactin excess; normalized. Cited addressed."),
        "D": ("yes","yes","no","shah: PCOS via ultrasound + Rotterdam + normalized prolactin + hirsutism + acne. Comprehensive."),
        "E": ("yes","yes","no","v5: Cited only prolactin excess; normalized. Cited addressed."),
        "F": ("yes","yes","no","v5_cot: Cited only prolactin excess; normalized. Cited addressed."),
        "G": ("no","yes","no","aegis: Adds PCOS + normalized prolactin but keeps 'whitish nipple discharge bilaterally' (galactorrhea) — inconsistent with normal prolactin lab."),
    },
    # Topic 07: sigir-201516__NCT00116584 — Bronchiolitis trial 2-12mo. Original: 4yo + sandbox-onset (foreign body suspicion).
    "cf_audit_K7_cell3__07__sigir-201516__NCT00116584": {
        "A": ("no","yes","no","aegis: Convoluted dual-age narrative ('4-month-old ... triage 12mo, caregiver reports 4mo') — incoherent age accounting."),
        "B": ("yes","yes","no","v5_cot: Only cited blocker was age; 4yo→8mo addresses it cleanly. (FB exclusion not cited.)"),
        "C": ("yes","yes","no","tg: 8mo + viral illness + no choking + bronchiolitis exam + hyperinflation on CXR. Comprehensive."),
        "D": ("yes","yes","no","v5_cot_gpt5: Only cited blocker was age; 4yo→10mo addresses it."),
        "E": ("yes","yes","no","shah: 8mo + nasal congestion + no choking + bronchiolitis exam + RSV+ + M-WCBS=5. Most thorough."),
        "F": ("yes","yes","no","v5: Cited age + FB; CF gives 8mo + 'no choking, no gagging'. Both cited addressed."),
        "G": ("yes","yes","no","v5_blockers: Cited age + FB; CF gives 10mo + 'no choking, no sudden onset'. Both addressed."),
    },
    # Topic 08: sigir-201418__NCT02016053 — Cirrhosis+AKI trial (age 18-70). Original: 6mo infant w/ post-op AKI.
    "cf_audit_K7_cell3__08__sigir-201418__NCT02016053": {
        "A": ("yes","yes","no","shah: Cited blocker was age only; 6mo→45yo addresses it. Patient now fits Group B (AKI)."),
        "B": ("yes","yes","no","v5_cot: Cited age only; addressed."),
        "C": ("yes","yes","no","tg: Age + cirrhosis + previously normal baseline renal function. Addresses inclusion (Group A) and age exclusion."),
        "D": ("yes","yes","no","v5_cot_gpt5: Cited age only; addressed."),
        "E": ("yes","yes","no","aegis: Age→18yo + adds cirrhosis + normalizes urine output/creatinine/casts (fits Group A). Comprehensive."),
        "F": ("yes","yes","no","v5: Cited age only; addressed."),
        "G": ("yes","yes","no","v5_blockers: Cited age only; addressed."),
    },
    # Topic 09: sigir-201413__NCT01828697 — Pregnancy VTE trial. Original: 3wk postpartum, no prior VTE.
    "cf_audit_K7_cell3__09__sigir-201413__NCT01828697": {
        "A": ("yes","yes","no","v5: Pregnant 8wk + prior DVT on OCP. Clean."),
        "B": ("yes","yes","no","v5_cot: Pregnant 8wk + prior DVT on OCP. Clean."),
        "C": ("yes","yes","no","aegis: Pregnant + prior unprovoked objectively-confirmed VTE. Explicitly enumerates 'not related to pregnancy/OCP/travel/trauma' to nail unprovoked."),
        "D": ("yes","yes","no","v5_cot_gpt5: Pregnant 10wk + unprovoked DVT confirmed by ultrasound. Clean."),
        "E": ("yes","yes","no","v5_blockers: Pregnant 8wk + DVT 3yr ago on OCP + 2 abortions. Clean."),
        "F": ("yes","yes","no","tg: Pregnant 9wk + DVT on OCP. Clean."),
        "G": ("yes","yes","no","shah: Pregnant 8wk + DVT 2yr ago on OCP. Clean."),
    },
    # Topic 10: sigir-20154__NCT02590653 — STEMI trial (age 35-65, single-vessel CAD). Original: 82yo, no stenosis on angio, CKD, dementia, BP 199/108.
    "cf_audit_K7_cell3__10__sigir-20154__NCT02590653": {
        "A": ("yes","yes","no","v5_blockers: Cited age, LAD stenosis, CKD, dementia, BP — addresses all comprehensively (BP 168/102 borderline but improved)."),
        "B": ("yes","yes","no","v5_cot: Cited age + LAD stenosis — both addressed. (BP/CKD/dementia not cited.)"),
        "C": ("yes","no","no","v5: Cited age + CKD; addresses both. But doesn't add LAD stenosis which was a critical missing inclusion (covered in v5's cite as 'age exclusion')."),
        "D": ("no","no","no","aegis: Atom-level adds STEMI documentation + stricture narrative but keeps 'angiography showed no stenosis or clinically significant disease' — direct contradiction."),
        "E": ("yes","yes","no","tg: Cited age + dementia + LAD stenosis + severe concurrent disease — addresses all."),
        "F": ("yes","yes","no","shah: Cited age + dementia + LAD stenosis + CKD + BP — addresses all. Comprehensive."),
        "G": ("yes","yes","no","v5_cot_gpt5: Cited age + LAD stenosis — both addressed."),
    },
    # Topic 11: sigir-201525__NCT00857701 — Knee mobility trial; excl: osteomyelitis/orthopedic infection. Original: 10yo w/ septic-arthritis-like presentation + osteolytic lesion.
    "cf_audit_K7_cell3__11__sigir-201525__NCT00857701": {
        "A": ("yes","yes","no","v5_cot: No fever/edema/effusion/osteolytic lesion, mild ROM reduction. Removes infection signs cleanly."),
        "B": ("no","yes","no","v5: 10yo s/p TKA — clinically implausible for a child — but does address both cited blockers."),
        "C": ("no","yes","no","v5_blockers: Cited blocker was osteolytic lesion — addressed. But narrative keeps fever, lethargy, edema, effusion → infection picture intact."),
        "D": ("yes","yes","no","tg: Removes fever, swelling, effusion, normal CT. Comprehensive."),
        "E": ("yes","yes","no","shah: No fever/swelling/effusion/osteolytic + keeps limited ROM (mobility inclusion preserved)."),
        "F": ("yes","yes","no","v5_cot_gpt5: No fever/infection signs/osteolytic + keeps ROM deficit. Clean."),
    },
    # Topic 12: sigir-201521__NCT00001081 — HIV+ cryptosporidiosis trial. Original: hiker w/ Giardia (no HIV, wrong pathogen).
    "cf_audit_K7_cell3__12__sigir-201521__NCT00001081": {
        "A": ("yes","yes","no","v5: HIV + crypto oocysts 4-6μ + 4 stools/day for 3 weeks. Comprehensive on all cited."),
        "B": ("yes","yes","no","v5_cot: HIV + 6-8 loose stools + cryptosporidium + explicit 'no Giardia'. Comprehensive."),
        "C": ("yes","yes","no","v5_cot_gpt5: HIV+ + crypto + no Giardia. Clean."),
        "D": ("yes","yes","no","v5_blockers: HIV+ + crypto + antigen + negative for Giardia and bacterial enteropathogens. Most thorough."),
        "E": ("yes","no","no","shah: Cited HIV + crypto + giardia exclusion; CF adds crypto + removes giardia but does NOT add HIV documentation."),
        "F": ("yes","yes","no","tg: Only cited blocker was giardiasis (exclusion); CF removes it via crypto + no-natural-water. Cited addressed."),
    },
    # Topic 13: sigir-201425__NCT00163774 — TBI trial age 17-70 + (GCS<9 OR GCS>8+ICP+CT). Original: 8yo + GCS 6 + no CT/ICP.
    "cf_audit_K7_cell3__13__sigir-201425__NCT00163774": {
        "A": ("yes","yes","no","tg: Age 19 + GCS 10/15 + ICP monitor + CT showing edema/contusion 3.5cm/midline 6mm. Fits the GCS>8+ICP+CT inclusion path."),
        "B": ("yes","yes","no","shah: Age 22 + keeps GCS 6 + adds ICP monitor + CT findings. Fits the GCS<9 path comprehensively."),
        "C": ("yes","yes","no","v5_blockers: Age 28 only. GCS 6 alone qualifies as severe head injury (GCS<9 inclusion path) — minimal but sufficient."),
        "D": ("yes","yes","no","v5: Same — age fix only. GCS 6 satisfies severe-TBI inclusion."),
        "E": ("yes","yes","no","v5_cot_gpt5: Age fix only (28). Same logic."),
        "F": ("yes","yes","no","v5_cot: Age fix only (29). Same logic."),
    },
    # Topic 14: sigir-20153__NCT01444612 — Cancer-related surgery + anticoag prophylaxis trial. Original: 65yo, post-hip-replacement, suspected PE.
    "cf_audit_K7_cell3__14__sigir-20153__NCT01444612": {
        "A": ("yes","yes","no","v5: Replaces hip with hemicolectomy for colon adenocarcinoma + enoxaparin + removes PE symptoms. Addresses both cancer inclusion AND PE exclusion."),
        "B": ("yes","yes","no","v5_cot: Cited cancer + cancer surgery inclusions; CF adds both. (PE exclusion not cited.)"),
        "C": ("yes","yes","no","v5_cot_gpt5: Cited cancer + surgery + anticoag; CF adds all three."),
        "D": ("yes","yes","no","shah: Cited cancer + surgery + anticoag; CF adds all."),
        "E": ("no","yes","no","aegis: Cited only PE diagnosis code; CF adds awkward sentence 'PE diagnosis code not assigned' — addresses cited atom but is narrative non-sequitur."),
        "F": ("yes","yes","no","v5_blockers: Cited cancer + cancer surgery; CF adds both."),
    },
    # Topic 15: sigir-20153__NCT01164046 — Cancer-VTE long-term anticoag trial. Same patient as topic 14.
    "cf_audit_K7_cell3__15__sigir-20153__NCT01164046": {
        "A": ("yes","yes","no","tg: Cited only 'indication for long-term anticoag'; CF adds metastatic prostate cancer + chemo + indication. Cited addressed."),
        "B": ("yes","yes","no","v5_cot_gpt5: Metastatic colon cancer + chemo + confirmed PE 8mo + LMWH ongoing. Comprehensive."),
        "C": ("yes","yes","no","v5_cot: Metastatic prostate cancer + chemo + PE 9mo + 9mo LMWH. Comprehensive."),
        "D": ("yes","yes","no","aegis: Cited only 'indication for long-term anticoag'; CF adds 'has indication' sentence. Cited atom addressed (minimal but valid)."),
        "E": ("yes","yes","no","v5: Metastatic colon cancer + cancer-associated PE 9mo + 9mo LMWH. Comprehensive."),
        "F": ("yes","yes","no","shah: Metastatic prostate cancer + chemo + PE 9mo + 9mo LMWH. Comprehensive."),
    },
    # Topic 16: sigir-201424__NCT00769873 — Elective lap splenectomy. Excl: splenectomy due to trauma. Original: 33yo athlete w/ traumatic splenic rupture.
    "cf_audit_K7_cell3__16__sigir-201424__NCT00769873": {
        "A": ("no","yes","no","shah: Denies trauma + intact spleen but keeps shock vitals (BP 60/30, HR 140) — incoherent with 'intact spleen'."),
        "B": ("yes","yes","no","v5_cot_gpt5: No trauma + elective lap splenectomy + normal vitals + intact spleen. Coherent and comprehensive."),
        "C": ("yes","yes","no","v5: Bike fall but no abdominal strike + no rupture + elective splenectomy + normal vitals. Coherent."),
        "D": ("no","yes","no","aegis: Keeps full traumatic narrative (fall, blunt trauma) but contradicts with 'no hemorrhage, intact spleen, no contraindication'."),
        "E": ("no","yes","no","v5_cot: No trauma in history but keeps shock vitals (BP 60/30, HR 140) and 'atraumatic spleen rupture' — internally contradictory."),
        "F": ("no","no","no","tg: Keeps fall + abdominal trauma narrative but says 'intact spleen'. Trauma mechanism implies trauma-related splenectomy."),
    },
    # Topic 17: sigir-201521__NCT01840891 — Renal transplant + chronic norovirus trial. Original: 32yo w/ giardiasis (no transplant).
    "cf_audit_K7_cell3__17__sigir-201521__NCT01840891": {
        "A": ("yes","yes","no","shah: Renal transplant + chronic diarrhea + chronic norovirus PCR+ + explicit no Giardia. Comprehensive on all cited."),
        "B": ("no","no","no","aegis: Adds 'norovirus infection' sentence but keeps 'ellipsoidal cysts with 2+ nuclei' (Giardia hallmark). Direct contradiction."),
        "C": ("yes","yes","no","v5_blockers: Cited exclusion only (giardiasis); CF removes Giardia cysts. Cited addressed."),
        "D": ("yes","yes","no","v5: Cited Giardia exclusion; CF removes via filtered water + no ova/parasites. Cited addressed."),
        "E": ("yes","yes","no","v5_cot: Cited Giardia exclusion; CF removes Giardia. Cited addressed."),
    },
    # Topic 18: sigir-201530__NCT01562535 — Pulled elbow (nursemaid) trial — children. Original: 47yo male w/ adult elbow dislocation from fall.
    "cf_audit_K7_cell3__18__sigir-201530__NCT01562535": {
        "A": ("yes","yes","no","tg: Age 2 + caregiver-pulled + no deformity/bruising + normal X-ray. Comprehensive."),
        "B": ("no","no","no","aegis: Says 3yo + 'concern for subluxation' but keeps 'severe pain, bruising, swelling, inability to bend' AND keeps X-ray showing ulnar+radial dislocation. Direct contradiction."),
        "C": ("yes","yes","no","v5_blockers: Age 2 + pulled arm + no bruising/swelling + normal X-ray. Comprehensive."),
        "D": ("yes","yes","no","v5: Age 3 + pulled by caregiver + no swelling/bruising + normal X-ray. Comprehensive."),
        "E": ("yes","yes","no","shah: Age 2 + pulled by parent + no deformity/bruising + normal X-ray. Comprehensive."),
        "F": ("yes","yes","no","v5_cot: Age 2 + pulled by adult + no bruising/swelling + normal X-ray. Comprehensive."),
        "G": ("yes","yes","no","v5_cot_gpt5: Age 2 + pulled by adult + no bruising/swelling + normal X-ray. Comprehensive."),
    },
}

if __name__ == '__main__':
    import json, pathlib
    from datetime import datetime, timezone
    FE = pathlib.Path('/Users/xyrus/Desktop/llm-smt/clinical-trial-annotation-frontend/private')
    ROOT = pathlib.Path('/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored')

    audit = json.load((ROOT/'experiments/clinician_validation/cf_audit_K7_cell3.json').open())
    topic_by_id = {t['id']: t for t in audit['topics']}

    evals = json.load((FE/'evaluations.json').open())
    evals.setdefault('users', {}).setdefault('claude_simclin_cell3', {'clinician_reviews': {}})
    revs = evals['users']['claude_simclin_cell3']['clinician_reviews']

    now = datetime.now(timezone.utc).isoformat()
    n_filled = 0
    for topic_id, judgments in JUDGMENTS.items():
        topic = topic_by_id.get(topic_id)
        if not topic:
            print(f'  WARN: topic not found: {topic_id}'); continue
        present_labels = {rw['label'] for rw in topic.get('rewrites', [])}
        rewrites = {}
        for label, val in judgments.items():
            if label not in present_labels:
                print(f'  WARN: label {label} not in {topic_id}'); continue
            coh, fl, nb, notes = val
            rewrites[label] = {
                'cf_coherent': coh,
                'cf_flipped_cited': fl,
                'cf_new_blocker': nb,
                'cf_notes': f'[claude] {notes}',
            }
            n_filled += 1
        revs[topic_id] = {
            'relevance': {
                'clinician_decision': '', 'clinician_rationale': '', 'pairwise_winner': '',
                'cf_coherent': '', 'cf_flipped_cited': '', 'cf_new_blocker': '', 'cf_notes': '',
                'rewrites': rewrites, 'rationale_axes': {},
            },
            'subcohorts': {}, 'updated_at': now,
        }
    (FE/'evaluations.json').write_text(json.dumps(evals, indent=2))
    print(f'\nfilled {n_filled} cells across {len(JUDGMENTS)} topics as claude_simclin_cell3')
