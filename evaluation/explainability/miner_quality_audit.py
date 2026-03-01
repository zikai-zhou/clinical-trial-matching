"""GPT-5 vs GPT-4.1 miner quality audit (no solver).

For each v3 failure pair:
  1. Load the trial's variable_index (from the pair's smt_decision.json).
  2. Build the miner prompt using those variables + the patient chart.
  3. Run the miner with GPT-5 (the alternate miner backend).
  4. Compare the new mined values to the original GPT-4.1 mined values.

Signal:
  - Same values = failure is data-ambiguity bound (even a stronger miner makes
    the same call).
  - Different values on load-bearing variables = failure is miner-ceiling bound
    (the stronger miner might reach the correct solver output).

Usage:
    python -m evaluation.explainability.miner_quality_audit
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine_5 import AzureInferenceEngine as Engine5  # noqa: E402
from evaluation.explainability.run_verbalize_judge import (  # noqa: E402
    load_patient_from_queries, load_trial_from_corpus,
)

RESULTS = ROOT / "evaluation" / "results"
V3 = RESULTS / "verbalize_judge_235_v3"
OUT = RESULTS / "miner_quality_audit"
OUT.mkdir(parents=True, exist_ok=True)

MINER_PROMPT = ROOT / "smt_matcher" / "prompts" / "clinical_trial" / "SMTMatcher" / "SMTVariableValueMinerInclusion.prompt"


def build_miner_prompt(template: str, var_index: dict, chart: str) -> str:
    """Build a simplified standalone miner prompt. Not identical to the full
    SatIR miner (which iterates per-side etc.), but approximates a single-shot
    miner query over all variables for comparison purposes."""
    var_list = "\n".join(
        f"- {k}: {v.get('type', '?')} — {v.get('description','')[:200]}"
        for k, v in list(var_index.items())[:60]  # cap to limit context
    )
    # Use the template's tokens; fall back to a generic wrapper if placeholders
    # don't match.
    prompt = template
    replacements = {
        "#PATIENT_NOTE#": chart[:4000],
        "#PATIENT_VIGNETTE#": chart[:4000],
        "#VARIABLES#": var_list,
        "#VARIABLE_INDEX#": var_list,
        "#CLINICAL_TRIAL_DESCRIPTION#": "(see variables)",
    }
    for k, v in replacements.items():
        prompt = prompt.replace(k, v)
    # If the original template still has unfilled tokens, append a fallback
    # instruction.
    if "#" in prompt and "{" not in prompt:
        prompt += "\n\nPatient note:\n" + chart[:4000]
        prompt += "\n\nVariables:\n" + var_list
        prompt += '\n\nReturn a JSON object mapping each variable name to its value (or null if the chart is silent).'
    return prompt


def load_failures():
    fails = json.load(open(RESULTS / "smt_failure_audit_v3.json"))
    # restrict to mining-localized ones
    return [f for f in fails if f.get("root_cause") == "MINING_WRONG"][:20]


def one_pair(engine5, failure, data_root, template):
    pair = failure["pair"]
    pid, tid = pair.split("__")
    patient = load_patient_from_queries(pid, data_root / "sigir" / "queries.jsonl")
    if not patient:
        return {"pair": pair, "error": "no patient"}
    chart = patient.get("text", "") or patient.get("content", "")

    # Find pair dir
    pd = None
    for shard in V3.glob("shard_*"):
        cand = shard / pair
        if cand.exists():
            pd = cand; break
    if pd is None:
        return {"pair": pair, "error": "no pair_dir"}
    smt_dec = json.load(open(pd / "smt_decision.json"))
    inc_raw = (smt_dec.get("inclusion") or {}).get("raw") or {}
    var_index = inc_raw.get("variable_index") or {}
    original_mined = inc_raw.get("patient_var_values") or {}

    if not var_index:
        return {"pair": pair, "error": "no var_index"}

    prompt = build_miner_prompt(template, var_index, chart)
    raw = engine5(prompt, temperature=0.0)
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    # Try to extract JSON from raw
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    gpt5_mined = {}
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                gpt5_mined = obj
        except Exception:
            pass

    # Compare: for each variable in the original, did GPT-5 produce the same value?
    differences = []
    for k, orig_v in original_mined.items():
        orig_val = orig_v.get("value") if isinstance(orig_v, dict) else orig_v
        new_val = gpt5_mined.get(k)
        if isinstance(new_val, dict):
            new_val = new_val.get("value")
        # Normalize None/null strings
        def norm(v):
            if v is None: return None
            if isinstance(v, str) and v.strip().lower() in {"null", "none", ""}: return None
            return v
        o, n = norm(orig_val), norm(new_val)
        if o != n:
            differences.append({"var": k, "gpt4.1": o, "gpt5": n})

    return {
        "pair": pair,
        "explanation": failure.get("explanation", "")[:200],
        "n_vars": len(var_index),
        "n_differences": len(differences),
        "differences": differences[:10],  # cap output
    }


def main() -> None:
    data_root = pathlib.Path("/tmp/satir_full_dataset")
    endpoint = os.environ.get("OPENAI_ENDPOINT_GPT5")
    if not endpoint:
        print("FATAL: OPENAI_ENDPOINT_GPT5 must be set", file=sys.stderr)
        sys.exit(2)

    engine5 = Engine5(
        endpoint=endpoint, api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-5", default_temperature=0.0,
    )
    template = MINER_PROMPT.read_text(encoding="utf-8")

    failures = load_failures()
    print(f"Miner-quality audit on {len(failures)} MINING_WRONG failures", file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(one_pair, engine5, f, data_root, template): f for f in failures}
        for i, fut in enumerate(as_completed(futs), 1):
            f = futs[fut]
            try:
                r = fut.result()
                results.append(r)
                nd = r.get("n_differences", "?")
                nv = r.get("n_vars", "?")
                print(f"  [{i}/{len(failures)}] {r['pair']}: {nd}/{nv} vars differ", file=sys.stderr)
            except Exception as e:
                print(f"  FAIL {f['pair']}: {type(e).__name__}: {e}", file=sys.stderr)

    (OUT / "results.json").write_text(json.dumps(results, indent=2))

    # Summary
    with_diffs = [r for r in results if r.get("n_differences", 0) > 0]
    total_vars = sum(r.get("n_vars", 0) for r in results)
    total_diffs = sum(r.get("n_differences", 0) for r in results)
    print()
    print(f"Summary: {len(with_diffs)}/{len(results)} pairs show miner disagreement")
    print(f"Total variable disagreements: {total_diffs}/{total_vars}")
    print()
    print("Interpretation:")
    print("  If GPT-5 mines differently on load-bearing variables, a miner upgrade")
    print("  could recover the failure. If GPT-5 mines identically, the failure is")
    print("  chart-ambiguity-bound.")


if __name__ == "__main__":
    main()
