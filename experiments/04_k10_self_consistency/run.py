#!/usr/bin/env python3
"""
Fliprate experiment runner (H1).

For each (patient, trial) pair and each system, run N repeats and measure
how often the system's decision changes across runs.

Systems compared:
  - smt:        our SMT matcher (LLM mines values, Z3 evaluates)
  - smt_value:  SMT matcher but value-mining cached from first run (isolates
                matching-step flip rate, should be ~0)
  - llm_direct: LLM-as-eligibility-judge on full trial + patient (end-to-end)
  - trialgpt:   TrialGPT sentence-level criterion judge

Outputs: evaluation/results/h1_fliprate_{N}pairs_{K}repeats.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine import AzureInferenceEngine
from smt_matcher.judges.llm_judge import run_llm_eligibility_judge
from smt_matcher.judges.trialgpt_judge import run_trialgpt_judge

# SMT matcher imports (for the "smt_full" and "smt_match_only" systems)
from smt_matcher.match_patient_to_trial import (
    Config as SMTConfig,
    run_match_for_side as run_smt_match_for_side,
)

# ─── Helpers ────────────────────────────────────────────────────────────

def load_trial_from_corpus(trial_id: str, corpus_path: pathlib.Path) -> Optional[Dict[str, Any]]:
    """Load a trial record by NCT ID from a JSONL corpus."""
    with open(corpus_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("_id") == trial_id:
                return _normalize_trial(obj)
    return None


def _normalize_trial(obj: Dict[str, Any]) -> Dict[str, Any]:
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


def load_patient_from_queries(patient_id: str, queries_path: pathlib.Path) -> Optional[Dict[str, Any]]:
    with open(queries_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("_id") == patient_id:
                return {
                    "patient_id": patient_id,
                    "_id": patient_id,
                    "text": obj.get("text") or obj.get("note") or "",
                    "metadata": obj.get("metadata") or {},
                }
    return None


# ─── Run one repeat of one system ───────────────────────────────────────

def _uncached_out_root(base: pathlib.Path, system: str, repeat: int) -> pathlib.Path:
    """
    Each repeat writes to a unique out_root so the fingerprint-based cache
    does NOT deduplicate repeats. Fliprate requires fresh calls.
    """
    p = base / "_uncached" / system / f"repeat_{repeat}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_llm_direct_once(trial_id, trial_obj, patient, engine, prompt_path,
                         base_out: pathlib.Path, repeat: int) -> Dict[str, Any]:
    out_root = _uncached_out_root(base_out, "llm_direct", repeat)
    try:
        res = run_llm_eligibility_judge(
            trial_id=trial_id, trial_obj=trial_obj, patient=patient, engine=engine,
            prompt_path=prompt_path, out_root=out_root,
            model_name="gpt-4.1", temperature=0.0,
        )
        r = res["result"]
        return {"system": "llm_direct", "repeat": repeat,
                "eligible": r["eligible"],
                "raw_label": r["eligibility"],
                "parse_error": r.get("parse_error"),   # NEW: surface parse failures
                "reasoning_snippet": (r.get("reasoning") or "")[:200],  # for debugging flips
                "error": None}
    except Exception as e:
        return {"system": "llm_direct", "repeat": repeat,
                "eligible": None, "raw_label": None,
                "parse_error": None,
                "error": f"{type(e).__name__}: {e}"}


def run_smt_once(
    trial_id: str, patient: Dict[str, Any], engine, smt_cfg: SMTConfig,
    repeat: int,
) -> Dict[str, Any]:
    """
    Run our SMT pipeline on (patient, trial):
      1. LLM extracts variable values from patient note (value mining)
      2. Z3 evaluates the precompiled SMT program with those values

    Both steps run fresh each repeat — value mining is nondeterministic.

    Eligibility mapping:
      inc_sat=True AND exc_sat=True → eligible (both sides pass)
      inc_sat=False OR exc_sat=False → ineligible
      otherwise (some None) → None = 'unknown' (valid 3rd label, not a failure)
    """
    try:
        # NOTE: SMTMatcher prints verbose SMT programs to stdout; we accept the
        # noise rather than use contextlib.redirect_stdout (which is NOT thread-safe).
        # For cleaner logs, run with --max-concurrent 1 or pipe stdout to /dev/null.
        inc = run_smt_match_for_side("inclusion", trial_id, patient, smt_cfg, engine, None)
        exc = run_smt_match_for_side("exclusion", trial_id, patient, smt_cfg, engine, None)
        inc_sat = inc.get("sat_like")
        exc_sat = exc.get("sat_like")
        # Combined eligibility: None if either side is unknown, else rule-combine
        if inc_sat is None or exc_sat is None:
            eligible = None
        else:
            # Lenient (prescreen): defer None → pass-through. Matches TG convention.
            eligible = (inc_sat is not False) and (exc_sat is not False)
        return {"system": "smt", "repeat": repeat,
                "eligible": eligible,
                "inc_sat": inc_sat, "exc_sat": exc_sat,
                "parse_error": None,
                "error": None}
    except FileNotFoundError as e:
        return {"system": "smt", "repeat": repeat,
                "eligible": None, "parse_error": None,
                "error": f"FileNotFoundError: missing build artifact: {e}"}
    except Exception as e:
        return {"system": "smt", "repeat": repeat,
                "eligible": None, "parse_error": None,
                "error": f"{type(e).__name__}: {e}"}


def run_trialgpt_once(trial_id, trial_obj, patient, engine,
                       base_out: pathlib.Path, repeat: int) -> Dict[str, Any]:
    out_root = _uncached_out_root(base_out, "trialgpt", repeat)
    try:
        res = run_trialgpt_judge(
            trial_id=trial_id, trial_obj=trial_obj, patient=patient, engine=engine,
            out_root=out_root, model_name="gpt-4.1", temperature=0.0,
        )
        agg = res.get("aggregate", {})
        # Aggregate TrialGPT parse errors from both sides
        inc_err = (res.get("inclusion") or {}).get("parse_error")
        exc_err = (res.get("exclusion") or {}).get("parse_error")
        parse_err = inc_err or exc_err  # surface any parse failure
        return {"system": "trialgpt", "repeat": repeat,
                "eligible": agg.get("eligible"),
                "eligible_strict": agg.get("eligible_strict"),
                "parse_error": parse_err,
                "error": None}
    except Exception as e:
        return {"system": "trialgpt", "repeat": repeat,
                "eligible": None, "parse_error": None,
                "error": f"{type(e).__name__}: {e}"}


# ─── Metrics ────────────────────────────────────────────────────────────

def _adjacent_flip_rate(labels: List[Any]) -> float:
    """Fraction of consecutive pairs that differ."""
    if len(labels) < 2:
        return 0.0
    flips = sum(1 for i in range(len(labels) - 1) if labels[i] != labels[i + 1])
    return flips / (len(labels) - 1)


def _effective_flip_rate(labels: List[Any]) -> float:
    """1 - majority-label consistency."""
    if not labels:
        return 0.0
    c = Counter(labels)
    _, majority = c.most_common(1)[0]
    return 1.0 - (majority / len(labels))


def _label_entropy(labels: List[Any]) -> float:
    import math
    n = len(labels)
    if n == 0:
        return 0.0
    c = Counter(labels)
    h = 0.0
    for v in c.values():
        p = v / n
        if p > 0:
            h -= p * math.log2(p)
    return h


def summarize_repeats(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Group by (pair, system), compute metrics across repeats.

    Treats parse errors and API errors as MISSING data (not as a separate
    label). A repeat with a parse error is excluded from flip-rate
    computation so we don't count extraction bugs as model disagreement.
    """
    by_pair: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for r in results:
        key = f"{r['patient_id']}__{r['trial_id']}"
        sys_name = r["system"]
        by_pair.setdefault(key, {}).setdefault(sys_name, []).append(r)

    summary: Dict[str, Any] = {}
    for pair_key, systems in by_pair.items():
        summary[pair_key] = {}
        for sys_name, runs in systems.items():
            runs_sorted = sorted(runs, key=lambda x: x["repeat"])
            n_errors       = sum(1 for r in runs_sorted if r.get("error"))
            n_parse_errors = sum(1 for r in runs_sorted if r.get("parse_error"))
            # Keep only runs without API/parse errors. None eligibility is a
            # valid "unknown" label (not a failure) and IS included.
            clean = [r for r in runs_sorted
                     if not r.get("error") and not r.get("parse_error")]
            # Map True/False/None -> "eligible"/"ineligible"/"unknown"
            def _norm(v):
                if v is True:  return "eligible"
                if v is False: return "ineligible"
                return "unknown"
            labels = [_norm(r.get("eligible")) for r in clean]

            summary[pair_key][sys_name] = {
                "n_repeats": len(runs_sorted),
                "n_clean": len(labels),
                "n_errors": n_errors,
                "n_parse_errors": n_parse_errors,
                "labels": labels,
                "raw_labels": [r.get("eligible") for r in runs_sorted],  # preserves Nones for audit
                "adjacent_flip_rate": _adjacent_flip_rate(labels) if len(labels) >= 2 else 0.0,
                "effective_flip_rate": _effective_flip_rate(labels),
                "entropy_bits": _label_entropy(labels),
            }
    return summary


def aggregate(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate fliprate across pairs per system."""
    by_system: Dict[str, List[Dict[str, float]]] = {}
    for pair_key, systems in summary.items():
        for sys_name, stats in systems.items():
            by_system.setdefault(sys_name, []).append({
                "adj": stats["adjacent_flip_rate"],
                "eff": stats["effective_flip_rate"],
                "H":   stats["entropy_bits"],
            })

    def _mean(xs): return sum(xs) / len(xs) if xs else 0.0
    def _percentile(xs, p):
        if not xs: return 0.0
        s = sorted(xs)
        k = int(len(s) * p)
        return s[min(k, len(s) - 1)]

    out: Dict[str, Any] = {}
    for sys_name, rows in by_system.items():
        adj = [r["adj"] for r in rows]
        eff = [r["eff"] for r in rows]
        H   = [r["H"]   for r in rows]
        out[sys_name] = {
            "n_pairs": len(rows),
            "mean_adjacent_flip_rate": round(_mean(adj), 4),
            "mean_effective_flip_rate": round(_mean(eff), 4),
            "mean_entropy_bits": round(_mean(H), 4),
            "p50_adjacent_flip_rate": round(_percentile(adj, 0.50), 4),
            "p95_adjacent_flip_rate": round(_percentile(adj, 0.95), 4),
            "max_adjacent_flip_rate": round(max(adj) if adj else 0.0, 4),
            "pairs_with_any_flip": sum(1 for r in rows if r["adj"] > 0),
        }
    return out


# ─── Main experiment ────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="H1 fliprate experiment")
    ap.add_argument("--pairs", nargs="+", default=None,
                    help="List of 'patient_id,trial_id' (default: use test subset)")
    ap.add_argument("--pairs-file", default=None,
                    help="Path to a text file with one 'patient_id,trial_id' per line.")
    ap.add_argument("--n-repeats", type=int, default=3)
    ap.add_argument("--systems", nargs="+", default=["llm_direct", "trialgpt"],
                    help="Subset of: llm_direct, trialgpt (smt requires more setup)")
    ap.add_argument("--data-root", default=str(ROOT / "dataset" / "clinical_trial"))
    ap.add_argument("--out-dir", default=str(ROOT / "evaluation" / "results"))
    ap.add_argument("--base-out", default=str(ROOT / "evaluation" / "results" / "_work"))
    ap.add_argument("--max-concurrent", type=int, default=8)
    ap.add_argument("--prompt-root", default=str(ROOT / "smt_matcher" / "prompts" / "clinical_trial" / "SMTMatcher"))
    ap.add_argument("--smt-build-root", default=str(ROOT / "build"),
                    help="Build dir with ir/, symtab/, linkmap/ (required for SMT systems).")
    ap.add_argument("--smt-prompt-root", default=str(ROOT / "smt_matcher" / "prompts" / "clinical_trial"),
                    help="SMT matcher prompt root.")
    args = ap.parse_args()

    # Load pairs from file if given
    if args.pairs_file:
        with open(args.pairs_file) as f:
            args.pairs = [line.strip() for line in f if line.strip()]

    # Default pair list: what's in our test build
    if not args.pairs:
        args.pairs = ["sigir-20141,NCT00000402", "sigir-20141,NCT00000408", "sigir-20141,NCT00000520",
                      "sigir-20142,NCT00000402", "sigir-20142,NCT00000408", "sigir-20142,NCT00000520"]

    pair_tuples = []
    for p in args.pairs:
        pid, tid = p.split(",")
        pair_tuples.append((pid.strip(), tid.strip()))

    data_root = pathlib.Path(args.data_root)
    corpus_path = data_root / "sigir" / "corpus.jsonl"
    queries_path = data_root / "sigir" / "queries.jsonl"
    base_out = pathlib.Path(args.base_out)
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt_root = pathlib.Path(args.prompt_root)
    llm_direct_prompt = prompt_root / "eligibility.explicit.prompt"

    # Validate
    for p in [corpus_path, queries_path, llm_direct_prompt]:
        if not p.exists():
            print(f"FATAL: missing {p}", file=sys.stderr)
            sys.exit(1)

    # Pre-load trials and patients
    print(f"Loading {len(pair_tuples)} pairs...", file=sys.stderr)
    trials: Dict[str, Any] = {}
    patients: Dict[str, Any] = {}
    for pid, tid in pair_tuples:
        if tid not in trials:
            trials[tid] = load_trial_from_corpus(tid, corpus_path)
            if trials[tid] is None:
                print(f"  WARN: trial {tid} not in corpus; skipping", file=sys.stderr)
        if pid not in patients:
            patients[pid] = load_patient_from_queries(pid, queries_path)
            if patients[pid] is None:
                print(f"  WARN: patient {pid} not in queries; skipping", file=sys.stderr)
    pair_tuples = [(pid, tid) for pid, tid in pair_tuples
                    if trials.get(tid) is not None and patients.get(pid) is not None]
    print(f"  loaded {len(pair_tuples)} valid pairs, {len(trials)} trials, {len(patients)} patients",
          file=sys.stderr)

    # Build engine
    engine = AzureInferenceEngine(
        endpoint=os.environ["OPENAI_ENDPOINT"],
        api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-4.1",
        default_temperature=0.0,   # match judges; miner uses engine default
    )

    # Build the task list: one per (pair, system, repeat)
    tasks = []
    for pid, tid in pair_tuples:
        for system in args.systems:
            for repeat in range(args.n_repeats):
                tasks.append((pid, tid, system, repeat))

    print(f"Dispatching {len(tasks)} tasks with concurrency={args.max_concurrent}...",
          file=sys.stderr)
    results: List[Dict[str, Any]] = []
    t0 = time.perf_counter()

    # SMT-mode config (only used if 'smt_*' in systems)
    smt_cfg = None
    if any(s.startswith("smt") for s in args.systems):
        smt_cfg = SMTConfig(
            data_root=pathlib.Path(args.data_root),
            build_root=pathlib.Path(args.smt_build_root),
            prompt_root=pathlib.Path(args.smt_prompt_root),
        )

    def _run_task(pid, tid, system, repeat):
        trial_obj = trials[tid]
        patient   = patients[pid]
        if system == "llm_direct":
            r = run_llm_direct_once(tid, trial_obj, patient, engine,
                                    llm_direct_prompt, base_out, repeat)
        elif system == "trialgpt":
            r = run_trialgpt_once(tid, trial_obj, patient, engine,
                                  base_out, repeat)
        elif system == "smt":
            r = run_smt_once(tid, patient, engine, smt_cfg, repeat)
        else:
            return {"system": system, "repeat": repeat, "error": "unsupported system"}
        r["patient_id"] = pid
        r["trial_id"] = tid
        return r

    with ThreadPoolExecutor(max_workers=args.max_concurrent) as pool:
        futures = [pool.submit(_run_task, *t) for t in tasks]
        done = 0
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if done % max(1, len(tasks) // 20) == 0 or done == len(tasks):
                elapsed = time.perf_counter() - t0
                print(f"  {done}/{len(tasks)} done ({elapsed:.1f}s)", file=sys.stderr)

    wall = time.perf_counter() - t0
    print(f"Done in {wall:.1f}s", file=sys.stderr)

    summary = summarize_repeats(results)
    agg = aggregate(summary)

    output = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "n_pairs": len(pair_tuples),
        "n_repeats": args.n_repeats,
        "systems": args.systems,
        "wall_time_s": round(wall, 1),
        "aggregate": agg,
        "per_pair": summary,
        "raw_results": results,
    }

    sys_tag = "-".join(sorted(args.systems))
    out_path = out_dir / f"h1_fliprate_{len(pair_tuples)}pairs_{args.n_repeats}repeats__{sys_tag}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n=== Aggregate fliprate ===")
    print(json.dumps(agg, indent=2))
    print(f"\nWrote: {out_path}")


if __name__ == "__main__":
    main()
