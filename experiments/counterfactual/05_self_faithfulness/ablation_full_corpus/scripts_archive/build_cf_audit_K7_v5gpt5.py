#!/usr/bin/env python3
"""Extended K=7 CF audit: 24 pairs, 5 systems (SMT, v5, TG, Shah, v5_gpt5)."""
import json, pathlib, re, sys, difflib, shutil
from collections import defaultdict

ROOT = pathlib.Path("<local-path>/Desktop/llm-smt/TrialGPT-SMT-Refactored")
FE_PRIVATE = pathlib.Path("<local-path>/Desktop/llm-smt/clinical-trial-annotation-frontend/private")

sample = json.load(open("/tmp/cf_audit_K7_extended.json"))
pairs = sample["pairs"]
print(f"loading {len(pairs)} pairs")

# Load auxiliary data
sigir = {json.loads(l)["_id"]: json.loads(l) for l in (ROOT/"dataset/clinical_trial/sigir/corpus.jsonl").open() if l.strip()}
notes = {json.loads(l)["_id"]: json.loads(l).get("text","") for l in (ROOT/"dataset/clinical_trial/sigir/queries.jsonl").open() if l.strip()}

_inc_re = re.compile(r"(?i)\binclusion\s+criteria\s*:\s*")
_exc_re = re.compile(r"(?i)\bexclusion\s+criteria\s*:\s*")
def split_inc_exc(text):
    if not text: return "",""
    im = _inc_re.search(text); em = _exc_re.search(text)
    i = e = ""
    if im: i = text[im.end(): em.start() if em else len(text)].strip()
    if em: e = text[em.end():].strip()
    return i, e
def trial_info(tid):
    rec = sigir.get(tid) or sigir.get(re.sub(r"(?<=NCT\d{8})[a-z]+$","",tid))
    if not rec: return "","",""
    text = rec.get("text","")
    summary = text.split("Inclusion criteria:")[0].replace("Summary:","").strip() if "Inclusion criteria:" in text else text[:1000]
    inc, exc = split_inc_exc(text)
    return summary, inc, exc

smt_cfs = {r["pair"]: r for r in [json.loads(l) for l in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_smt_gpt5modifier.jsonl").open() if l.strip()] if r.get("cf_chart")}
baseline_cfs = {(r["pair"], r["system"]): r for r in [json.loads(l) for l in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier.jsonl").open() if l.strip()] if r.get("cf_chart")}
rejudge_verdicts = {(r["pair"], r["system"]): r.get("cf_eligibility_under_v2") for r in [json.loads(l) for l in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_baselines_gpt5modifier_rejudged.jsonl").open() if l.strip()]}
sf_main = {r["pair"]: r for r in [json.loads(l) for l in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness.jsonl").open() if l.strip()]}
v5g5_rat = {}
for ln in (ROOT/"experiments/counterfactual/05_self_faithfulness/out/self_faithfulness_v5gpt5.jsonl").open():
    if not ln.strip(): continue
    try: r = json.loads(ln)
    except: continue
    if r.get("cited_rationale"):
        v5g5_rat[r["pair"]] = r["cited_rationale"]
shah_bin_cf = {json.loads(l)["pair"]: json.loads(l).get("eligible_binary") for l in pathlib.Path("/tmp/shah_binary_v2cf.jsonl").open() if l.strip()}

ext_rationales = {}
for sys_id, path in [("v5", ROOT/"matchers/systems/single_shot_llm/rationales.jsonl"),
                     ("tg", ROOT/"matchers/systems/trialgpt/rationales.jsonl"),
                     ("shah", ROOT/"matchers/systems/shahlab/rationales.jsonl")]:
    if path.exists():
        for ln in path.open():
            try: o = json.loads(ln)
            except: continue
            if o.get("pair"):
                ext_rationales[(o["pair"], sys_id)] = (o.get("rationale") or o.get("explanation","") or "")

# === SMT atom humanizer (from earlier) ===
_PHRASE_FIXES = [
    (r"\bpatient_age_value_recorded_(now_|inthehistory_)?in_years\b", "age (years)"),
    (r"\bpatient_age_value_recorded_now_in_years_known\b", "age (years, known)"),
    (r"\bpatient_known_age_value_recorded_(now_|inthehistory_)?in_years\b", "age (years, known)"),
    (r"\bpatient_hemoglobin_finding_value_recorded_(now_|inthehistory_)?withunit_grams_per_liter\b", "hemoglobin (g/L)"),
    (r"\bpatient_glasgow_coma_score_value_recorded_(now_|inthehistory_)?withunit_score\b", "Glasgow Coma Scale score"),
    (r"\btime_since_kawasaki_disease_diagnosis_in_months\b", "time since Kawasaki diagnosis (months)"),
    (r"\bpatient_disease_value_recorded_(now_|inthehistory_)?withunit_months\b", "disease duration (months)"),
    (r"\brisk_factors_for_at_risk_of_variant_creutzfeldt_jakob_disease\b", "risk factors for variant Creutzfeldt-Jakob disease"),
    (r"\brisk_factors_for_human_transmissible_spongiform_encephalopathy\b", "risk factors for transmissible spongiform encephalopathy"),
    (r"\brisk_factors_for_", "risk factors for "),
    (r"\bcondition_unstable\b", "unstable clinical condition"),
    (r"\bpatient_s_condition_unstable\b", "unstable clinical condition"),
    (r"\bs_condition_unstable\b", "unstable clinical condition"),
    (r"\bhemodynamic_instability\b", "hemodynamic instability"),
    (r"\baneurysm\b", "aneurysm"), (r"\blimb_ischemia\b", "limb ischemia"),
    (r"\bpseudoaneurysm\b", "pseudoaneurysm"), (r"\barteriovenous_fistula\b", "arteriovenous fistula"),
    (r"\bsubdural_hematoma\b", "subdural hematoma"),
    (r"\btraumatic_brain_injury\b", "traumatic brain injury"),
    (r"\bcongenital_pigmented_melanocytic_nevus\b", "congenital pigmented melanocytic nevus"),
    (r"\bsecondary_malignant_neoplasm_of_lung\b", "metastatic lung disease"),
    (r"\bacute_febrile_mucocutaneous_lymph_node_syndrome\b", "acute febrile mucocutaneous lymph node syndrome (Kawasaki)"),
    (r"\bsystemic_onset_juvenile_chronic_arthritis\b", "systemic-onset juvenile chronic arthritis (SO-JIA)"),
    (r"\bcreutzfeldt_jakob_disease\b", "Creutzfeldt-Jakob disease"),
    (r"\btransmissible_spongiform_encephalopathy\b", "transmissible spongiform encephalopathy"),
    (r"\btobacco_user\b", "tobacco use"),
    (r"\bcerebrovascular_accident\b", "stroke/cerebrovascular accident"),
    (r"\bpulmonary_embolism\b", "pulmonary embolism"),
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
    (r"\bdisability_of_lower_limb\b", "lower-limb disability"),
]
_TEMPLATE_PREFIXES = [r"^patient_has_finding_of_", r"^patient_has_diagnosis_of_", r"^patient_has_symptoms_of_", r"^patient_has_episode_of_", r"^patient_has_history_of_", r"^patient_is_", r"^patient_has_"]
def strip_prefix(name):
    for pat in _TEMPLATE_PREFIXES: name = re.sub(pat, "", name)
    return name
def humanize_var(name):
    s = name
    for pat, repl in _PHRASE_FIXES: s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    s = s.replace("_"," ").strip(); s = re.sub(r"\s+"," ",s)
    return s
def temporal(name):
    if "inthehistory" in name: return "in the past"
    if "_now" in name: return "currently"
    return None
def strip_temp(n): return n.replace("_inthehistory","").replace("_now","")
def humanize_atom(atom):
    if atom.startswith("__THRESH__::"):
        parts = atom.split("::")
        if len(parts)==4:
            _,var,op,val=parts
            op_word={"ge":"≥","le":"≤","gt":">","lt":"<","eq":"=","ne":"≠"}.get(op,op)
            return f"{humanize_var(strip_prefix(strip_temp(var)))} {op_word} {val}", None, None
    qual=None; base=atom
    if "@@" in atom: base,qual=atom.split("@@",1); qual=qual.replace("_"," ").strip()
    t=temporal(base); base=strip_temp(base); base=strip_prefix(base)
    return humanize_var(base), t, qual
def format_smt_blockers(targets):
    add_b=defaultdict(set); rem_b=defaultdict(set)
    add_q=defaultdict(list); rem_q=defaultdict(list)
    setval=[]; silent=set()
    for t in targets:
        cv,tv = t.get("current_value"), t.get("target_value")
        phrase, tm, qual = humanize_atom(t.get("atom",""))
        if isinstance(cv,bool) and isinstance(tv,bool):
            if cv is False and tv is True:
                add_b[phrase].add(tm or "any");
                if qual: add_q[phrase].append(qual)
            elif cv is True and tv is False:
                rem_b[phrase].add(tm or "any")
                if qual: rem_q[phrase].append(qual)
            else: setval.append(f"  • {phrase}: {cv} → {tv}")
        elif tv is None: silent.add(phrase)
        else: setval.append(f"  • {phrase}: currently {cv}, must be {tv}")
    def render(buckets, quals):
        out=[]
        for phrase,times in buckets.items():
            time_str=""
            if times=={"currently"}: time_str=" (currently)"
            elif times=={"in the past"}: time_str=" (in the past)"
            elif {"currently","in the past"} <= times: time_str=" (currently or in the past)"
            q=list(dict.fromkeys(quals.get(phrase,[])))
            q_str = " — " + "; ".join(q) if q else ""
            out.append(f"  • {phrase}{time_str}{q_str}")
        return out
    blocks=[]
    if add_b: blocks.append("Findings/diagnoses the chart should ESTABLISH (currently missing):\n" + "\n".join(render(add_b, add_q)))
    if rem_b: blocks.append("Findings/diagnoses the chart should NOT establish (currently present, must be removed or contradicted):\n" + "\n".join(render(rem_b, rem_q)))
    if setval: blocks.append("Specific values to change:\n" + "\n".join(setval))
    if silent: blocks.append("Quantitative facts to leave unspecified (chart should no longer commit to a value):\n" + "\n".join(f"  • {x}" for x in silent))
    if not blocks: return "(no symbolic targets)"
    return "To make this patient eligible, the chart should be modified as follows:\n" + "\n\n".join(blocks)

def cited_for_system(pair, system_id):
    if system_id == "aegis":
        a = (sf_main.get(pair, {}).get("systems") or {}).get("aegis", {})
        return format_smt_blockers(a.get("targets", []))
    if system_id == "v5_gpt5":
        return v5g5_rat.get(pair,"(no rationale)")[:2000]
    info = (sf_main.get(pair, {}).get("systems") or {}).get(system_id, {})
    cr = (info.get("cited_rationale") or "").strip()
    if cr: return cr[:2000]
    bl = info.get("blocker_lines") or []
    if bl: return "\n".join(str(x) for x in bl[:10])[:2000]
    return (ext_rationales.get((pair, system_id),"") or "(no rationale)")[:2000]

def word_diff(orig, cf):
    toks_a=re.findall(r"\S+|\s+",orig or ""); toks_b=re.findall(r"\S+|\s+",cf or "")
    sm=difflib.SequenceMatcher(a=toks_a,b=toks_b,autojunk=False); out=[]
    for tag,i1,i2,j1,j2 in sm.get_opcodes():
        if tag=="equal": out.append({"type":"equal","text":"".join(toks_a[i1:i2])})
        elif tag=="delete": out.append({"type":"del","text":"".join(toks_a[i1:i2])})
        elif tag=="insert": out.append({"type":"add","text":"".join(toks_b[j1:j2])})
        elif tag=="replace":
            out.append({"type":"del","text":"".join(toks_a[i1:i2])})
            out.append({"type":"add","text":"".join(toks_b[j1:j2])})
    return out

SYSTEMS_LIST = [
    ("aegis","A","SMT"),
    ("v5","B","Single Shot LLM (gpt-4.1)"),
    ("tg","C","TrialGPT (gpt-4.1)"),
    ("shah","D","Shah (gpt-4.1, binary-forced)"),
    ("v5_gpt5","E","Single Shot LLM (gpt-5)"),
]

def build_rewrite(pair, system_id, label, system_display):
    pid,_ = pair.split("__",1)
    orig = notes.get(pid,"")
    if system_id=="aegis":
        rec=smt_cfs.get(pair)
        if not rec: return None
        cf = rec.get("cf_chart","")
        verdict = "ineligible→eligible (Z3)" if rec.get("simclin_flips_cited") else "ineligible→ineligible (atoms not flipped)"
    else:
        rec=baseline_cfs.get((pair,system_id))
        if not rec: return None
        cf=rec.get("cf_chart","")
        if system_id=="shah":
            sbin=shah_bin_cf.get(pair)
            verdict = ("ineligible→eligible (binary)" if sbin else "ineligible→ineligible (binary)") if sbin is not None else "?"
        else:
            ver=rejudge_verdicts.get((pair,system_id)) or "?"
            verdict=f"ineligible→{ver}"
    return {
        "label":label,"system_blind_id":system_id,"system_display":system_display,
        "cf_chart":cf,"cited_blocker_text":cited_for_system(pair,system_id),
        "system_verdict":verdict,"diff_segments":word_diff(orig[:6000],cf[:6000]),
    }

topics=[]
for idx,pair in enumerate(pairs,1):
    pid,tid=pair.split("__",1)
    summary,inc,exc=trial_info(tid)
    orig=notes.get(pid,"")
    if not orig: continue
    rewrites=[]
    for sys_id,label,disp in SYSTEMS_LIST:
        rw=build_rewrite(pair,sys_id,label,disp)
        if rw: rewrites.append(rw)
    if not rewrites: continue
    topics.append({
        "id":f"cf_audit_K7v2__{idx:02d}__{pair}","sheet":"cf_rewrite_review","task_id":"cf_rewrite_review",
        "display_index":idx,"patient_id":pid,"trial_id":tid,
        "original_chart":orig,
        "trial_listing":f"Summary: {summary}\nInclusion criteria: {inc}\nExclusion criteria: {exc}",
        "trial_inclusion":inc,"trial_exclusion":exc,"rewrites":rewrites,
    })
print(f"built {len(topics)} CF audit topics, total rewrites: {sum(len(t['rewrites']) for t in topics)}")

# Merge with existing pairwise topics
existing_review = json.load((FE_PRIVATE/"clinician_review.json").open())
keep_pairwise = [t for t in existing_review["topics"] if t.get("sheet")=="formatch_pairwise_review"]

merged = []
for i,t in enumerate(keep_pairwise,1):
    t["display_index"]=i; merged.append(t)
for i,t in enumerate(topics,1):
    t["display_index"]=i; merged.append(t)

(FE_PRIVATE/"clinician_review.json").write_text(json.dumps({"topics":merged}, indent=2))
print(f"wrote clinician_review.json: {len(keep_pairwise)} pairwise + {len(topics)} CF = {len(merged)} total")
