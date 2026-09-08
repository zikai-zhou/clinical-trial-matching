"""
TrialGPT sentence-level criterion judge.

A parallel decision baseline that evaluates each inclusion/exclusion
criterion one-by-one, producing per-criterion labels in the space:
    inclusion: {"not applicable", "not enough information", "included", "not included"}
    exclusion: {"not applicable", "not enough information", "excluded", "not excluded"}

Follows the TrialGPT methodology: patient note is sentence-tokenized and
numbered; the LLM is asked to link criterion evaluations to sentence IDs.

Extracted from cmsrc/match_patient_to_trial.py (_trialgpt_* functions +
run_trialgpt_criterion_judge).
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from smt_matcher.cache import (
    CACHE_SCHEMA_VERSION,
    cache_file_for,
    cache_payload_matches,
    safe_read_json,
    safe_write_json_atomic,
    stable_hash,
)
from smt_matcher.judges.llm_judge import (
    _call_engine_text,
    extract_patient_note_text,
    parent_nct_id,
)


# ── Patient sentence tokenization ────────────────────────────────────────

def _sent_tokenize_best_effort(text: str) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []
    try:
        from nltk.tokenize import sent_tokenize as _st  # type: ignore
        sents = _st(t)
        return [s.strip() for s in sents if isinstance(s, str) and s.strip()]
    except Exception:
        parts = re.split(r"(?<=[.!?])\s+", t)
        return [p.strip() for p in parts if p.strip()]


def patient_with_sentence_ids(patient: Dict[str, Any]) -> Dict[str, Any]:
    note = extract_patient_note_text(patient)
    sents = _sent_tokenize_best_effort(note)
    sents.append(
        "The patient will provide informed consent, and will comply with the trial protocol without any practical issues."
    )
    sents = [s.strip() for s in sents if s.strip()]
    numbered = [f"{i}. {s}" for i, s in enumerate(sents)]
    return {"patient_note_sentenced": "\n".join(numbered), "sentences": sents}


# ── Trial rendering ──────────────────────────────────────────────────────

def _maybe_parse_list(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        try:
            obj = json.loads(v)
            if isinstance(obj, list):
                return [str(x) for x in obj]
        except Exception:
            pass
        return [v] if v.strip() else []
    return []


def _parse_criteria_list(criteria: Any) -> List[str]:
    if not isinstance(criteria, str):
        return []
    out: List[str] = []
    for b in criteria.split("\n\n"):
        c = (b or "").strip()
        if len(c) < 5:
            continue
        low = c.lower()
        if "inclusion criteria" in low or "exclusion criteria" in low:
            continue
        out.append(c)
    return out


def _render_trial(trial_obj: Dict[str, Any], inc_exc: str) -> Tuple[str, List[str]]:
    md = trial_obj.get("metadata") or {}
    title = (trial_obj.get("brief_title") or md.get("brief_title")
             or trial_obj.get("title") or "")
    summary = (trial_obj.get("brief_summary") or md.get("brief_summary")
               or trial_obj.get("description") or "")
    diseases_list = _maybe_parse_list(trial_obj.get("diseases_list") or md.get("diseases_list") or [])
    drugs_list = _maybe_parse_list(trial_obj.get("drugs_list") or md.get("drugs_list") or [])

    inc_text = trial_obj.get("inclusion_criteria") or md.get("inclusion_criteria") or ""
    exc_text = trial_obj.get("exclusion_criteria") or md.get("exclusion_criteria") or ""

    criteria_list = _parse_criteria_list(inc_text if inc_exc == "inclusion" else exc_text)
    criteria_block = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria_list))

    trial = (
        f"Title: {title}\n"
        f"Target diseases: {', '.join(diseases_list) if diseases_list else ''}\n"
        f"Interventions: {', '.join(drugs_list) if drugs_list else ''}\n"
        f"Summary: {summary}\n"
    )
    if inc_exc == "inclusion":
        trial += f"Inclusion criteria:\n {criteria_block}\n"
    else:
        trial += f"Exclusion criteria:\n {criteria_block}\n"

    return trial, criteria_list


def _build_prompts(trial_obj: Dict[str, Any], inc_exc: str, patient_sentenced: str) -> Tuple[str, str, List[str]]:
    trial_str, criteria_list = _render_trial(trial_obj, inc_exc)

    system_prompt = (
        "You are a helpful assistant for clinical trial recruitment. "
        f"Your task is to compare a given patient note and the {inc_exc} criteria of a clinical trial "
        "to determine the patient's eligibility at the criterion level.\n"
    )

    if inc_exc == "inclusion":
        system_prompt += (
            "The factors that allow someone to participate in a clinical study are called inclusion criteria. "
            "They are based on characteristics such as age, gender, the type and stage of a disease, previous treatment history, and other medical conditions.\n"
        )
    else:
        system_prompt += (
            "The factors that disqualify someone from participating are called exclusion criteria. "
            "They are based on characteristics such as age, gender, the type and stage of a disease, previous treatment history, and other medical conditions.\n"
        )

    system_prompt += (
        f"You should check the {inc_exc} criteria one-by-one, and output the following three elements for each criterion:\n"
        f"\tElement 1. For each {inc_exc} criterion, briefly generate your reasoning process: First, judge whether the criterion is not applicable (not very common), where the patient does not meet the premise of the criterion. Then, check if the patient note contains direct evidence. If so, judge whether the patient meets or does not meet the criterion. If there is no direct evidence, try to infer from existing evidence, and answer one question: If the criterion is true, is it possible that a good patient note will miss such information? If impossible, then you can assume that the criterion is not true. Otherwise, there is not enough information.\n"
        "\tElement 2. If there is relevant information, you must generate a list of relevant sentence IDs in the patient note. If there is no relevant information, you must annotate an empty list.\n"
        f"\tElement 3. Classify the patient eligibility for this specific {inc_exc} criterion: "
    )

    if inc_exc == "inclusion":
        system_prompt += (
            'the label must be chosen from {"not applicable", "not enough information", "included", "not included"}. '
            '"not applicable" should only be used for criteria that are not applicable to the patient. '
            '"not enough information" should be used where the patient note does not contain sufficient information for making the classification. '
            'Try to use as less "not enough information" as possible because if the note does not mention a medically important fact, you can assume that the fact is not true for the patient. '
            '"included" denotes that the patient meets the inclusion criterion, while "not included" means the reverse.\n'
        )
    else:
        system_prompt += (
            'the label must be chosen from {"not applicable", "not enough information", "excluded", "not excluded"}. '
            '"not applicable" should only be used for criteria that are not applicable to the patient. '
            '"not enough information" should be used where the patient note does not contain sufficient information for making the classification. '
            'Try to use as less "not enough information" as possible because if the note does not mention a medically important fact, you can assume that the fact is not true for the patient. '
            '"excluded" denotes that the patient meets the exclusion criterion and should be excluded in the trial, while "not excluded" means the reverse.\n'
        )

    system_prompt += (
        "You should output only a JSON dict exactly formatted as: "
        "dict{str(criterion_number): list[str(element_1_brief_reasoning), list[int(element_2_sentence_id)], str(element_3_eligibility_label)]}."
    )

    user_prompt = (
        "Here is the patient note, each sentence is led by a sentence_id:\n"
        f"{patient_sentenced}\n\n"
        f"Here is the clinical trial:\n{trial_str}\n\n"
        "Plain JSON output:"
    )

    return system_prompt, user_prompt, criteria_list


# ── Output parsing ───────────────────────────────────────────────────────

def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _extract_first_json_object(text: str) -> Optional[str]:
    t = _strip_code_fences(text)
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return t[start:i + 1]
    return None


def _parse_output(raw_text: str, *, n_criteria: int, inc_exc: str) -> Dict[str, Any]:
    js = _extract_first_json_object(raw_text or "")
    parsed: Dict[str, Any] = {}
    parse_error: Optional[str] = None

    if js is None:
        parse_error = "no_json_found"
    else:
        try:
            obj = json.loads(js)
            if isinstance(obj, dict):
                parsed = obj
            else:
                parse_error = "json_not_dict"
        except Exception as e:
            parse_error = f"json_parse_error: {e}"

    rows: List[Dict[str, Any]] = []
    allowed_inc = {"not applicable", "not enough information", "included", "not included"}
    allowed_exc = {"not applicable", "not enough information", "excluded", "not excluded"}
    allowed = allowed_inc if inc_exc == "inclusion" else allowed_exc

    for i in range(n_criteria):
        v = parsed.get(str(i))
        reasoning = None
        sent_ids: List[int] = []
        label = "not enough information"

        if isinstance(v, list) and len(v) >= 3:
            if isinstance(v[0], str):
                reasoning = v[0]
            if isinstance(v[1], list):
                for x in v[1]:
                    try:
                        sent_ids.append(int(x))
                    except Exception:
                        continue
            if isinstance(v[2], str):
                cand = v[2].strip().lower()
                if cand in allowed:
                    label = cand

        rows.append({"criterion_id": i, "reasoning": reasoning, "sentence_ids": sent_ids, "label": label})

    return {"parse_error": parse_error, "raw_text": raw_text, "rows": rows}


def _side_sat_like(side_rows: List[Dict[str, Any]], inc_exc: str) -> Tuple[Optional[bool], Dict[str, Any]]:
    labels = [r.get("label") for r in side_rows]
    unknown: List[int] = []

    if inc_exc == "inclusion":
        violated: List[int] = []
        for r in side_rows:
            if r.get("label") == "not included":
                violated.append(int(r.get("criterion_id", -1)))
            elif r.get("label") == "not enough information":
                unknown.append(int(r.get("criterion_id", -1)))
        if violated:
            return False, {"violated": violated, "unknown": unknown}
        if labels and all(l in ("included", "not applicable") for l in labels):
            return True, {"violated": violated, "unknown": unknown}
        return None, {"violated": violated, "unknown": unknown}

    triggered: List[int] = []
    for r in side_rows:
        if r.get("label") == "excluded":
            triggered.append(int(r.get("criterion_id", -1)))
        elif r.get("label") == "not enough information":
            unknown.append(int(r.get("criterion_id", -1)))

    if triggered:
        return False, {"triggered": triggered, "unknown": unknown}
    if labels and all(l in ("not excluded", "not applicable") for l in labels):
        return True, {"triggered": triggered, "unknown": unknown}
    return None, {"triggered": triggered, "unknown": unknown}


def _call_engine_chat(engine: Any, system_prompt: str, user_prompt: str, *, temperature: Optional[float] = None) -> str:
    """Call engine with system+user message. Falls back to flat prompt."""
    try:
        from azure.ai.inference.models import SystemMessage, UserMessage  # type: ignore
        if hasattr(engine, "run") and callable(getattr(engine, "run")):
            msgs = [SystemMessage(content=system_prompt), UserMessage(content=user_prompt)]
            if temperature is None:
                return engine.run(msgs)
            return engine.run(msgs, temperature=float(temperature))
    except Exception:
        pass

    if callable(engine):
        try:
            if temperature is None:
                out = engine(user_prompt, system_message=system_prompt)
            else:
                out = engine(user_prompt, system_message=system_prompt, temperature=float(temperature))
            if isinstance(out, list) and out and isinstance(out[0], str):
                return out[0]
            if isinstance(out, str):
                return out
        except Exception:
            pass

    flat = f"{system_prompt}\n\n{user_prompt}"
    return _call_engine_text(engine, flat, temperature=temperature)


# ── Main judge entry point ───────────────────────────────────────────────

def _side_fingerprint(
    *,
    inc_exc: str,
    system_prompt: str,
    user_prompt: str,
    criteria_list: List[str],
    model_name: str,
    temperature: Optional[float],
    parent_id: str,
    patient_id: str,
) -> str:
    return stable_hash({
        "schema_version": CACHE_SCHEMA_VERSION,
        "stage": f"trialgpt_{inc_exc}",
        "inc_exc": inc_exc,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "criteria_list": criteria_list,
        "model_name": model_name,
        "temperature": temperature,
        "trial_id_parent": parent_id,
        "patient_id": patient_id,
    })


def run_trialgpt_judge(
    trial_id: str,
    trial_obj: Dict[str, Any],
    patient: Dict[str, Any],
    engine: Any,
    *,
    out_root: pathlib.Path,
    model_name: str = "gpt-4.1",
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Run TrialGPT sentence-level judge on a (trial, patient) pair.

    Evaluates each inclusion and exclusion criterion individually, linking
    to relevant patient sentence IDs. Aggregates into SAT-like booleans.

    Args:
        trial_id:    Trial identifier
        trial_obj:   Dict with brief_title/brief_summary/diseases_list/drugs_list/
                     inclusion_criteria/exclusion_criteria
        patient:     Dict with text/note
        engine:      AzureInferenceEngine-like
        out_root:    Cache root (creates <out_root>/_prompt_cache/trialgpt_*)
        model_name:  For fingerprinting
        temperature: Optional sampling temperature

    Returns:
        Dict with inclusion/exclusion payloads and an aggregate block
        containing inclusion_sat_like, exclusion_sat_like, eligible, eligible_strict.
    """
    parent_id = parent_nct_id(trial_id)
    patient_id = str(patient.get("patient_id") or patient.get("_id") or patient.get("id") or "UNKNOWN")

    patient_pkg = patient_with_sentence_ids(patient)
    patient_sentenced = patient_pkg["patient_note_sentenced"]

    sides_out: Dict[str, Any] = {}
    side_meta: Dict[str, Any] = {}

    for inc_exc in ("inclusion", "exclusion"):
        sys_p, usr_p, criteria_list = _build_prompts(trial_obj, inc_exc, patient_sentenced)
        fingerprint = _side_fingerprint(
            inc_exc=inc_exc,
            system_prompt=sys_p, user_prompt=usr_p,
            criteria_list=criteria_list, model_name=model_name,
            temperature=temperature, parent_id=parent_id, patient_id=patient_id,
        )

        cache_path = cache_file_for(out_root, f"trialgpt_{inc_exc}", fingerprint)
        side_payload = safe_read_json(cache_path)

        if cache_payload_matches(side_payload, fingerprint):
            payload = side_payload.get("payload")
            if isinstance(payload, dict):
                sides_out[inc_exc] = payload
                side_meta[inc_exc] = {"fingerprint": fingerprint, "used_cache": True}
                continue

        t0 = time.perf_counter()
        raw = _call_engine_chat(engine, sys_p, usr_p, temperature=temperature)
        dur = time.perf_counter() - t0

        parsed = _parse_output(raw, n_criteria=len(criteria_list), inc_exc=inc_exc)
        payload = {
            "cache": {
                "schema_version": CACHE_SCHEMA_VERSION,
                "stage": f"trialgpt_{inc_exc}",
                "fingerprint": fingerprint,
                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                "model_name": model_name,
                "temperature": temperature,
            },
            "inc_exc": inc_exc,
            "criteria": criteria_list,
            "duration_s": dur,
            "temperature_used": temperature,
            "parse_error": parsed.get("parse_error"),
            "rows": parsed.get("rows"),
            "raw_text": parsed.get("raw_text"),
        }
        safe_write_json_atomic(cache_path, {"cache": payload["cache"], "payload": payload})
        sides_out[inc_exc] = payload
        side_meta[inc_exc] = {"fingerprint": fingerprint, "used_cache": False}

    inc_sat, inc_detail = _side_sat_like(sides_out["inclusion"]["rows"], "inclusion")
    exc_sat, exc_detail = _side_sat_like(sides_out["exclusion"]["rows"], "exclusion")

    eligible_strict = None if (inc_sat is None or exc_sat is None) else (bool(inc_sat) and bool(exc_sat))
    eligible = (inc_sat is not False) and (exc_sat is not False)

    return {
        "trial_id_original": trial_id,
        "trial_id_parent": parent_id,
        "patient_id": patient_id,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "patient_sentences": patient_pkg.get("sentences"),
        "inclusion": sides_out.get("inclusion"),
        "exclusion": sides_out.get("exclusion"),
        "aggregate": {
            "inclusion_sat_like": inc_sat,
            "exclusion_sat_like": exc_sat,
            "eligible": eligible,
            "eligible_strict": eligible_strict,
            "inclusion_detail": inc_detail,
            "exclusion_detail": exc_detail,
        },
        "side_meta": side_meta,
    }
