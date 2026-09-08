"""Classify SatIR's failure root causes via per-case LLM audit.

Loads v3 failure list (produced by refresh_failure_audit.py) and for each pair
builds an audit prompt showing the patient note, trial criteria, SatIR's
decision + mined values, and the judge's verdict. A GPT-4.1 call classifies
the root cause as MINING_WRONG / PARSING / SEMANTIC / JUDGE_WRONG.

Usage:
    python -m evaluation.explainability.classify_failures_v3
"""
from __future__ import annotations
import json
import pathlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine import AzureInferenceEngine  # noqa: E402

RESULTS = ROOT / "evaluation" / "results"
V3 = RESULTS / "verbalize_judge_235_v3"


CLASSIFIER_PROMPT = """You are auditing a clinical trial matching system (SatIR) that uses an LLM
variable miner plus a Z3 symbolic solver. A given eligibility decision is wrong
— we want to identify the root cause.

SatIR has three layers:
  1. MINING: an LLM extracts patient facts into typed variables (bool/real/int).
     Possible failures: under-extraction (returned null when chart has evidence),
     wrong value, miss implicit inference, miss negation, miss qualitative finding.
  2. PARSING: trial criteria were compiled to SMT constraints. Possible failures:
     criterion compiled too strict, or a clinically inappropriate interpretation.
  3. SEMANTIC: solver would have to be wrong (this essentially never happens;
     Z3 is deterministic).

Additionally, JUDGE_WRONG: SatIR's decision is actually correct, but the judge
disagreed (rare but possible).

You will be given:
- Patient chart
- Trial eligibility text
- SatIR's decision + rationale
- SatIR's concretely-mined variable values (non-null)
- Judge's independent verdict + reasoning
- Judge's brief comment on SatIR

Respond in strict JSON with these keys:
{
  "root_cause": "MINING_WRONG" | "PARSING" | "SEMANTIC" | "JUDGE_WRONG",
  "confidence": <0.0-1.0>,
  "explanation": "<1-2 sentences identifying the specific failure>",
  "mining_pattern": "<if MINING_WRONG: 'under_extraction' | 'wrong_value' | 'missed_inference' | 'missed_negation' | 'missed_qualitative' | 'other'; else null>"
}

=== INPUTS ===

## Patient chart
#PATIENT_NOTE#

## Trial eligibility text
#TRIAL_TEXT#

## SatIR's decision
#SATIR_DECISION#

## SatIR's rationale
#SATIR_RATIONALE#

## Concretely-mined variable values (non-null)
#MINED_VALUES#

## Judge verdict
#JUDGE_VERDICT#

## Judge reasoning
#JUDGE_REASONING#

## Judge comment on SatIR
#JUDGE_COMMENT#

Return STRICT JSON only.
"""


def load_v3_failures():
    summary = json.load(open(RESULTS / "accuracy_sharpness_235_v3_clinician/summary.json"))
    out = []
    for p in summary.get("per_pair", []):
        per = p.get("per_system", {}).get("smt", {}) or {}
        if per.get("decision_correct") != "no":
            continue
        out.append({
            "pair": p.get("pair"),
            "judge_verdict": p.get("judge_verdict"),
            "judge_reasoning": p.get("judge_reasoning", ""),
            "satir_decision": per.get("decision"),
            "judge_comment": per.get("brief_comment", ""),
        })
    return out


def load_pair_context(pair: str) -> dict:
    for shard in V3.glob("shard_*"):
        pd = shard / pair
        if pd.exists():
            break
    else:
        return {}
    try:
        smt_dec = json.load(open(pd / "smt_decision.json"))
        rationale = (pd / "smt_rationale.txt").read_text(encoding="utf-8") if (pd / "smt_rationale.txt").exists() else ""
        # mined values
        mv = {}
        for side in ("inclusion", "exclusion"):
            raw = (smt_dec.get(side) or {}).get("raw") or {}
            pvv = raw.get("patient_var_values") or {}
            for k, v in pvv.items():
                val = v.get("value") if isinstance(v, dict) else v
                if val is None:
                    continue
                if isinstance(val, str) and val.strip().lower() in {"null", "none", ""}:
                    continue
                mv[k] = val
        return {"smt_dec": smt_dec, "rationale": rationale, "mined_values": mv}
    except Exception:
        return {}


def load_patient_note(pid: str, data_root: pathlib.Path) -> str:
    for sub in ("sigir2014_1", "sigir2014_2", "sigir2014_3", "sigir"):
        p = data_root / sub / "patients" / f"{pid}.txt"
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def load_trial_text(tid: str, data_root: pathlib.Path) -> str:
    for sub in ("sigir2014_1", "sigir2014_2", "sigir2014_3", "sigir"):
        p = data_root / sub / "trials" / f"{tid}.json"
        if p.exists():
            d = json.load(open(p))
            return d.get("criteria") or d.get("eligibility") or d.get("text") or ""
    return ""


def classify_one(engine, failure: dict, data_root: pathlib.Path) -> dict:
    pair = failure["pair"]
    pid, tid = pair.split("__")
    ctx = load_pair_context(pair)
    note = load_patient_note(pid, data_root)
    trial = load_trial_text(tid, data_root)
    mv = ctx.get("mined_values", {})
    rationale = ctx.get("rationale", "")

    subs = {
        "#PATIENT_NOTE#": note[:3000],
        "#TRIAL_TEXT#": trial[:5000],
        "#SATIR_DECISION#": str(failure["satir_decision"]),
        "#SATIR_RATIONALE#": rationale[:500],
        "#MINED_VALUES#": json.dumps(mv, indent=2, default=str)[:3000],
        "#JUDGE_VERDICT#": str(failure["judge_verdict"]),
        "#JUDGE_REASONING#": failure["judge_reasoning"],
        "#JUDGE_COMMENT#": failure["judge_comment"],
    }
    prompt = CLASSIFIER_PROMPT
    for k, v in subs.items():
        prompt = prompt.replace(k, v)
    raw = engine(prompt, temperature=0.0)
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    parsed = {}
    if m:
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            pass
    return {
        "pair": pair,
        "judge_verdict": failure["judge_verdict"],
        "satir_decision": failure["satir_decision"],
        **parsed,
    }


def main() -> None:
    import os
    import csv

    failures = load_v3_failures()
    print(f"V3 failures to classify: {len(failures)}", file=sys.stderr)

    data_root = pathlib.Path("/tmp/satir_full_dataset")
    endpoint = os.environ.get("OPENAI_ENDPOINT")
    if not endpoint:
        print("FATAL: OPENAI_ENDPOINT must be set", file=sys.stderr)
        sys.exit(2)
    engine = AzureInferenceEngine(
        endpoint=endpoint,
        api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-4.1",
        default_temperature=0.0,
    )

    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(classify_one, engine, f, data_root): f for f in failures}
        for i, fut in enumerate(as_completed(futs), 1):
            f = futs[fut]
            try:
                r = fut.result()
                results.append(r)
                rc = r.get("root_cause", "?")
                print(f"  [{i}/{len(failures)}] {r['pair']}: {rc}", file=sys.stderr)
            except Exception as e:
                print(f"  FAIL {f['pair']}: {type(e).__name__}: {e}", file=sys.stderr)
                results.append({"pair": f["pair"], "root_cause": "ERROR", "error": str(e)})

    # Write outputs
    out_json = RESULTS / "smt_failure_audit_v3.json"
    out_json.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {out_json}", file=sys.stderr)

    out_csv = RESULTS / "smt_failure_audit_v3.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pair", "judge_verdict", "satir_decision", "root_cause",
                    "mining_pattern", "confidence", "explanation"])
        for r in sorted(results, key=lambda x: x["pair"]):
            w.writerow([
                r.get("pair", ""),
                r.get("judge_verdict", ""),
                r.get("satir_decision", ""),
                r.get("root_cause", ""),
                r.get("mining_pattern", "") or "",
                r.get("confidence", ""),
                r.get("explanation", ""),
            ])
    print(f"Wrote {out_csv}", file=sys.stderr)

    # Summarize
    from collections import Counter
    cats = Counter(r.get("root_cause", "?") for r in results)
    patterns = Counter(r.get("mining_pattern") for r in results if r.get("mining_pattern"))
    n = len(results)
    print(f"\nSummary over {n} failures:")
    for k, v in cats.most_common():
        print(f"  {k:<18} {v:>3}  ({v/n:.1%})")
    if patterns:
        print(f"\nMining pattern breakdown:")
        for k, v in patterns.most_common():
            print(f"  {k:<24} {v:>3}")


if __name__ == "__main__":
    main()
