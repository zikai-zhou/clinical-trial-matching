"""Two-pass LLM judge design: controls for rationale leakage.

Stage 1: GPT-5 judge sees ONLY patient note + trial criteria, produces
         an independent verdict (no rationales visible).
Stage 2: For each pair, compare each system's stored decision to the
         stage-1 verdict. Accuracy = agreement rate.

This decouples "is the decision right" from "is the rationale convincing".
Under this design, accuracy numbers should be more robust to verbalizer
content than the original single-pass judge.

Usage:
    python -m evaluation.explainability.twopass_judge
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

from smt_core.inference_engine_5 import AzureInferenceEngine  # noqa: E402

# Inline loaders to avoid z3 chain
def load_patient_from_queries(patient_id, queries_path):
    if not queries_path.exists(): return None
    with open(queries_path, "r") as f:
        for line in f:
            try: obj = json.loads(line)
            except: continue
            if obj.get("_id") == patient_id:
                return {"patient_id": patient_id, "_id": patient_id,
                        "text": obj.get("text") or "", "metadata": obj.get("metadata") or {}}
    return None

def load_trial_from_corpus(trial_id, corpus_path):
    if not corpus_path.exists(): return None
    with open(corpus_path, "r") as f:
        for line in f:
            try: obj = json.loads(line)
            except: continue
            if obj.get("_id") == trial_id:
                md = obj.get("metadata") or {}
                incl = md.get("inclusion_criteria") or ""
                excl = md.get("exclusion_criteria") or ""
                criteria = (incl + "\n\nExclusion:\n" + excl) if incl or excl else (obj.get("text") or "")
                return {"nct_id": obj.get("_id"), "criteria": criteria,
                        "inclusion_criteria": incl, "exclusion_criteria": excl,
                        "metadata": md}
    return None

RESULTS = ROOT / "evaluation" / "results"
V3 = RESULTS / "verbalize_judge_235_v3"
OUT = RESULTS / "twopass_judge"
PROMPT = ROOT / "sql_retrieval" / "meval" / "prompts" / "accuracy_and_sharpness_twopass_stage1.prompt"


def main() -> None:
    data_root = pathlib.Path("/tmp/satir_full_dataset")
    endpoint = os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ.get("OPENAI_ENDPOINT")
    if not endpoint:
        print("FATAL: OPENAI_ENDPOINT_GPT5 must be set", file=sys.stderr)
        sys.exit(2)

    engine = AzureInferenceEngine(
        endpoint=endpoint, api_key_env_var="OPENAI_API_KEY",
        model_name="gpt-5", default_temperature=0.0,
    )

    template = PROMPT.read_text(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)

    # Enumerate all pairs
    pairs = []
    for shard in sorted(V3.glob("shard_*")):
        for pair_dir in sorted(shard.iterdir()):
            if pair_dir.is_dir() and "__" in pair_dir.name:
                pairs.append(pair_dir)

    print(f"Two-pass judge on {len(pairs)} pairs", file=sys.stderr)

    def one(pair_dir):
        pair = pair_dir.name
        pid, tid = pair.split("__")
        patient = load_patient_from_queries(pid, data_root / "sigir" / "queries.jsonl")
        trial = load_trial_from_corpus(tid, data_root / "sigir" / "corpus.jsonl")
        if not patient or not trial:
            return {"pair": pair, "error": "missing data"}
        chart = patient.get("text", "") or patient.get("content", "")
        trial_text = trial.get("criteria") or trial.get("eligibility") or trial.get("text") or ""

        prompt = (template
                  .replace("#PATIENT_NOTE#", chart[:6000])
                  .replace("#TRIAL_TEXT#", trial_text[:8000]))

        raw = engine(prompt, temperature=0.0)
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        parsed = {}
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = {"raw": raw}

        # Read each system's stored decision
        smt_dec = json.load(open(pair_dir / "smt_decision.json")) if (pair_dir / "smt_decision.json").exists() else {}
        llm_dec = json.load(open(pair_dir / "llm_direct_decision.json")) if (pair_dir / "llm_direct_decision.json").exists() else {}
        tg_dec = json.load(open(pair_dir / "trialgpt_decision.json")) if (pair_dir / "trialgpt_decision.json").exists() else {}

        def lbl(e):
            return "eligible" if e is True else ("ineligible" if e is False else "unknown")

        return {
            "pair": pair,
            "judge_verdict": parsed.get("independent_verdict", "unknown"),
            "judge_reasoning": parsed.get("independent_reasoning", ""),
            "smt_decision": lbl(smt_dec.get("eligible")),
            "llm_direct_decision": lbl((llm_dec.get("result") or {}).get("eligible")),
            "trialgpt_decision": lbl((tg_dec.get("aggregate") or {}).get("eligible")),
        }

    results = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(one, p): p for p in pairs}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                r = fut.result()
                results.append(r)
                if i % 20 == 0:
                    print(f"  [{i}/{len(pairs)}]", file=sys.stderr)
            except Exception as e:
                print(f"  FAIL {futs[fut].name}: {type(e).__name__}: {e}", file=sys.stderr)

    (OUT / "judge_results.json").write_text(json.dumps(results, indent=2))

    # Aggregate accuracy per system
    systems = ["smt", "llm_direct", "trialgpt"]
    agree = {s: 0 for s in systems}
    total = 0
    for r in results:
        if r.get("error"):
            continue
        if r["judge_verdict"] not in ("eligible", "ineligible"):
            continue
        total += 1
        for s in systems:
            if r[f"{s}_decision"] == r["judge_verdict"]:
                agree[s] += 1
    print()
    print("Two-pass accuracy (judge sees ONLY chart + trial, no rationales):")
    for s in systems:
        print(f"  {s:<12}  {agree[s]}/{total} = {agree[s]/total:.3f}" if total else f"  {s}: n=0")


if __name__ == "__main__":
    main()
