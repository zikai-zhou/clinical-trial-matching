"""SatIR end-to-end CF — MINIMAL UNSAT CORE version.

Proper implementation of the "100% by construction" test:
  1. For each INELIGIBLE pair, read SatIR's unsat core (list of REQ labels).
  2. Extract the assertion text of each REQ label from the SMT program.
  3. Use GPT-4.1 to parse each assertion and produce a {variable: target_value}
     map — the minimum set of variable assignments that would satisfy the core.
  4. Construct a CF chart that asserts exactly those target values, leaving
     everything else identical.
  5. Re-run SatIR end-to-end (miner + solver) on the CF chart.
  6. Check whether decision flipped.

Under correct SatIR theorem: the unsat-core minimality guarantees that the
full program with the flipped values is SAT. Empirical flip rate should be
~100%, bounded only by miner re-extraction accuracy.

Usage:
    python -m evaluation.explainability.counterfactual_satir_minimal_core
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine import AzureInferenceEngine  # noqa: E402
from smt_matcher.match_patient_to_trial import run_match_for_side, Config  # noqa: E402
from evaluation.explainability.smtlib_parser import (  # noqa: E402
    extract_named_assertions, extract_targets,
)
from evaluation.explainability.z3_target_solver import (  # noqa: E402
    extract_targets_via_optimize,
)
from evaluation.explainability.preprocess_trial_conflicts import (  # noqa: E402
    preprocess_trial_smt, identify_conflicting_exc_labels,
)

USE_Z3_OPTIMIZE_PARSER = os.environ.get("USE_Z3_OPTIMIZE_PARSER", "1") == "1"
PREPROCESS_INC_EXC_CONFLICTS = os.environ.get("PREPROCESS_INC_EXC_CONFLICTS", "0") == "1"


def _evaluate_pruned_smt(smt_program: str, pvv: dict, vindex: dict) -> str:
    """Given a (possibly pruned) SMT-LIB program and mined patient values,
    run Z3 directly to determine sat/unsat. Returns 'sat', 'unsat', or 'unknown'."""
    import z3
    ctx = z3.Context()
    s = z3.Solver(ctx=ctx)
    try:
        s.from_string(smt_program)
    except z3.Z3Exception:
        return "error"
    # Apply mined values as hard equality assertions (mirrors SatIR's _assert_block)
    for vname, vinfo in (pvv or {}).items():
        val = vinfo.get("value") if isinstance(vinfo, dict) else vinfo
        if val is None: continue
        if isinstance(val, str) and val.strip().lower() in {"null", "none", ""}: continue
        sort = (vindex.get(vname) or {}).get("type", "").lower()
        try:
            if sort == "bool":
                c = z3.Bool(vname, ctx=ctx)
                b = str(val).lower() in {"true", "1", "t", "yes"}
                s.add(c == z3.BoolVal(b, ctx=ctx))
            elif sort in {"int", "integer"}:
                s.add(z3.Int(vname, ctx=ctx) == z3.IntVal(int(float(val)), ctx=ctx))
            elif sort == "real":
                s.add(z3.Real(vname, ctx=ctx) == z3.RealVal(float(val), ctx=ctx))
        except Exception:
            continue
    r = s.check()
    return str(r)

RESULTS = ROOT / "evaluation" / "results"
V3 = RESULTS / "verbalize_judge_235_v3"
OUT = RESULTS / (os.environ.get("CF_OUT") or "counterfactual_satir_minimal_20")
OUT.mkdir(parents=True, exist_ok=True)


def load_patient(pid, data_root):
    with open(data_root / "sigir" / "queries.jsonl") as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("_id") == pid:
                return obj.get("text", "")
    return ""


# Match a full (assert (! ... :named REQ...)) block.
_ASSERT_RE = re.compile(r"\(assert\s*\(!\s*(.*?)\s*:named\s+(REQ\w+)\)\s*\)", re.DOTALL)


def extract_req_assertion(smt_lines, req_label):
    """Return the inner assertion text (not including the :named wrapper) for
    a given REQ label, or None if not found."""
    text = "\n".join(smt_lines)
    for m in _ASSERT_RE.finditer(text):
        inner, label = m.group(1).strip(), m.group(2)
        if label == req_label:
            return inner
    return None


def build_target_map_via_llm(engine, core_assertions, current_mined, variable_descriptions):
    """Given the assertion text(s) from unsat core, ask GPT-4.1 to produce
    {variable: target_value} — minimum flips to satisfy the core."""
    asserts_str = "\n".join(f"- {req_label}: {text}"
                             for req_label, text in core_assertions.items())
    mined_str = json.dumps(current_mined, indent=2, default=str)[:3000]
    # Truncate variable descriptions
    vars_str = "\n".join(
        f"- {v}: {(variable_descriptions.get(v) or '')[:200]}"
        for v in list(variable_descriptions.keys())[:40]
    )

    prompt = f"""You are analyzing SMT-LIB assertions to identify the minimum set of
variable assignments needed to satisfy them.

ASSERTIONS IN THE UNSAT CORE (each must be made satisfiable):
{asserts_str}

CURRENT MINED VARIABLE VALUES (what the patient chart currently implies):
{mined_str}

VARIABLE DESCRIPTIONS (for interpretation):
{vars_str}

For each assertion above, identify:
1. Which variable(s) it constrains
2. What value(s) those variables need to take to make the assertion satisfiable
3. Whether the current mined value differs from the target

Return a strict JSON object mapping variable names to target values. Include
ONLY variables whose current value differs from the target. Use JSON types
appropriate to the variable (boolean true/false, numbers, strings).

Example output:
{{"patient_age_value_recorded_now_in_years": 30, "patient_has_diabetes_now": true, "patient_has_finding_of_heart_failure_now": false}}

Return ONLY the JSON object, no commentary.
"""
    raw = engine(prompt, temperature=0.0)
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


MINIMAL_CF_PROMPT = """You are rewriting a clinical patient chart to assert specific fact values.
Make the MINIMUM edits to the chart so the patient now has each listed fact.
Keep everything else byte-identical where possible.

ORIGINAL CHART:
#CHART#

FACTS THE CHART MUST NOW ASSERT:
#TARGETS#

For each fact, rewrite the chart so the fact is now clearly and explicitly
true in the chart. Integrate edits minimally — change only what is necessary
to flip each fact from its current value to the target value. Keep the
overall style and narrative identical.

Return the COMPLETE rewritten chart. No preamble, no commentary.
"""


def gen_minimal_cf(engine, chart, targets, variable_descriptions):
    targets_str = "\n".join(
        f"- {var}: {val}  ({(variable_descriptions.get(var) or '')[:150]})"
        for var, val in targets.items()
    )
    prompt = (MINIMAL_CF_PROMPT
              .replace("#CHART#", chart)
              .replace("#TARGETS#", targets_str))
    raw = engine(prompt, temperature=0.0)
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return raw.strip()


def process_one(candidate, engine, cfg, data_root, out_dir):
    pair = candidate["pair"]
    pid, tid = pair.split("__")
    pd = None
    for shard in V3.glob("shard_*"):
        cand = shard / pair
        if cand.exists():
            pd = cand; break
    if pd is None:
        return {"pair": pair, "error": "no pair_dir"}

    # Load unsat cores + SMT programs per side
    smt_dec = json.load(open(pd / "smt_decision.json"))
    variable_descriptions = {}
    unsat_labels = []
    targets: dict = {}
    core_assertion_count = 0

    inc_raw = (smt_dec.get("inclusion") or {}).get("raw") or {}
    exc_raw = (smt_dec.get("exclusion") or {}).get("raw") or {}
    inc_er = inc_raw.get("eval_result") or {}
    exc_er = exc_raw.get("eval_result") or {}
    inc_unsat = inc_er.get("status") == "unsat"
    exc_unsat = exc_er.get("status") == "unsat"

    # Collect diagnostics from BOTH sides (unsat core labels + variable descriptions).
    for side_raw, side_er in ((inc_raw, inc_er), (exc_raw, exc_er)):
        if side_er.get("status") != "unsat":
            continue
        labels = (side_er.get("label_status") or {}).get("unsat") or []
        unsat_labels.extend(labels)
        smt_src = "\n".join(side_raw.get("smt_program_lines") or [])
        named = extract_named_assertions(smt_src)
        for lbl in labels:
            if lbl in named:
                core_assertion_count += 1
        var_index = side_raw.get("variable_index") or {}
        for k, v in var_index.items():
            if k not in variable_descriptions:
                variable_descriptions[k] = (v.get("description") or "")

    # Target extraction: prefer Z3-Optimize (principled), fall back to heuristic s-expr parser.
    if USE_Z3_OPTIMIZE_PARSER:
        inc_prog = "\n".join(inc_raw.get("smt_program_lines") or []) or None
        exc_prog = "\n".join(exc_raw.get("smt_program_lines") or []) or None
        try:
            z3_targets, z3_status = extract_targets_via_optimize(
                inc_prog, exc_prog,
                inc_raw.get("patient_var_values") or {},
                exc_raw.get("patient_var_values") or {},
                inc_was_unsat=inc_unsat,
                exc_was_unsat=exc_unsat,
            )
            if z3_status == "ok" and z3_targets:
                targets = z3_targets
        except Exception as e:
            print(f"  [z3-opt failed on {pair}: {type(e).__name__}: {e}]", file=sys.stderr)

    # Heuristic fallback if Z3-Optimize returned no targets
    if not targets:
        import math

        # Build a lightweight range-constraint map from ALL inc REQ bodies
        # (not just unsat-core ones). This captures the inc-feasible region
        # that any flipped variable must still satisfy, even when inc is
        # currently SAT (so has no unsat-core labels).
        def _numeric_range(expr):
            """Return (var, (lo, hi)) if expr is a simple numeric comparison."""
            if not isinstance(expr, list) or len(expr) != 3: return None
            op, a, b = expr
            if not isinstance(a, str) or isinstance(b, list): return None
            try: n = float(b)
            except (TypeError, ValueError): return None
            if op == ">=": return (a, (n, math.inf))
            if op == ">":  return (a, (n + 1e-9, math.inf))
            if op == "<=": return (a, (-math.inf, n))
            if op == "<":  return (a, (-math.inf, n - 1e-9))
            if op == "=":  return (a, (n, n))
            return None

        def _intersect(r1, r2):
            return (max(r1[0], r2[0]), min(r1[1], r2[1]))

        def _empty(r):
            return r[0] > r[1]

        def _collect_inc_ranges(named_inc):
            """Walk all inc REQ bodies; for conjunctive/atomic comparisons, accumulate range intersections per var."""
            cons = {}
            def walk(body):
                if isinstance(body, list) and body:
                    if body[0] == "and":
                        for s in body[1:]: walk(s)
                        return
                r = _numeric_range(body)
                if r:
                    var, rng = r
                    prev = cons.get(var, (-math.inf, math.inf))
                    cons[var] = _intersect(prev, rng)
            for body in named_inc.values():
                walk(body)
            return cons

        # Collect inc-side range constraints (used for cross-side conflict detection).
        named_inc = extract_named_assertions("\n".join(inc_raw.get("smt_program_lines") or [])) if inc_raw else {}
        inc_ranges = _collect_inc_ranges(named_inc)

        # Phase 1: inc-side targets (from unsat core, aux-skip).
        inc_targets: dict = {}
        if inc_er.get("status") == "unsat":
            labels = (inc_er.get("label_status") or {}).get("unsat") or []
            for lbl in labels:
                if "_AUXILIARY" in lbl: continue
                body = named_inc.get(lbl)
                if body is None: continue
                inc_targets.update(extract_targets(body))

        # Phase 2: exc-side targets, cross-side-aware (avoid disjuncts that
        # violate inc ranges, even if inc has no unsat core currently).
        def _candidate_conflicts_inc(cand_targets, inc_ranges, forced_inc):
            for var, val in cand_targets.items():
                # Numeric range check
                if var in inc_ranges and isinstance(val, (int, float)):
                    lo, hi = inc_ranges[var]
                    if not (lo <= float(val) <= hi):
                        return True
                # Forced inc-target check (bool equality)
                if var in forced_inc:
                    a, b = forced_inc[var], val
                    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
                        if abs(float(a)-float(b))>1e-9: return True
                    elif str(a).lower() != str(b).lower(): return True
            return False

        def _extract_cross_side_aware(body, inc_ranges, forced_inc):
            if isinstance(body, list) and body and body[0] == "or":
                for sub in body[1:]:
                    sub_t = extract_targets(sub)
                    if sub_t and not _candidate_conflicts_inc(sub_t, inc_ranges, forced_inc):
                        return sub_t
                return {}  # trial-level infeasibility: all disjuncts conflict with inc
            return extract_targets(body)

        exc_targets: dict = {}
        if exc_er.get("status") == "unsat":
            labels = (exc_er.get("label_status") or {}).get("unsat") or []
            named_exc = extract_named_assertions("\n".join(exc_raw.get("smt_program_lines") or []))
            for lbl in labels:
                if "_AUXILIARY" in lbl: continue
                body = named_exc.get(lbl)
                if body is None: continue
                exc_targets.update(_extract_cross_side_aware(body, inc_ranges, inc_targets))

        # Merge: inclusion takes priority on conflicts.
        targets = {**exc_targets, **inc_targets}

    if not targets:
        return {"pair": pair, "error": "no targets extracted from unsat core"}

    # Step 2: generate minimal CF chart
    chart = load_patient(pid, data_root)
    cf_chart = gen_minimal_cf(engine, chart, targets, variable_descriptions)

    # Step 3: run SatIR E2E on CF chart
    patient_cf = {"patient_id": pid, "_id": pid, "text": cf_chart, "metadata": {}}
    try:
        inc = run_match_for_side("inclusion", tid, patient_cf, cfg, engine, None)
        exc = run_match_for_side("exclusion", tid, patient_cf, cfg, engine, None)
    except Exception as e:
        return {"pair": pair, "error": f"satir: {type(e).__name__}: {e}"}

    inc_sat = inc.get("sat_like")
    exc_sat = exc.get("sat_like")

    # If preprocessing is enabled, override the solver's eligibility verdict
    # by evaluating the CF chart's mined values against the PRUNED exc SMT
    # (with inc-exc-conflicting REQs removed). Inc side is unchanged.
    removed_conflict_labels: list = []
    if PREPROCESS_INC_EXC_CONFLICTS:
        inc_prog_orig = "\n".join((inc.get("raw") or {}).get("smt_program_lines") or [])
        exc_prog_orig = "\n".join((exc.get("raw") or {}).get("smt_program_lines") or [])
        pruned_inc, pruned_exc, removed_conflict_labels = preprocess_trial_smt(
            inc_prog_orig, exc_prog_orig)
        if removed_conflict_labels:
            # Re-evaluate using pruned exc program
            exc_pvv = (exc.get("raw") or {}).get("patient_var_values") or {}
            exc_vindex = (exc.get("raw") or {}).get("variable_index") or {}
            pruned_status = _evaluate_pruned_smt(pruned_exc, exc_pvv, exc_vindex)
            exc_sat = True if pruned_status == "sat" else (
                False if pruned_status == "unsat" else None)

    eligible = (inc_sat is not False) and (exc_sat is not False)

    # Check whether each target variable was actually re-extracted as requested
    new_pvv_inc = (inc.get("raw") or {}).get("patient_var_values") or {}
    new_pvv_exc = (exc.get("raw") or {}).get("patient_var_values") or {}
    new_pvv_all = {**new_pvv_inc, **new_pvv_exc}
    target_outcome = {}
    for var, target_val in targets.items():
        new_val = new_pvv_all.get(var)
        if isinstance(new_val, dict):
            new_val = new_val.get("value")
        target_outcome[var] = {
            "target": target_val,
            "miner_re_extracted": new_val,
            "correctly_set": (str(new_val).lower() == str(target_val).lower())
                              or (new_val == target_val),
        }

    correct_extractions = sum(1 for v in target_outcome.values() if v["correctly_set"])

    result = {
        "pair": pair,
        "unsat_labels": unsat_labels[:10],
        "core_assertion_count": core_assertion_count,
        "targets": targets,
        "target_outcome": target_outcome,
        "n_targets": len(targets),
        "n_correctly_re_extracted": correct_extractions,
        "original_chart": chart,
        "minimal_cf_chart": cf_chart,
        "original_decision": "ineligible",
        "cf_decision": "eligible" if eligible else "ineligible",
        "flipped": eligible,
        "preprocessing_applied": PREPROCESS_INC_EXC_CONFLICTS,
        "removed_conflict_labels": removed_conflict_labels if PREPROCESS_INC_EXC_CONFLICTS else [],
        "cf_inc_sat_like": inc_sat,
        "cf_exc_sat_like": exc_sat,
    }
    pd_out = out_dir / pair
    pd_out.mkdir(parents=True, exist_ok=True)
    (pd_out / "result.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def main() -> None:
    data_root = pathlib.Path("/tmp/satir_full_dataset")
    endpoint = os.environ.get("OPENAI_ENDPOINT")
    engine = AzureInferenceEngine(
        endpoint=endpoint, api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-4.1", default_temperature=0.0,
    )
    cfg = Config()

    cf_path = os.environ.get("CF_CANDIDATES", "/tmp/counterfactual_candidates.json")
    candidates = json.load(open(cf_path))
    print(f"SatIR MINIMAL UNSAT-CORE CF probe on {len(candidates)} pairs → {OUT}", file=sys.stderr)

    results = []
    for i, c in enumerate(candidates, 1):
        try:
            r = process_one(c, engine, cfg, data_root, OUT)
            results.append(r)
            if r.get("error"):
                print(f"  [{i}/{len(candidates)}] {c['pair']}: ERROR {r['error']}", file=sys.stderr)
            else:
                print(f"  [{i}/{len(candidates)}] {r['pair']}: "
                      f"targets={r['n_targets']} "
                      f"correctly_extracted={r['n_correctly_re_extracted']}/{r['n_targets']} "
                      f"cf_decision={r['cf_decision']} flipped={r['flipped']}",
                      file=sys.stderr)
            (OUT / "all_results.json").write_text(json.dumps(results, indent=2, default=str))
        except Exception as e:
            print(f"  FAIL {c['pair']}: {type(e).__name__}: {e}", file=sys.stderr)

    # Summary
    valid = [r for r in results if "flipped" in r]
    flipped = [r for r in valid if r["flipped"]]
    total_t = sum(r["n_targets"] for r in valid)
    total_c = sum(r["n_correctly_re_extracted"] for r in valid)
    print()
    if valid:
        print(f"SatIR MINIMAL CF (n={len(valid)}):")
        print(f"  Flip rate: {len(flipped)}/{len(valid)} = {len(flipped)/len(valid):.1%}")
        print(f"  Target re-extraction rate: {total_c}/{total_t} = {total_c/total_t:.1%}" if total_t else "  (no targets)")
        print()
        print("Expected under '100% by construction' + correct miner: ~100%.")
        print("Deviation quantifies (a) LLM's core-assertion parsing accuracy,")
        print("(b) miner's re-extraction accuracy on the CF chart.")


if __name__ == "__main__":
    main()
