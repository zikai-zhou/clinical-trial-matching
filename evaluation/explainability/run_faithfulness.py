#!/usr/bin/env python3
"""
Rationale-reproducibility / faithfulness evaluation.

For each pair in a verbalize_judge output directory, for each system:
  1. Ask GPT-4.1 which patient-note sentences the rationale relies on.
  2. Build a MINIMAL patient note = just those sentences.
  3. Re-run the system on the minimal note.
  4. faithful = 1 iff re-run decision == original decision.
Also checks SMT for verbalizer hallucination (REQ labels cited in the rationale
that were not in the original unsat core).

Usage:
  set -a; source .env; set +a
  python -m evaluation.explainability.run_faithfulness \\
      --in-dir evaluation/results/verbalize_judge_unified \\
      --sample 5 --data-root /tmp/satir_full_dataset --build-root build
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine import AzureInferenceEngine  # noqa: E402
from smt_matcher.match_patient_to_trial import (  # noqa: E402
    Config as SMTConfig,
    run_match_for_side,
)
from smt_matcher.judges.llm_judge import (  # noqa: E402
    run_llm_eligibility_judge,
    _call_engine_text,
    extract_patient_note_text,
)
from smt_matcher.judges.trialgpt_judge import run_trialgpt_judge  # noqa: E402

# Reuse helpers from the verbalize_judge script to avoid drift.
from evaluation.explainability import run_verbalize_judge as rvj  # noqa: E402

P_ELIG_EXPLICIT = ROOT / "smt_matcher" / "prompts" / "clinical_trial" / "SMTMatcher" / "eligibility.explicit.prompt"
P_EXTRACT_CITED = ROOT / "sql_retrieval" / "meval" / "prompts" / "extract_cited_sentences.prompt"

SYSTEMS_ALL = ("smt", "llm_direct", "trialgpt")


# ── Sentence tokenization ─────────────────────────────────────────────────
def _sent_tokenize(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        import nltk  # type: ignore
        try:
            return [s.strip() for s in nltk.sent_tokenize(text) if s.strip()]
        except LookupError:
            try:
                nltk.download("punkt", quiet=True)
                nltk.download("punkt_tab", quiet=True)
                return [s.strip() for s in nltk.sent_tokenize(text) if s.strip()]
            except Exception:
                pass
    except Exception:
        pass
    # Fallback: split on . ! ? followed by whitespace
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _number_sentences(sents: List[str]) -> str:
    return "\n".join(f"[{i}] {s}" for i, s in enumerate(sents))


# ── Extraction call ───────────────────────────────────────────────────────
def _call_with_retry(engine, prompt: str, *, tag: str = "") -> str:
    try:
        return _call_engine_text(engine, prompt, temperature=0.0)
    except Exception as e1:
        print(f"  [retry {tag}] {type(e1).__name__}: {e1}", file=sys.stderr)
        time.sleep(2.0)
        return _call_engine_text(engine, prompt, temperature=0.0)


def extract_cited_indices(engine, template: str, *, patient_sents: List[str],
                          rationale: str) -> Tuple[List[int], bool]:
    """Returns (indices, fallback). fallback=True if extraction failed/empty."""
    if not rationale.strip() or not patient_sents:
        return [], True
    prompt = template.replace("#PATIENT_NOTE_NUMBERED#", _number_sentences(patient_sents))
    prompt = prompt.replace("#RATIONALE#", rationale[:12000])
    try:
        raw = _call_with_retry(engine, prompt, tag="extract")
    except Exception as e:
        print(f"  extract_cited failed: {type(e).__name__}: {e}", file=sys.stderr)
        return [], True
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not m:
        return [], True
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return [], True
    ids = obj.get("indices")
    if not isinstance(ids, list):
        return [], True
    clean: List[int] = []
    for x in ids:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(patient_sents) and i not in clean:
            clean.append(i)
    if not clean:
        return [], True
    return sorted(clean), False


def build_minimal_note(patient_sents: List[str], indices: List[int],
                        full_note: str, fallback: bool) -> str:
    if fallback or not indices:
        return full_note
    return " ".join(patient_sents[i] for i in sorted(indices))


# ── Original decision readers ─────────────────────────────────────────────
def _label(e: Any) -> str:
    if e is True:
        return "eligible"
    if e is False:
        return "ineligible"
    return "unknown"


def _orig_decision(pair_dir: pathlib.Path, system: str) -> str:
    f = pair_dir / f"{system}_decision.json"
    if not f.exists():
        return "unknown"
    try:
        d = json.loads(f.read_text())
    except Exception:
        return "unknown"
    if system == "trialgpt":
        agg = d.get("aggregate") or {}
        return _label(agg.get("eligible"))
    if system == "llm_direct":
        res = d.get("result") or {}
        return _label(res.get("eligible"))
    # smt: either top-level 'eligible' or derive from inclusion/exclusion sat_like
    if "eligible" in d:
        return _label(d.get("eligible"))
    inc = (d.get("inclusion") or {}).get("sat_like")
    exc = (d.get("exclusion") or {}).get("sat_like")
    if inc is None or exc is None:
        return "unknown"
    # Lenient (prescreen): defer when either side is None/unknown.
    return _label((inc is not False) and (exc is not False))


# ── Rerun per system ──────────────────────────────────────────────────────
def rerun_smt(*, pid: str, tid: str, minimal_note: str, trial_obj: Dict[str, Any],
              engine, smt_cfg: SMTConfig, work_dir: pathlib.Path) -> str:
    patient = {"_id": pid, "patient_id": pid, "text": minimal_note, "metadata": {}}
    # run_match_for_side takes (side, trial_id, patient, cfg, engine, out_root)
    work_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for side in ("inclusion", "exclusion"):
        results[side] = run_match_for_side(side, tid, patient, smt_cfg, engine, None)
    inc = results["inclusion"].get("sat_like")
    exc = results["exclusion"].get("sat_like")
    if inc is None or exc is None:
        return "unknown"
    # Lenient (prescreen): defer when either side is None/unknown.
    return _label((inc is not False) and (exc is not False))


def rerun_llm_direct(*, pid: str, tid: str, minimal_note: str, trial_obj: Dict[str, Any],
                     engine, work_dir: pathlib.Path) -> str:
    patient = {"_id": pid, "patient_id": pid, "text": minimal_note, "metadata": {}}
    work_dir.mkdir(parents=True, exist_ok=True)
    res = run_llm_eligibility_judge(
        trial_id=tid, trial_obj=trial_obj, patient=patient, engine=engine,
        prompt_path=P_ELIG_EXPLICIT, out_root=work_dir,
        model_name="gpt-4.1", temperature=0.0,
    )
    return _label((res.get("result") or {}).get("eligible"))


def rerun_trialgpt(*, pid: str, tid: str, minimal_note: str, trial_obj: Dict[str, Any],
                   engine, work_dir: pathlib.Path) -> str:
    patient = {"_id": pid, "patient_id": pid, "text": minimal_note, "metadata": {}}
    work_dir.mkdir(parents=True, exist_ok=True)
    res = run_trialgpt_judge(
        trial_id=tid, trial_obj=trial_obj, patient=patient, engine=engine,
        out_root=work_dir, model_name="gpt-4.1", temperature=0.0,
    )
    return _label((res.get("aggregate") or {}).get("eligible"))


# ── SMT structural rerun (empirical verification of Theorem 1) ────────────
def _smt_literal(sort: str, value: Any) -> Optional[str]:
    """Replicate SMTProgramEvaluator._literal for building value assertions."""
    if value is None:
        return None
    # Miner occasionally emits the string "null"/"None" instead of JSON null.
    # Treat these as missing and skip the assertion.
    if isinstance(value, str) and value.strip().lower() in {"null", "none", "nan", ""}:
        return None
    if isinstance(value, dict):
        return _smt_literal(sort, value.get("value"))
    s = (sort or "").lower()
    if s == "bool":
        return "true" if str(value).lower() in {"true", "1", "t", "yes"} else "false"
    if s == "string":
        return f'"{value}"'
    if s in {"int", "integer", "real"}:
        return str(value)
    # Fallback: still coerce bool-ish Python values to lowercase SMT-LIB.
    if isinstance(value, bool):
        return "true" if value else "false"
    if str(value) in ("True", "False"):
        return str(value).lower()
    return str(value)


def _rerun_side_structural(side_raw: Dict[str, Any]) -> Dict[str, Any]:
    """Run Z3 on just the unsat-core assertions + mined variable values.
    Returns {status, original_status, matched, restricted_count, asserted_values}.
    If original side was SAT, re-running is trivial (any subset of a SAT system
    is SAT); we report matched=True by definition and skip the Z3 run."""
    try:
        import z3
    except Exception as e:
        return {"error": f"z3 import failed: {e}"}

    smt_lines: List[str] = side_raw.get("smt_program_lines") or []
    if not smt_lines:
        return {"error": "no smt_program_lines"}

    er = side_raw.get("eval_result") or {}
    original_status = er.get("status") or "unknown"
    var_index: Dict[str, Any] = side_raw.get("variable_index") or {}
    pvv: Dict[str, Any] = side_raw.get("patient_var_values") or {}
    core_labels = set((er.get("label_status") or {}).get("unsat") or [])

    if original_status == "sat":
        # Any subset of a satisfiable program is satisfiable — trivially faithful.
        return {
            "original_status": "sat",
            "rerun_status": "sat",
            "matched": True,
            "trivial": True,
            "restricted_assertions": 0,
            "asserted_values": 0,
        }

    if original_status != "unsat":
        return {
            "original_status": original_status,
            "rerun_status": "skipped_not_unsat",
            "matched": None,
            "trivial": False,
        }

    # Build minimal program: keep all declare-* lines, plus only labeled
    # assertions whose label is in unsat_core.
    full_text = "\n".join(smt_lines)

    def _parse_and_filter(src: str) -> Tuple[str, int]:
        """Return (minimal SMT-LIB text, num_kept_assertions). Uses a simple
        paren-balance walker because :named asserts can span lines."""
        out_lines: List[str] = []
        i = 0
        n = len(src)
        kept = 0
        while i < n:
            # skip whitespace + comments
            while i < n and src[i] in " \t\r\n":
                out_lines.append(src[i]); i += 1
                continue
            if i >= n:
                break
            if src[i] == ";":
                # comment to end of line — keep it
                j = src.find("\n", i)
                j = n if j == -1 else j + 1
                out_lines.append(src[i:j]); i = j
                continue
            if src[i] != "(":
                # stray token; keep and advance
                out_lines.append(src[i]); i += 1
                continue
            # balanced s-expr
            depth = 0
            start = i
            while i < n:
                c = src[i]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            sexpr = src[start:i]
            head = sexpr.lstrip("(").split(None, 1)[0] if sexpr.lstrip("(") else ""
            if head == "assert":
                # Check if it contains a :named label we want to keep
                m = re.search(r":named\s+([A-Za-z_][\w]*)", sexpr)
                if m:
                    label = m.group(1)
                    if label in core_labels:
                        out_lines.append(sexpr)
                        kept += 1
                    # else drop entirely (this is the restriction)
                else:
                    # Unlabeled asserts (e.g., auxiliary/declarations of datatypes)
                    # keep them to preserve type correctness.
                    out_lines.append(sexpr)
            else:
                # set-logic, declare-const, declare-datatype, define-fun, etc.
                out_lines.append(sexpr)
        return "".join(out_lines), kept

    minimal_text, kept = _parse_and_filter(full_text)

    # Build value assertions from patient_var_values for variables we care about.
    value_asserts: List[str] = []
    asserted_count = 0
    for var_name, meta in pvv.items():
        raw_val = meta.get("value") if isinstance(meta, dict) else meta
        if raw_val is None:
            continue  # prescreen deferral — skip
        vinfo = var_index.get(var_name) or {}
        lit = _smt_literal(vinfo.get("type", ""), raw_val)
        if lit is None:
            continue
        value_asserts.append(f"(assert (= {var_name} {lit})) ; cited mined value")
        asserted_count += 1

    program = minimal_text + "\n" + "\n".join(value_asserts) + "\n(check-sat)\n"

    try:
        ctx = z3.Context()
        s = z3.Solver(ctx=ctx)
        s.from_string(program)
        r = s.check()
        rerun_status = ("sat" if r == z3.sat
                        else "unsat" if r == z3.unsat
                        else "unknown")
    except Exception as e:
        return {
            "original_status": original_status,
            "rerun_status": f"z3_error: {type(e).__name__}: {e}",
            "matched": False,
            "restricted_assertions": kept,
            "asserted_values": asserted_count,
        }

    return {
        "original_status": original_status,
        "rerun_status": rerun_status,
        "matched": rerun_status == original_status,
        "trivial": False,
        "restricted_assertions": kept,
        "asserted_values": asserted_count,
    }


def smt_structural_faithfulness(pair_dir: pathlib.Path) -> Dict[str, Any]:
    """Run the structural faithfulness test on both inclusion and exclusion sides.
    Returns per-side results + pair-level 'faithful' bit."""
    dec_path = pair_dir / "smt_decision.json"
    if not dec_path.exists():
        return {"error": "no smt_decision.json"}
    try:
        d = json.loads(dec_path.read_text())
    except Exception as e:
        return {"error": f"json parse: {e}"}

    out = {"per_side": {}}
    all_matched = True
    for side in ("inclusion", "exclusion"):
        raw = ((d.get(side) or {}).get("raw")) or {}
        r = _rerun_side_structural(raw)
        out["per_side"][side] = r
        if r.get("matched") is False:
            all_matched = False
    out["faithful"] = all_matched
    return out


# ── SMT hallucinated-REQ check ────────────────────────────────────────────
REQ_RE = re.compile(r"\bREQ\d+(?:_[A-Z0-9]+)*\b")


def smt_original_reqs(pair_dir: pathlib.Path) -> Tuple[set, set]:
    """Returns (unsat_core_req_names, all_known_req_names)."""
    f = pair_dir / "smt_decision.json"
    unsat: set = set()
    known: set = set()
    if not f.exists():
        return unsat, known
    try:
        d = json.loads(f.read_text())
    except Exception:
        return unsat, known

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("unsat_core", "unsat_core_names", "unsat") and isinstance(v, list):
                    for x in v:
                        if isinstance(x, str):
                            unsat.add(x)
                elif k == "label_status" and isinstance(v, dict):
                    for grp in ("sat", "unsat", "unknown"):
                        for x in v.get(grp) or []:
                            if isinstance(x, str):
                                known.add(x)
                                if grp == "unsat":
                                    unsat.add(x)
                else:
                    _walk(v)
        elif isinstance(obj, list):
            for x in obj:
                _walk(x)

    _walk(d)
    return unsat, known


def smt_hallucinated_reqs(rationale_text: str, unsat_core: set, known: set) -> List[str]:
    cited = set(REQ_RE.findall(rationale_text or ""))
    # A "hallucination" is a cited REQ that is NOT in the original unsat core.
    # If we have a known-set for this trial, also filter to REQs that at least
    # look like they belong to this trial family. Otherwise just compare to unsat.
    hall = [r for r in sorted(cited) if r not in unsat_core]
    return hall


# ── Per-pair driver ───────────────────────────────────────────────────────
def process_pair(
    pair_dir: pathlib.Path,
    *,
    out_pair_dir: pathlib.Path,
    data_root: pathlib.Path,
    smt_cfg: SMTConfig,
    engine_4: AzureInferenceEngine,
    extract_template: str,
    systems: Tuple[str, ...],
) -> Optional[Dict[str, Any]]:
    out_pair_dir.mkdir(parents=True, exist_ok=True)
    pair_name = pair_dir.name
    pid, tid = pair_name.split("__", 1)

    # Load original patient + trial from dataset
    sub = rvj._decide_corpus_dir(pid, data_root)
    patient = rvj.load_patient_from_queries(pid, sub / "queries.jsonl")
    trial_obj = rvj.load_trial_from_corpus(tid, sub / "corpus.jsonl")
    if not patient or not trial_obj:
        return {"pair": pair_name, "errors": ["missing patient or trial"]}
    full_note = extract_patient_note_text(patient)
    patient_sents = _sent_tokenize(full_note)

    errors: List[str] = []
    original_decisions: Dict[str, str] = {}
    cited: Dict[str, List[int]] = {}
    minimal_notes: Dict[str, str] = {}
    fallbacks: Dict[str, bool] = {}
    rerun_decisions: Dict[str, str] = {}
    faithful: Dict[str, int] = {}
    smt_hall: List[str] = []

    faith_work = out_pair_dir / "faith_work"

    for sys_key in systems:
        try:
            rat_path = pair_dir / f"{sys_key}_rationale.txt"
            destyled_path = pair_dir / f"{sys_key}_rationale_destyled.txt"
            if rat_path.exists():
                rationale = rat_path.read_text(encoding="utf-8")
            elif destyled_path.exists():
                rationale = destyled_path.read_text(encoding="utf-8")
            else:
                errors.append(f"{sys_key}: no rationale file")
                continue

            original_decisions[sys_key] = _orig_decision(pair_dir, sys_key)

            if sys_key == "smt":
                # SMT faithfulness has THREE components, all reported:
                #   (1) Structural sufficiency: re-run Z3 on the cited unsat-core
                #       assertions + mined variable values. By minimality of the
                #       unsat core this should match the original decision.
                #   (2) Verbalizer hallucination: did the verbalizer cite REQ
                #       labels not in the original unsat core? (regex check)
                #   (3) Sentence-citation sufficiency (NEW, symmetric with LLM/TG):
                #       extract patient-note sentences the rationale cites, rerun
                #       the FULL SMT pipeline (miner + solver) on the stripped
                #       note, check whether the decision is reproduced.
                #
                # The per-system "faithful" bit uses (1)+(2). The sentence
                # citation sufficiency (3) is stored as `smt_sentence_faithful`
                # for cross-system comparison with LLM-d and TG.
                unsat, known = smt_original_reqs(pair_dir)
                smt_hall = smt_hallucinated_reqs(rationale, unsat, known)
                struct = smt_structural_faithfulness(pair_dir)
                struct_ok = bool(struct.get("faithful"))
                hall_ok = not smt_hall

                # Sentence-citation sufficiency (using the same GPT-4.1 extractor
                # we use for LLM-direct, since the verbalized SMT rationale is
                # itself prose that cites patient facts).
                sent_idxs, sent_fallback = extract_cited_indices(
                    engine_4, extract_template,
                    patient_sents=patient_sents, rationale=rationale,
                )
                cited[sys_key] = sent_idxs
                fallbacks[sys_key] = sent_fallback

                sent_rerun_label = None
                sent_faithful = None
                if sent_fallback:
                    # Rationale didn't cite specific sentences; under ERASER
                    # sufficiency, count as not-faithful at the sentence level.
                    sent_rerun_label = "fallback_uninterpretable"
                    sent_faithful = 0
                    minimal_notes[sys_key] = ""
                else:
                    min_note = build_minimal_note(patient_sents, sent_idxs,
                                                  full_note, False)
                    minimal_notes[sys_key] = min_note
                    work_dir = faith_work / "smt"
                    try:
                        sent_rerun_label = rerun_smt(
                            pid=pid, tid=tid, minimal_note=min_note,
                            trial_obj=trial_obj, engine=engine_4,
                            smt_cfg=smt_cfg, work_dir=work_dir,
                        )
                        sent_faithful = (1 if sent_rerun_label == original_decisions[sys_key]
                                         else 0)
                    except Exception as e:
                        sent_rerun_label = f"rerun_error: {type(e).__name__}"
                        sent_faithful = 0

                # Record both tests
                per_side = struct.get("per_side") or {}
                rerun_decisions[sys_key] = json.dumps({
                    "structural": {
                        s: per_side.get(s, {}).get("rerun_status", "?")
                        for s in ("inclusion", "exclusion")
                    },
                    "sentence_citation": sent_rerun_label,
                })
                # Primary `faithful` uses the structural + hallucination check
                # (preserves semantic "by construction" claim).
                faithful[sys_key] = 1 if (struct_ok and hall_ok) else 0

                out_pair_dir.mkdir(parents=True, exist_ok=True)
                (out_pair_dir / "smt_structural.json").write_text(
                    json.dumps(struct, indent=2, default=str))
                (out_pair_dir / "smt_sentence_citation.json").write_text(
                    json.dumps({
                        "cited_indices": sent_idxs,
                        "fallback": sent_fallback,
                        "minimal_note": minimal_notes[sys_key][:2000],
                        "rerun_decision": sent_rerun_label,
                        "original_decision": original_decisions[sys_key],
                        "faithful": sent_faithful,
                    }, indent=2, default=str))
                continue

            # LLM-direct / TrialGPT — sentence-citation sufficiency test.
            # Use RAW system outputs for citations (not the verbalized rationale)
            # so that verbalizer distillation doesn't artificially drop citations.
            if sys_key == "trialgpt":
                # TrialGPT natively emits per-criterion sentence_ids in rows.
                # Use TG's FULL citation set (union across all rows, all labels) —
                # this is what TG actually cited. Filtering to decisive-only would
                # be post-hoc cherry-picking; each system is tested on its native
                # citation set as-is.
                tg_path = pair_dir / "trialgpt_decision.json"
                idxs_set = set()
                if tg_path.exists():
                    tg_json = json.loads(tg_path.read_text())
                    for side in ("inclusion", "exclusion"):
                        rows = (tg_json.get(side) or {}).get("rows") or []
                        for row in rows:
                            for sid in (row.get("sentence_ids") or []):
                                if isinstance(sid, int) and 0 <= sid < len(patient_sents):
                                    idxs_set.add(sid)
                idxs = sorted(idxs_set)
                fallback = (len(idxs) == 0)
            elif sys_key == "llm_direct":
                # LLM-direct is free prose. Use the raw `explanation` text (pre-
                # verbalizer) for citation extraction rather than the unified rationale.
                ld_path = pair_dir / "llm_direct_decision.json"
                raw_rationale = rationale
                if ld_path.exists():
                    try:
                        ld_json = json.loads(ld_path.read_text())
                        r = ld_json.get("result") or {}
                        raw_rationale = (r.get("explanation") or r.get("reasoning")
                                         or r.get("raw_text") or rationale)
                    except Exception:
                        pass
                idxs, fallback = extract_cited_indices(
                    engine_4, extract_template,
                    patient_sents=patient_sents, rationale=raw_rationale,
                )
            else:
                idxs, fallback = extract_cited_indices(
                    engine_4, extract_template,
                    patient_sents=patient_sents, rationale=rationale,
                )
            cited[sys_key] = idxs
            fallbacks[sys_key] = fallback
            min_note = build_minimal_note(patient_sents, idxs, full_note, fallback)
            minimal_notes[sys_key] = min_note

            work_dir = faith_work / sys_key
            if fallback:
                # The rationale did not cite any specific note sentences.
                # Under ACL's ERASER-style sufficiency semantics, a rationale
                # with no extractable support is NOT self-sufficient — treat
                # as unfaithful rather than trivially faithful via full-note
                # fallback.
                rerun_decisions[sys_key] = "fallback_uninterpretable"
                faithful[sys_key] = 0
                continue

            if sys_key == "llm_direct":
                dec = rerun_llm_direct(pid=pid, tid=tid, minimal_note=min_note,
                                       trial_obj=trial_obj, engine=engine_4,
                                       work_dir=work_dir)
            elif sys_key == "trialgpt":
                dec = rerun_trialgpt(pid=pid, tid=tid, minimal_note=min_note,
                                     trial_obj=trial_obj, engine=engine_4,
                                     work_dir=work_dir)
            else:
                errors.append(f"{sys_key}: unknown system")
                continue
            rerun_decisions[sys_key] = dec
            faithful[sys_key] = 1 if dec == original_decisions[sys_key] else 0
        except Exception as e:
            errors.append(f"{sys_key}: {type(e).__name__}: {e}")
            traceback.print_exc(file=sys.stderr)

    result = {
        "pair": pair_name,
        "original_decisions": original_decisions,
        "cited_sentence_indices": cited,
        "fallback_used": fallbacks,
        "minimal_note": minimal_notes,
        "rerun_decisions": rerun_decisions,
        "faithful": faithful,
        "smt_hallucinated_reqs": smt_hall,
        "errors": errors,
    }
    (out_pair_dir / "faithfulness.json").write_text(
        json.dumps(result, indent=2, default=str))
    return result


# ── Aggregation ───────────────────────────────────────────────────────────
def aggregate(results: List[Dict[str, Any]], systems: Tuple[str, ...]) -> Dict[str, Any]:
    rates: Dict[str, float] = {}
    totals: Dict[str, int] = {}
    matched: Dict[str, int] = {}
    for s in systems:
        totals[s] = 0
        matched[s] = 0
    n_hall_pairs = 0
    n_smt_pairs = 0
    # SMT sentence-citation sufficiency — separate from structural
    smt_sent_matched = 0
    smt_sent_total = 0
    smt_sent_fallback = 0
    for r in results:
        for s in systems:
            if s in r.get("faithful", {}):
                totals[s] += 1
                matched[s] += r["faithful"][s]
        if "smt" in systems:
            if "smt" in r.get("faithful", {}) or r.get("smt_hallucinated_reqs") is not None:
                n_smt_pairs += 1
                if r.get("smt_hallucinated_reqs"):
                    n_hall_pairs += 1
            # SMT sentence-citation from per-pair artifact
            rd = r.get("rerun_decisions", {}).get("smt", "")
            if isinstance(rd, str) and rd.startswith("{"):
                try:
                    parsed = json.loads(rd)
                    sc = parsed.get("sentence_citation")
                    orig = r.get("original_decisions", {}).get("smt")
                    if sc is not None:
                        smt_sent_total += 1
                        if sc == "fallback_uninterpretable":
                            smt_sent_fallback += 1
                        elif sc == orig:
                            smt_sent_matched += 1
                except Exception:
                    pass
    for s in systems:
        rates[s] = round(matched[s] / totals[s], 3) if totals[s] else 0.0
    summary = {
        "n_pairs": len(results),
        "faithfulness_rate": rates,
        "faithfulness_counts": {s: {"matched": matched[s], "total": totals[s]} for s in systems},
        "hallucinated_req_rate": round(n_hall_pairs / n_smt_pairs, 3) if n_smt_pairs else 0.0,
        "smt_sentence_citation": {
            "matched": smt_sent_matched,
            "total": smt_sent_total,
            "fallback": smt_sent_fallback,
            "rate": round(smt_sent_matched / smt_sent_total, 3) if smt_sent_total else 0.0,
            "note": "Sentence-level sufficiency symmetric with LLM-d and TG tests: "
                    "extract cited note sentences from SMT rationale, rerun full SMT "
                    "pipeline (miner+solver) on stripped note, check decision match.",
        },
        "per_pair_summary": [
            {
                "pair": r["pair"],
                "original": r.get("original_decisions", {}),
                "rerun": r.get("rerun_decisions", {}),
                "faithful": r.get("faithful", {}),
                "smt_hallucinated_reqs": r.get("smt_hallucinated_reqs", []),
                "errors": r.get("errors", []),
            }
            for r in results
        ],
    }
    return summary


def print_table(agg: Dict[str, Any], systems: Tuple[str, ...]) -> None:
    print("\n=== Faithfulness results ===")
    print(f"pairs={agg['n_pairs']}   hallucinated_req_rate={agg['hallucinated_req_rate']:.3f}")
    print(f"{'system':<14}{'matched':>9}{'total':>7}{'rate':>8}")
    for s in systems:
        c = agg["faithfulness_counts"][s]
        print(f"{s:<14}{c['matched']:>9}{c['total']:>7}{agg['faithfulness_rate'][s]:>8.3f}")


# ── Pair discovery ────────────────────────────────────────────────────────
def discover_pairs(in_dir: pathlib.Path) -> List[pathlib.Path]:
    pairs: List[pathlib.Path] = []
    for shard in sorted(in_dir.iterdir()):
        if not shard.is_dir() or not shard.name.startswith("shard_"):
            continue
        for pair in sorted(shard.iterdir()):
            if pair.is_dir() and "__" in pair.name:
                pairs.append(pair)
    # Fallback: flat layout
    if not pairs:
        for pair in sorted(in_dir.iterdir()):
            if pair.is_dir() and "__" in pair.name:
                pairs.append(pair)
    return pairs


# ── Main ──────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", default="evaluation/results/verbalize_judge_unified")
    ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    ap.add_argument("--out-dir", default=f"evaluation/results/faithfulness_{ts}")
    ap.add_argument("--data-root", default="/tmp/satir_full_dataset")
    ap.add_argument("--build-root", default="build")
    ap.add_argument("--max-workers", type=int, default=1)
    ap.add_argument("--sample", type=int, default=0,
                    help="Limit to first N pairs (0 = all)")
    ap.add_argument("--systems", nargs="+", default=list(SYSTEMS_ALL),
                    choices=list(SYSTEMS_ALL))
    args = ap.parse_args()

    in_dir = pathlib.Path(args.in_dir)
    if not in_dir.is_absolute():
        in_dir = ROOT / in_dir
    out_dir = pathlib.Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    data_root = pathlib.Path(args.data_root)

    pair_dirs = discover_pairs(in_dir)
    if args.sample and args.sample > 0:
        pair_dirs = pair_dirs[: args.sample]
    print(f"Found {len(pair_dirs)} pairs under {in_dir}", file=sys.stderr)

    systems: Tuple[str, ...] = tuple(args.systems)
    extract_template = P_EXTRACT_CITED.read_text(encoding="utf-8")

    endpoint_4 = os.environ.get("OPENAI_ENDPOINT")
    if not endpoint_4:
        print("FATAL: set OPENAI_ENDPOINT (source .env first)", file=sys.stderr)
        sys.exit(2)
    engine_4 = AzureInferenceEngine(
        endpoint=endpoint_4, api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-4.1", default_temperature=0.0,
    )

    smt_cfg = SMTConfig(
        data_root=data_root,
        build_root=pathlib.Path(args.build_root),
        prompt_root=ROOT / "smt_matcher" / "prompts" / "clinical_trial",
    )

    def _out_pair_dir(pd: pathlib.Path) -> pathlib.Path:
        # mirror shard/pair hierarchy
        if pd.parent.name.startswith("shard_"):
            return out_dir / pd.parent.name / pd.name
        return out_dir / pd.name

    def _do(pd: pathlib.Path) -> Optional[Dict[str, Any]]:
        try:
            return process_pair(
                pd,
                out_pair_dir=_out_pair_dir(pd),
                data_root=data_root, smt_cfg=smt_cfg,
                engine_4=engine_4, extract_template=extract_template,
                systems=systems,
            )
        except Exception as e:
            print(f"  FAIL {pd.name}: {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return None

    t0 = time.perf_counter()
    results: List[Dict[str, Any]] = []
    if args.max_workers <= 1:
        for i, pd in enumerate(pair_dirs, 1):
            r = _do(pd)
            if r:
                results.append(r)
            print(f"  {i}/{len(pair_dirs)} pairs ({time.perf_counter()-t0:.1f}s)",
                  file=sys.stderr)
    else:
        with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
            futs = {ex.submit(_do, pd): pd for pd in pair_dirs}
            for i, fut in enumerate(as_completed(futs), 1):
                r = fut.result()
                if r:
                    results.append(r)
                print(f"  {i}/{len(pair_dirs)} pairs ({time.perf_counter()-t0:.1f}s)",
                      file=sys.stderr)

    agg = aggregate(results, systems)
    summary_file = out_dir / "faithfulness_summary.json"
    summary_file.write_text(json.dumps({
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        **agg,
    }, indent=2, default=str))
    print_table(agg, systems)
    print(f"\nSaved to {summary_file}")


if __name__ == "__main__":
    main()
