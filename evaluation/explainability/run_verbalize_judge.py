#!/usr/bin/env python3
"""
Explainability pilot: verbalize + blind pairwise LLM-as-judge
for SMT vs LLM-direct vs TrialGPT rationales.

Pipeline per (patient, trial) pair:
  1. Invoke each system (SMT, LLM-direct, TrialGPT) to get decision + artifacts.
  2. Verbalize into NL rationale via system-specific prompt (gpt-4.1, T=0).
     LLM-direct passes through its own reasoning text.
  3. Destyle all three rationales together (gpt-4.1, T=0) to strip
     system-identifying fingerprints.
  4. Run three pairwise contests via llm_adjudicate_disagreement.prompt
     with GPT-5 judge; randomize A/B slots per contest.
  5. Aggregate win-rates per system across all contests.

Usage:
  set -a; source .env; set +a
  python -m evaluation.explainability.run_verbalize_judge \
      --sample 5 --data-root /tmp/satir_full_dataset --build-root build
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import random
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine import AzureInferenceEngine  # noqa: E402
from smt_core.inference_engine_5 import AzureInferenceEngine as AzureInferenceEngine5  # noqa: E402
from smt_matcher.match_patient_to_trial import (  # noqa: E402
    Config as SMTConfig,
    run_match_for_side,
)
from smt_matcher.judges.llm_judge import (  # noqa: E402
    run_llm_eligibility_judge,
    _call_engine_text,
    format_trial_description,
    extract_patient_note_text,
)
from smt_matcher.judges.trialgpt_judge import run_trialgpt_judge  # noqa: E402

# ── Prompt locations ──────────────────────────────────────────────────────
MEVAL_PROMPTS = ROOT / "sql_retrieval" / "meval" / "prompts"
P_VERB_SMT = MEVAL_PROMPTS / "verbalize_smt_rationale_prescreen.prompt"
P_VERB_TG = MEVAL_PROMPTS / "verbalize_trialgpt_rationale.prompt"
P_VERB_UNIFIED = MEVAL_PROMPTS / "verbalize_prescreen_unified.prompt"
P_DESTYLE = MEVAL_PROMPTS / "destyle_verbalization.prompt"
P_ADJUDICATE = MEVAL_PROMPTS / "llm_adjudicate_disagreement_prescreen.prompt"
P_ELIG_EXPLICIT = ROOT / "smt_matcher" / "prompts" / "clinical_trial" / "SMTMatcher" / "eligibility.explicit.prompt"


# ── Helpers ───────────────────────────────────────────────────────────────
def load_trial_from_corpus(trial_id: str, corpus_path: pathlib.Path) -> Optional[Dict[str, Any]]:
    if not corpus_path.exists():
        return None
    with open(corpus_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("_id") == trial_id:
                md = obj.get("metadata") or {}
                return {
                    "nct_id": obj.get("_id"),
                    "brief_title": md.get("brief_title") or obj.get("title") or "",
                    "brief_summary": md.get("brief_summary") or obj.get("text") or "",
                    "inclusion_criteria": md.get("inclusion_criteria") or "",
                    "exclusion_criteria": md.get("exclusion_criteria") or "",
                    "diseases_list": md.get("diseases_list") or [],
                    "drugs_list": md.get("drugs_list") or [],
                    "metadata": md,
                }
    return None


def load_patient_from_queries(patient_id: str, queries_path: pathlib.Path) -> Optional[Dict[str, Any]]:
    if not queries_path.exists():
        return None
    with open(queries_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("_id") == patient_id:
                return {
                    "patient_id": patient_id,
                    "_id": patient_id,
                    "text": obj.get("text") or "",
                    "metadata": obj.get("metadata") or {},
                }
    return None


def _decide_corpus_dir(patient_id: str, data_root: pathlib.Path) -> pathlib.Path:
    if patient_id.startswith("sigir"):
        return data_root / "sigir"
    if patient_id.startswith("trec-2021"):
        return data_root / "trec_2021"
    if patient_id.startswith("trec-2022"):
        return data_root / "trec_2022"
    return data_root / "sigir"


# ── Pair sampler ──────────────────────────────────────────────────────────
def sample_pairs_from_fliprate(n: int, seed: int = 13) -> List[Tuple[str, str]]:
    """Sample N pairs from 94-pair run; prefer ones where systems disagreed
    on majority eligibility label."""
    smt_f = ROOT / "evaluation" / "results" / "h1_fliprate_94pairs_3repeats__smt.json"
    llm_f = ROOT / "evaluation" / "results" / "h1_fliprate_94pairs_3repeats__llm_direct-trialgpt.json"
    with open(smt_f) as f:
        smt = json.load(f)["per_pair"]
    with open(llm_f) as f:
        llm = json.load(f)["per_pair"]
    all_keys = sorted(set(smt.keys()) & set(llm.keys()))
    disagreeing, agreeing = [], []
    for k in all_keys:
        def _maj(labels):
            if not labels:
                return None
            return max(set(labels), key=labels.count)
        s = _maj(smt[k].get("smt", {}).get("labels", []))
        l = _maj(llm[k].get("llm_direct", {}).get("labels", []))
        t = _maj(llm[k].get("trialgpt", {}).get("labels", []))
        if len({s, l, t} - {None}) >= 2:
            disagreeing.append(k)
        else:
            agreeing.append(k)
    rng = random.Random(seed)
    rng.shuffle(disagreeing)
    rng.shuffle(agreeing)
    chosen = (disagreeing + agreeing)[:n]
    pairs = []
    for k in chosen:
        pid, tid = k.split("__", 1)
        pairs.append((pid, tid))
    return pairs


# ── Engine wrappers with one retry ────────────────────────────────────────
def _call_with_retry(engine, prompt: str, *, temperature: float = 0.0, tag: str = "") -> str:
    try:
        return _call_engine_text(engine, prompt, temperature=temperature)
    except Exception as e1:
        print(f"  [retry {tag}] {type(e1).__name__}: {e1}", file=sys.stderr)
        time.sleep(2.0)
        return _call_engine_text(engine, prompt, temperature=temperature)


# ── Verbalization ─────────────────────────────────────────────────────────
def _fill(template: str, mapping: Dict[str, str]) -> str:
    out = template
    for k, v in mapping.items():
        out = out.replace(k, v)
    return out


_NAMED_ASSERT_RE = re.compile(
    r"\(assert\s*\(?\s*!.*?:named\s+(REQ\w+)\)+[^;]*?;;\s*\"(.+?)\"",
    re.DOTALL,
)


def _build_req_criterion_map(smt_program_lines: List[str]) -> Dict[str, str]:
    """Extract {REQ_label: criterion_text} mapping from the SMT program's
    inline comments. No pre-computation of "decisive reason" — just exposes
    the human-readable text that the LLM needs to reason about which
    criterion failed."""
    if not smt_program_lines:
        return {}
    text = "\n".join(smt_program_lines)
    out: Dict[str, str] = {}
    for m in _NAMED_ASSERT_RE.finditer(text):
        label, criterion = m.group(1), m.group(2)
        if label not in out:
            out[label] = criterion.strip()
    return out


def _summarize_smt_artifacts(smt_result: Dict[str, Any]) -> Dict[str, Any]:
    """Pull unsat_core + status + criterion descriptions from run_match_for_side output.
    Also surface the full label_status (sat/unsat/unknown) per side so the LLM can
    see which REQs passed vs failed."""
    art = {"sides": {}}
    for side in ("inclusion", "exclusion"):
        sr = smt_result.get(side, {}) or {}
        raw = sr.get("raw") or {}
        er = raw.get("eval_result") or {}
        label_status = er.get("label_status") or {}
        req_map = _build_req_criterion_map(raw.get("smt_program_lines") or [])
        # Attach criterion text to each label
        def decorate(labels):
            return [{"label": l, "criterion": req_map.get(l, "")}
                    for l in (labels or [])]
        # Only surface concrete mined values (nulls are deferred; mentioning them
        # dilutes the signal to the verbalizer).
        pvv = raw.get("patient_var_values") or {}
        concrete = {}
        for k, v in pvv.items():
            val = v.get("value") if isinstance(v, dict) else v
            if val is None:
                continue
            if isinstance(val, str) and val.strip().lower() in {"null", "none", ""}:
                continue
            concrete[k] = {"value": val,
                           "evidence": (v.get("evidence") or "")[:200] if isinstance(v, dict) else ""}
        art["sides"][side] = {
            "sat_like": sr.get("sat_like"),
            "status": er.get("status") or er.get("overall_status_grouped") or er.get("overall_status"),
            "unsat_labels": decorate(label_status.get("unsat") or []),
            "sat_labels": decorate(label_status.get("sat") or [])[:8],  # cap to avoid prompt bloat
            "concrete_mined_values": concrete,
        }
    return art


def _extract_mined_values_summary(smt_side_result: Dict[str, Any]) -> str:
    raw = (smt_side_result or {}).get("raw") or {}
    # Try several common keys
    for key in ("variable_values", "mined_values", "patient_values", "mined_variable_values"):
        v = raw.get(key)
        if v:
            return json.dumps(v, indent=2, default=str)[:6000]
    # Fallback: dump shallow eval_result
    er = raw.get("eval_result") or {}
    return json.dumps({k: er.get(k) for k in ("per_requirement", "status") if k in er},
                      indent=2, default=str)[:6000]


def verbalize_smt(engine, template: str, *, patient_note: str, trial_text: str,
                  smt_result: Dict[str, Any], ref_decision: str, ref_rationale: str) -> str:
    art = _summarize_smt_artifacts(smt_result)
    incl_sat = art["sides"]["inclusion"].get("sat_like")
    excl_sat = art["sides"]["exclusion"].get("sat_like")
    if incl_sat is None or excl_sat is None:
        label = "unknown"
    else:
        label = "eligible" if (incl_sat and not excl_sat) else "ineligible"
    prompt = _fill(template, {
        "#PATIENT_NOTE#": patient_note,
        "#TRIAL_ELIGIBILITY_TEXT#": trial_text,
        "#SYSTEM_DECISION_LABEL#": label,
        "#SYSTEM_DECISION_ARTIFACTS#": json.dumps(art, indent=2, default=str)[:8000],
        "#INCLUSION_EXTRACTED_VARIABLE_VALUES#": _extract_mined_values_summary(smt_result.get("inclusion", {})),
        "#EXCLUSION_EXTRACTED_VARIABLE_VALUES#": _extract_mined_values_summary(smt_result.get("exclusion", {})),
        "#NL_DECISION#": ref_decision,
        "#NL_RATIONALE#": ref_rationale,
    })
    raw = _call_with_retry(engine, prompt, temperature=0.0, tag="verb_smt")
    return _extract_rationale_text(raw)


def verbalize_unified(engine, template: str, *, patient_note: str, trial_text: str,
                      decision_label: str, source_rationale: str, source_artifacts: str) -> str:
    """One uniform verbalizer applied to EVERY system for a controlled comparison.

    Each system's raw output is translated into the same (rationale, deferred_criteria)
    structure so that downstream blind judging does not reward effort-asymmetry in
    verbalizer prompts.
    """
    prompt = _fill(template, {
        "#PATIENT_NOTE#": patient_note,
        "#TRIAL_ELIGIBILITY_TEXT#": trial_text,
        "#SYSTEM_DECISION_LABEL#": decision_label,
        "#SOURCE_RATIONALE#": (source_rationale or "")[:8000],
        "#SOURCE_ARTIFACTS#": (source_artifacts or "")[:8000],
    })
    raw = _call_with_retry(engine, prompt, temperature=0.0, tag="verb_unified")
    return _extract_rationale_text(raw)


def verbalize_trialgpt(engine, template: str, *, patient_note: str, trial_text: str,
                       tg_result: Dict[str, Any], ref_decision: str, ref_rationale: str) -> str:
    agg = tg_result.get("aggregate", {})
    elig = agg.get("eligible")
    label = "eligible" if elig is True else ("ineligible" if elig is False else "unknown")
    artifacts = {
        "inclusion_rows": (tg_result.get("inclusion") or {}).get("rows"),
        "exclusion_rows": (tg_result.get("exclusion") or {}).get("rows"),
        "aggregate": agg,
        "criteria_inclusion": (tg_result.get("inclusion") or {}).get("criteria"),
        "criteria_exclusion": (tg_result.get("exclusion") or {}).get("criteria"),
    }
    prompt = _fill(template, {
        "#PATIENT_NOTE#": patient_note,
        "#TRIAL_ELIGIBILITY_TEXT#": trial_text,
        "#SYSTEM_DECISION_LABEL#": label,
        "#CRITERION_DECISION_ARTIFACTS#": json.dumps(artifacts, indent=2, default=str)[:12000],
        "#NL_DECISION#": ref_decision,
        "#NL_RATIONALE#": ref_rationale,
    })
    raw = _call_with_retry(engine, prompt, temperature=0.0, tag="verb_tg")
    return _extract_rationale_text(raw)


def _extract_rationale_text(raw: str) -> str:
    """Verbalizer prompts emit a JSON object with a 'rationale' key. Fall back to raw text."""
    raw = raw.strip()
    # try to locate first {...} block
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            r = obj.get("rationale") or obj.get("rewrite_rationale")
            if isinstance(r, str) and r.strip():
                return r.strip()
        except Exception:
            pass
    return raw


# ── Destyle (all three at once) ───────────────────────────────────────────
def destyle_all(engine, template: str, *, patient_note: str, trial_text: str,
                systems: List[Tuple[str, str]]) -> Dict[str, str]:
    """systems is a list of (prompt_used, rationale_text) in a fixed order:
       [smt, llm_direct, trialgpt]. Returns {system_key: destyled_text}."""
    (p1, o1), (p2, o2), (p3, o3) = systems
    prompt = _fill(template, {
        "#PATIENT_NOTE#": patient_note,
        "#TRIAL_ELIGIBILITY_TEXT#": trial_text,
        "#PROMPT_USED_BY_SYSTEM_1#": p1,
        "#OUTPUT_SYSTEM_1#": o1,
        "#PROMPT_USED_BY_SYSTEM_2#": p2,
        "#OUTPUT_SYSTEM_2#": o2,
        "#PROMPT_USED_BY_SYSTEM_3#": p3,
        "#OUTPUT_SYSTEM_3#": o3,
    })
    raw = _call_with_retry(engine, prompt, temperature=0.0, tag="destyle")
    # Extract JSON block
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not m:
        return {"system 1": o1, "system 2": o2, "system 3": o3, "_raw": raw}
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return {"system 1": o1, "system 2": o2, "system 3": o3, "_raw": raw}
    out = {}
    for k in ("system 1", "system 2", "system 3"):
        v = obj.get(k) or {}
        if isinstance(v, dict):
            out[k] = v.get("rewrite_rationale") or ""
        else:
            out[k] = str(v)
    out["_raw"] = raw
    return out


# ── Pairwise judge ────────────────────────────────────────────────────────
def _label_from_eligible(e: Any) -> str:
    if e is True:
        return "eligible"
    if e is False:
        return "ineligible"
    return "unknown"


def run_contest(engine, template: str, *, patient_note: str, trial_text: str,
                rat_A: str, rat_B: str, dec_A: str, dec_B: str) -> Dict[str, Any]:
    prompt = _fill(template, {
        "#PATIENT_NOTE#": patient_note,
        "#TRIAL_ELIGIBILITY_TEXT#": trial_text,
        "#TRIAL_TEXT#": trial_text,                       # prescreen prompt variant
        "#CANDIDATE_A_DECISION#": dec_A,
        "#CANDIDATE_A_RATIONALE#": rat_A,
        "#CANDIDATE_B_DECISION#": dec_B,
        "#CANDIDATE_B_RATIONALE#": rat_B,
        "#DECISION_A#": dec_A, "#DECISION_B#": dec_B,     # prescreen prompt variants
        "#RATIONALE_A#": rat_A, "#RATIONALE_B#": rat_B,
    })
    raw = _call_with_retry(engine, prompt, temperature=0.0, tag="judge")
    # Parse <your_decision>{...}</your_decision>
    parsed: Dict[str, Any] = {"raw": raw}
    m = re.search(r"<your_decision>\s*(\{.*?\})\s*</your_decision>", raw, flags=re.DOTALL)
    js = m.group(1) if m else None
    if js is None:
        m2 = re.search(r"\{[^{}]*\"decision_over_decisions\"[^{}]*\}", raw, flags=re.DOTALL)
        js = m2.group(0) if m2 else None
    if js:
        try:
            # strip trailing commas
            js_clean = re.sub(r",\s*}", "}", js)
            parsed.update(json.loads(js_clean))
        except Exception as e:
            parsed["parse_error"] = str(e)
    return parsed


# ── Per-pair pipeline ─────────────────────────────────────────────────────
def process_pair(
    pid: str,
    tid: str,
    *,
    patient: Dict[str, Any],
    trial_obj: Dict[str, Any],
    engine_4: AzureInferenceEngine,
    engine_5: AzureInferenceEngine,
    smt_cfg: SMTConfig,
    out_dir: pathlib.Path,
    templates: Dict[str, str],
    seed: int,
) -> Dict[str, Any]:
    pair_dir = out_dir / f"{pid}__{tid}"
    pair_dir.mkdir(parents=True, exist_ok=True)
    patient_note = extract_patient_note_text(patient)
    trial_text = format_trial_description(trial_obj)

    # 1. Decisions
    smt_result = {}
    for side in ("inclusion", "exclusion"):
        smt_result[side] = run_match_for_side(side, tid, patient, smt_cfg, engine_4, None)
    inc_sat = smt_result["inclusion"].get("sat_like")
    exc_sat = smt_result["exclusion"].get("sat_like")
    # Lenient (prescreen-aligned): defer when either side is None. Matches TG's formula.
    smt_eligible = (inc_sat is not False) and (exc_sat is not False)
    (pair_dir / "smt_decision.json").write_text(
        json.dumps({"eligible": smt_eligible, "inclusion": smt_result["inclusion"],
                    "exclusion": smt_result["exclusion"]}, default=str, indent=2))

    work_root = pair_dir / "_work"
    work_root.mkdir(exist_ok=True)
    llm_res = run_llm_eligibility_judge(
        trial_id=tid, trial_obj=trial_obj, patient=patient, engine=engine_4,
        prompt_path=P_ELIG_EXPLICIT, out_root=work_root,
        model_name="gpt-4.1", temperature=0.0,
    )
    llm_eligible = (llm_res.get("result") or {}).get("eligible")
    llm_reasoning = (llm_res.get("result") or {}).get("reasoning") or \
                    (llm_res.get("result") or {}).get("explanation") or \
                    (llm_res.get("result") or {}).get("raw_text") or ""
    (pair_dir / "llm_direct_decision.json").write_text(json.dumps(llm_res, default=str, indent=2))

    tg_res = run_trialgpt_judge(
        trial_id=tid, trial_obj=trial_obj, patient=patient, engine=engine_4,
        out_root=work_root, model_name="gpt-4.1", temperature=0.0,
    )
    tg_eligible = (tg_res.get("aggregate") or {}).get("eligible")
    (pair_dir / "trialgpt_decision.json").write_text(json.dumps(tg_res, default=str, indent=2))

    # 2. Verbalize. Use llm_direct reasoning as the "reference assessment" for
    #    SMT and TrialGPT verbalizers (the prompts expect a reference).
    ref_decision = _label_from_eligible(llm_eligible)
    ref_rationale = llm_reasoning[:4000] if isinstance(llm_reasoning, str) else ""

    # Unified prescreen-aware verbalizer applied to ALL THREE systems symmetrically
    # to avoid effort-asymmetry confounding the judge comparison.
    smt_art = _summarize_smt_artifacts(smt_result)
    smt_source_rat = json.dumps({
        "inclusion_mined_values": _extract_mined_values_summary(smt_result.get("inclusion", {})),
        "exclusion_mined_values": _extract_mined_values_summary(smt_result.get("exclusion", {})),
        "status": smt_art.get("sides"),
    }, default=str)[:6000]
    smt_rationale = verbalize_unified(
        engine_4, templates["verb_unified"],
        patient_note=patient_note, trial_text=trial_text,
        decision_label=_label_from_eligible(smt_eligible),
        source_rationale=smt_source_rat,
        source_artifacts=json.dumps(smt_art, default=str)[:6000],
    )
    tg_source_rat = json.dumps({
        "inclusion_rows": (tg_res.get("inclusion") or {}).get("rows"),
        "exclusion_rows": (tg_res.get("exclusion") or {}).get("rows"),
        "aggregate": tg_res.get("aggregate"),
    }, default=str)[:8000]
    tg_rationale = verbalize_unified(
        engine_4, templates["verb_unified"],
        patient_note=patient_note, trial_text=trial_text,
        decision_label=_label_from_eligible(tg_eligible),
        source_rationale=tg_source_rat,
        source_artifacts="",
    )
    llm_rationale = verbalize_unified(
        engine_4, templates["verb_unified"],
        patient_note=patient_note, trial_text=trial_text,
        decision_label=_label_from_eligible(llm_eligible),
        source_rationale=(llm_reasoning or "").strip() or "(empty)",
        source_artifacts="",
    )

    (pair_dir / "smt_rationale.txt").write_text(smt_rationale)
    (pair_dir / "trialgpt_rationale.txt").write_text(tg_rationale)
    (pair_dir / "llm_direct_rationale.txt").write_text(llm_rationale)

    # 3. Destyle all three together
    destyled = destyle_all(
        engine_4, templates["destyle"],
        patient_note=patient_note, trial_text=trial_text,
        systems=[
            (templates["verb_smt"], smt_rationale),
            (P_ELIG_EXPLICIT.read_text(encoding="utf-8"), llm_rationale),
            (templates["verb_tg"], tg_rationale),
        ],
    )
    smt_de = destyled.get("system 1", smt_rationale) or smt_rationale
    llm_de = destyled.get("system 2", llm_rationale) or llm_rationale
    tg_de = destyled.get("system 3", tg_rationale) or tg_rationale
    (pair_dir / "smt_rationale_destyled.txt").write_text(smt_de)
    (pair_dir / "llm_direct_rationale_destyled.txt").write_text(llm_de)
    (pair_dir / "trialgpt_rationale_destyled.txt").write_text(tg_de)

    # 4. Pairwise contests with randomized A/B, using GPT-5.
    rng = random.Random(seed + hash((pid, tid)) % 10**6)
    systems = {
        "smt": {"rationale": smt_de, "decision": _label_from_eligible(smt_eligible)},
        "llm_direct": {"rationale": llm_de, "decision": _label_from_eligible(llm_eligible)},
        "trialgpt": {"rationale": tg_de, "decision": _label_from_eligible(tg_eligible)},
    }
    pairs_list = [("smt", "llm_direct"), ("smt", "trialgpt"), ("llm_direct", "trialgpt")]
    contests = []
    for s1, s2 in pairs_list:
        if rng.random() < 0.5:
            a_key, b_key = s1, s2
        else:
            a_key, b_key = s2, s1
        res = run_contest(
            engine_5, templates["judge"],
            patient_note=patient_note, trial_text=trial_text,
            rat_A=systems[a_key]["rationale"], rat_B=systems[b_key]["rationale"],
            dec_A=systems[a_key]["decision"], dec_B=systems[b_key]["decision"],
        )
        # unscramble
        dod = res.get("decision_over_decisions")
        if dod == "A_wins":
            winner = a_key
        elif dod == "B_wins":
            winner = b_key
        else:
            winner = dod  # tie/unknown codes preserved
        contests.append({
            "contest": f"{s1}_vs_{s2}",
            "A_system": a_key, "B_system": b_key,
            "judge_decision": dod,
            "winner": winner,
            "confidence": res.get("confidence"),
            "brief_rationale": res.get("brief_rationale"),
            "raw": res.get("raw"),
        })
    (pair_dir / "contests.json").write_text(json.dumps(contests, indent=2, default=str))

    return {
        "pair": f"{pid}__{tid}",
        "decisions": {
            "smt": _label_from_eligible(smt_eligible),
            "llm_direct": _label_from_eligible(llm_eligible),
            "trialgpt": _label_from_eligible(tg_eligible),
        },
        "contests": contests,
    }


# ── Aggregation ───────────────────────────────────────────────────────────
def aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    wins = {"smt": 0, "llm_direct": 0, "trialgpt": 0}
    ties = 0
    inconclusive = 0
    n_contests = 0
    per_matchup: Dict[str, Dict[str, int]] = {}
    for r in results:
        for c in r["contests"]:
            n_contests += 1
            key = c["contest"]
            per_matchup.setdefault(key, {"A_win": 0, "B_win": 0, "tie_or_other": 0,
                                          c["contest"].split("_vs_")[0]: 0,
                                          c["contest"].split("_vs_")[1]: 0})
            w = c["winner"]
            if w in wins:
                wins[w] += 1
                per_matchup[key][w] = per_matchup[key].get(w, 0) + 1
            elif w in ("both_correct_missing_info_policy_difference",
                       "both_correct_clinical_interpretation_difference",
                       "both_incorrect"):
                ties += 1
                per_matchup[key]["tie_or_other"] += 1
            else:
                inconclusive += 1
                per_matchup[key]["tie_or_other"] += 1
    total_non_tie = sum(wins.values())
    win_rate = {k: (v / total_non_tie if total_non_tie else 0.0) for k, v in wins.items()}
    return {
        "n_pairs": len(results),
        "n_contests": n_contests,
        "wins": wins,
        "ties": ties,
        "inconclusive": inconclusive,
        "win_rate_over_decisive": {k: round(v, 3) for k, v in win_rate.items()},
        "per_matchup": per_matchup,
    }


def print_table(agg: Dict[str, Any]) -> None:
    print("\n=== Explainability pilot results ===")
    print(f"pairs={agg['n_pairs']} contests={agg['n_contests']} "
          f"ties={agg['ties']} inconclusive={agg['inconclusive']}")
    print(f"{'system':<14}{'wins':>6}{'rate':>8}")
    for s in ("smt", "llm_direct", "trialgpt"):
        print(f"{s:<14}{agg['wins'][s]:>6}{agg['win_rate_over_decisive'][s]:>8.3f}")
    print("\nPer matchup:")
    for k, v in agg["per_matchup"].items():
        print(f"  {k}: {v}")


# ── Main ──────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs-file", default=None)
    ap.add_argument("--sample", type=int, default=10)
    ap.add_argument("--data-root", default="/tmp/satir_full_dataset")
    ap.add_argument("--build-root", default="build")
    ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    ap.add_argument("--out-dir", default=f"evaluation/results/verbalize_judge_{ts}")
    ap.add_argument("--max-workers", type=int, default=1)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--verb-unified-prompt", default=str(P_VERB_UNIFIED),
                    help="Path to unified verbalizer prompt (for A/B testing)")
    args = ap.parse_args()

    # 1. Resolve pairs
    if args.pairs_file:
        pairs: List[Tuple[str, str]] = []
        for line in pathlib.Path(args.pairs_file).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pid, tid = line.split(",", 1)
            pairs.append((pid.strip(), tid.strip()))
    else:
        pairs = sample_pairs_from_fliprate(args.sample, seed=args.seed)
    print(f"Processing {len(pairs)} pairs", file=sys.stderr)

    out_dir = pathlib.Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # 2. Load templates
    templates = {
        "verb_smt": P_VERB_SMT.read_text(encoding="utf-8"),
        "verb_tg": P_VERB_TG.read_text(encoding="utf-8"),
        "verb_unified": pathlib.Path(args.verb_unified_prompt).read_text(encoding="utf-8"),
        "destyle": P_DESTYLE.read_text(encoding="utf-8"),
        "judge": P_ADJUDICATE.read_text(encoding="utf-8"),
    }

    # 3. Load patients/trials
    data_root = pathlib.Path(args.data_root)
    trials: Dict[str, Any] = {}
    patients: Dict[str, Any] = {}
    for pid, tid in pairs:
        sub = _decide_corpus_dir(pid, data_root)
        if tid not in trials:
            trials[tid] = load_trial_from_corpus(tid, sub / "corpus.jsonl")
        if pid not in patients:
            patients[pid] = load_patient_from_queries(pid, sub / "queries.jsonl")
    valid = [(pid, tid) for pid, tid in pairs if trials.get(tid) and patients.get(pid)]
    missing = [(pid, tid) for pid, tid in pairs if not (trials.get(tid) and patients.get(pid))]
    if missing:
        print(f"WARN: skipping {len(missing)} pairs with missing data: {missing[:3]}...",
              file=sys.stderr)

    # 4. Build engines
    endpoint_4 = os.environ.get("OPENAI_ENDPOINT")
    endpoint_5 = os.environ.get("OPENAI_ENDPOINT_GPT5") or endpoint_4
    if not endpoint_4:
        print("FATAL: set OPENAI_ENDPOINT (source .env first)", file=sys.stderr)
        sys.exit(2)
    engine_4 = AzureInferenceEngine(
        endpoint=endpoint_4, api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-4.1", default_temperature=0.0,
    )
    engine_5 = AzureInferenceEngine5(
        endpoint=endpoint_5, api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-5", default_temperature=0.0,
    )

    smt_cfg = SMTConfig(
        data_root=data_root,
        build_root=pathlib.Path(args.build_root),
        prompt_root=ROOT / "smt_matcher" / "prompts" / "clinical_trial",
    )

    # 5. Process pairs
    t0 = time.perf_counter()
    results: List[Dict[str, Any]] = []

    def _do(pid_tid: Tuple[str, str]) -> Optional[Dict[str, Any]]:
        pid, tid = pid_tid
        try:
            return process_pair(
                pid, tid,
                patient=patients[pid], trial_obj=trials[tid],
                engine_4=engine_4, engine_5=engine_5,
                smt_cfg=smt_cfg, out_dir=out_dir, templates=templates,
                seed=args.seed,
            )
        except Exception as e:
            print(f"  FAIL {pid}__{tid}: {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return None

    if args.max_workers <= 1:
        for i, pt in enumerate(valid, 1):
            r = _do(pt)
            if r:
                results.append(r)
            dt_s = time.perf_counter() - t0
            print(f"  {i}/{len(valid)} pairs done ({dt_s:.1f}s)", file=sys.stderr)
    else:
        with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
            futs = {ex.submit(_do, pt): pt for pt in valid}
            for i, fut in enumerate(as_completed(futs), 1):
                r = fut.result()
                if r:
                    results.append(r)
                dt_s = time.perf_counter() - t0
                print(f"  {i}/{len(valid)} pairs done ({dt_s:.1f}s)", file=sys.stderr)

    # 6. Aggregate
    agg = aggregate(results)
    out_file = out_dir / "judge_results.json"
    out_file.write_text(json.dumps({
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "aggregate": agg,
        "per_pair": results,
    }, indent=2, default=str))
    print_table(agg)
    print(f"\nSaved to {out_file}")


if __name__ == "__main__":
    main()
