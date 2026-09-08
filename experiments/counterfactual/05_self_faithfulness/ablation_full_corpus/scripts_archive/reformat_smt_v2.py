#!/usr/bin/env python3
"""Better SMT blocker formatting — natural clinical phrasing, deduplicated."""
import json, pathlib, re
from collections import defaultdict

FE_PRIVATE = pathlib.Path("/Users/xyrus/Desktop/llm-smt/clinical-trial-annotation-frontend/private")
ROOT = pathlib.Path("/Users/xyrus/Desktop/llm-smt/TrialGPT-SMT-Refactored")

smt_targets = {}
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl").open():
    if not ln.strip(): continue
    r = json.loads(ln)
    a = (r.get("systems") or {}).get("aegis", {})
    if a.get("targets"): smt_targets[r["pair"]] = a["targets"]


# === Atom-name → noun phrase ===

# Strip leading templates
_TEMPLATE_PREFIXES = [
    r"^patient_has_finding_of_",
    r"^patient_has_diagnosis_of_",
    r"^patient_has_symptoms_of_",
    r"^patient_has_episode_of_",
    r"^patient_has_history_of_",
    r"^patient_is_",
    r"^patient_has_",
]

def strip_prefix(name):
    for pat in _TEMPLATE_PREFIXES:
        name = re.sub(pat, "", name)
    return name


# Common medical phrase polishes
_PHRASE_FIXES = [
    # Numeric "value" atoms — collapse to clean noun
    (r"\bpatient_age_value_recorded_(now_|inthehistory_)?in_years\b", "age (years)"),
    (r"\bpatient_age_value_recorded_now_in_years_known\b", "age (years, known)"),
    (r"\bpatient_known_age_value_recorded_(now_|inthehistory_)?in_years\b", "age (years, known)"),
    (r"\bpatient_age_value_recorded_(at_treatment_initiation_)?in_years\b", "age at treatment initiation (years)"),
    (r"\bpatient_hemoglobin_finding_value_recorded_(now_|inthehistory_)?withunit_grams_per_liter\b", "hemoglobin (g/L)"),
    (r"\bpatient_glasgow_coma_score_value_recorded_(now_|inthehistory_)?withunit_score\b", "Glasgow Coma Scale score"),
    (r"\btime_since_kawasaki_disease_diagnosis_in_months\b", "time since Kawasaki diagnosis (months)"),
    (r"\bpatient_disease_value_recorded_(now_|inthehistory_)?withunit_months\b", "disease duration (months)"),
    # Risk factors / has_risk_factors_for (apply BEFORE prefix-stripping ideally, but also catch post-strip)
    (r"\brisk_factors_for_at_risk_of_variant_creutzfeldt_jakob_disease\b", "risk factors for variant Creutzfeldt-Jakob disease"),
    (r"\brisk_factors_for_human_transmissible_spongiform_encephalopathy\b", "risk factors for transmissible spongiform encephalopathy"),
    (r"\brisk_factors_for_", "risk factors for "),
    # Condition phrases
    (r"\bcondition_unstable\b", "unstable clinical condition"),
    (r"\bpatient_s_condition_unstable\b", "unstable clinical condition"),
    (r"\bs_condition_unstable\b", "unstable clinical condition"),
    (r"\bhemodynamic_instability\b", "hemodynamic instability"),
    (r"\baneurysm\b", "aneurysm"),
    (r"\blimb_ischemia\b", "limb ischemia"),
    (r"\bpseudoaneurysm\b", "pseudoaneurysm"),
    (r"\barteriovenous_fistula\b", "arteriovenous fistula"),
    (r"\bsubdural_hematoma\b", "subdural hematoma"),
    (r"\btraumatic_brain_injury\b", "traumatic brain injury"),
    (r"\bcongenital_pigmented_melanocytic_nevus\b", "congenital pigmented melanocytic nevus"),
    (r"\bsecondary_malignant_neoplasm_of_lung\b", "metastatic lung disease"),
    (r"\bacute_febrile_mucocutaneous_lymph_node_syndrome\b", "acute febrile mucocutaneous lymph node syndrome (Kawasaki)"),
    (r"\bsystemic_onset_juvenile_chronic_arthritis\b", "systemic-onset juvenile chronic arthritis (SO-JIA)"),
    (r"\bcreutzfeldt_jakob_disease\b", "Creutzfeldt-Jakob disease"),
    (r"\btransmissible_spongiform_encephalopathy\b", "transmissible spongiform encephalopathy"),
    (r"\btobacco_user\b", "tobacco use"),
    (r"\bdisability_of_lower_limb\b", "lower-limb disability"),
    (r"\bcerebrovascular_accident\b", "stroke/cerebrovascular accident"),
    (r"\bpulmonary_embolism\b", "pulmonary embolism"),
    (r"\bidiopathic\b", "idiopathic"),
    (r"\bischemic_stroke\b", "ischemic stroke"),
    (r"\bpick_complex\b", "Pick complex (frontotemporal dementia)"),
    (r"\bfrontotemporal_lobar_atrophy\b", "frontotemporal lobar atrophy"),
    (r"\bprimary_progressive_aphasia\b", "primary progressive aphasia"),
    (r"\bnon_penetrating_traumatic_brain_injury\b", "non-penetrating traumatic brain injury"),
    (r"\babscess_of_liver\b", "liver abscess"),
    (r"\brenal_artery_stenosis\b", "renal artery stenosis"),
    (r"\brenovascular_abnormality\b", "renovascular abnormality"),
    (r"\bchest_pain\b", "chest pain"),
    (r"\binjury_severity\b", "injury severity"),
    (r"\bability_to_understand_study_requirements\b", "ability to understand study requirements"),
    (r"\bcondition_that_compromises_ability_to_safely_provide_bone_marrow_donation\b", "condition compromising bone marrow donation safety"),
    (r"\bdegenerative_brain_disorder\b", "degenerative brain disorder"),
    (r"\bdementia\b", "dementia"),
    (r"\bmalignant_neoplastic_disease\b", "malignant neoplastic disease"),
    (r"\bmay_otherwise_compromise_follow_up\b", "may otherwise compromise follow-up"),
]

def humanize_var(name):
    s = name
    for pat, repl in _PHRASE_FIXES:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    s = s.replace("_", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def temporal_label(name):
    if "inthehistory" in name: return "in the past"
    if "_now" in name: return "currently"
    return None

def strip_temporal(name):
    return name.replace("_inthehistory","").replace("_now","")

def humanize_atom(atom_name):
    """Return (cleaned_phrase, temporal_label, qualifier_text or None)."""
    if atom_name.startswith("__THRESH__::"):
        parts = atom_name.split("::")
        if len(parts) == 4:
            _, var, op, val = parts
            op_word = {"ge":"≥","le":"≤","gt":">","lt":"<","eq":"=","ne":"≠"}.get(op, op)
            phrase = humanize_var(strip_prefix(strip_temporal(var)))
            return f"{phrase} {op_word} {val}", None, None
    qual = None
    base = atom_name
    if "@@" in atom_name:
        base, qual = atom_name.split("@@", 1)
        qual = qual.replace("_"," ").strip()
    temp = temporal_label(base)
    base = strip_temporal(base)
    base = strip_prefix(base)
    phrase = humanize_var(base)
    return phrase, temp, qual


def format_blockers(targets):
    """Group by (phrase) — merge current+historical pairs into one item."""
    add_buckets = defaultdict(set)     # phrase -> {timeframes}
    remove_buckets = defaultdict(set)
    add_qualifiers = defaultdict(list)  # phrase -> [qualifier_text]
    remove_qualifiers = defaultdict(list)
    setval_lines = []
    silent_lines = set()

    for t in targets:
        atom = t.get("atom","")
        cv = t.get("current_value"); tv = t.get("target_value")
        phrase, temp, qual = humanize_atom(atom)

        if isinstance(cv, bool) and isinstance(tv, bool):
            bucket_qual = qual
            if cv is False and tv is True:
                add_buckets[phrase].add(temp or "any")
                if qual: add_qualifiers[phrase].append(qual)
            elif cv is True and tv is False:
                remove_buckets[phrase].add(temp or "any")
                if qual: remove_qualifiers[phrase].append(qual)
            else:
                setval_lines.append(f"  • {phrase}: {cv} → {tv}")
        elif tv is None:
            silent_lines.add(phrase)
        else:
            setval_lines.append(f"  • {phrase}: currently {cv}, must be {tv}")

    def render_bucket(buckets, qualifiers):
        out = []
        for phrase, times in buckets.items():
            time_str = ""
            if times == {"currently"}:
                time_str = " (currently)"
            elif times == {"in the past"}:
                time_str = " (in the past)"
            elif times == {"currently","in the past"} or {"currently","in the past"} <= times:
                time_str = " (currently or in the past)"
            quals = qualifiers.get(phrase, [])
            q_str = ""
            if quals:
                # Deduplicate qualifiers
                unique_quals = list(dict.fromkeys(quals))
                q_str = " — " + "; ".join(unique_quals)
            out.append(f"  • {phrase}{time_str}{q_str}")
        return out

    blocks = []
    add_lines = render_bucket(add_buckets, add_qualifiers)
    if add_lines:
        blocks.append("Findings/diagnoses the chart should ESTABLISH (currently missing):\n" + "\n".join(add_lines))
    rem_lines = render_bucket(remove_buckets, remove_qualifiers)
    if rem_lines:
        blocks.append("Findings/diagnoses the chart should NOT establish (currently present, must be removed or contradicted):\n" + "\n".join(rem_lines))
    if setval_lines:
        blocks.append("Specific values to change:\n" + "\n".join(setval_lines))
    if silent_lines:
        blocks.append("Quantitative facts to leave unspecified (chart should no longer commit to a value):\n" +
                      "\n".join(f"  • {x}" for x in silent_lines))

    if not blocks: return "(no symbolic targets)"
    header = "To make this patient eligible, the chart should be modified as follows:\n"
    return header + "\n\n".join(blocks)


# Test on a few cases
sample_pairs = ["sigir-201411__NCT00990262", "sigir-201410__NCT02241642", "sigir-201515__NCT01464633"]
for p in sample_pairs:
    if p in smt_targets:
        print(f"=== {p} ===")
        print(format_blockers(smt_targets[p]))
        print()

# Apply to clinician_review.json
review = json.load((FE_PRIVATE/"clinician_review.json").open())
n_patched = 0
for t in review["topics"]:
    if t.get("sheet") != "cf_rewrite_review": continue
    pair = f"{t['patient_id']}__{t['trial_id']}"
    targets = smt_targets.get(pair, [])
    for rw in t.get("rewrites", []):
        if rw.get("system_blind_id") != "aegis": continue
        rw["cited_blocker_text"] = format_blockers(targets)
        n_patched += 1
(FE_PRIVATE/"clinician_review.json").write_text(json.dumps(review, indent=2))
print(f"\npatched {n_patched} SMT rewrites")
