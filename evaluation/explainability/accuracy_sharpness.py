#!/usr/bin/env python3
"""
Judge-adjudicated accuracy + sharpness rubric.

For each (patient, trial) pair with verbalized+destyled rationales cached in
--in-dir, this script:
  1. Loads patient note, trial text, and 3 (decision, destyled_rationale) tuples.
  2. Randomly shuffles the 3 systems into slots A/B/C (seeded).
  3. Prompts a judge (GPT-5 by default) with the accuracy_and_sharpness prompt.
  4. Parses {independent_verdict, per-candidate decision_rating, sharpness × 4}.
  5. Unscrambles back to per-system scores.

Writes per-pair JSON + aggregate summary.

Usage:
  set -a; source .env; set +a
  python -m evaluation.explainability.accuracy_sharpness \\
      --in-dir evaluation/results/verbalize_judge_235 \\
      --out-dir evaluation/results/accuracy_sharpness \\
      --judge gpt5 --data-root /tmp/satir_full_dataset --max-workers 6
"""
from __future__ import annotations
import argparse, json, os, pathlib, random, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MEVAL_PROMPTS = ROOT / "sql_retrieval" / "meval" / "prompts"
P_PROMPT = MEVAL_PROMPTS / "accuracy_and_sharpness.prompt"


def _fill(tmpl: str, subs: Dict[str, str]) -> str:
    for k, v in subs.items():
        tmpl = tmpl.replace(k, v)
    return tmpl


def _unwrap(out: Any) -> str:
    if isinstance(out, list) and out:
        return out[0] if isinstance(out[0], str) else str(out[0])
    return out if isinstance(out, str) else str(out)


def _call_with_retry(engine, prompt: str, *, tag: str = "judge", tries: int = 2) -> str:
    last = None
    for i in range(tries):
        try:
            return _unwrap(engine(prompt, temperature=0.0))
        except Exception as e:
            last = e
            time.sleep(2 + i * 2)
    raise RuntimeError(f"[{tag}] failed: {last}")


def _parse_response(raw: str) -> Optional[Dict[str, Any]]:
    # Bracket-counter to find balanced JSON object (Python re can't recurse)
    depth = 0
    start = None
    for i, c in enumerate(raw):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(raw[start:i+1])
                except Exception:
                    pass
                start = None
    return None


def load_patient_notes(data_root: pathlib.Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for sub in ("sigir", "trec-2021", "trec-2022"):
        q = data_root / sub / "queries.jsonl"
        if q.exists():
            for line in q.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                    out[o.get("_id") or ""] = o.get("text") or ""
                except Exception:
                    pass
    return out


def load_trial_texts(data_root: pathlib.Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for sub in ("sigir", "trec-2021", "trec-2022"):
        c = data_root / sub / "corpus.jsonl"
        if c.exists():
            for line in c.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                    tid = o.get("_id") or ""
                    parts = [o.get("title") or "", o.get("text") or ""]
                    meta = o.get("metadata") or {}
                    if isinstance(meta, dict):
                        for k in ("inclusion_criteria", "exclusion_criteria", "brief_summary"):
                            if meta.get(k):
                                parts.append(f"{k}:\n{meta[k]}")
                    out[tid] = "\n\n".join(p for p in parts if p)
                except Exception:
                    pass
    return out


def _label_from_decision(path: pathlib.Path) -> str:
    try:
        d = json.loads(path.read_text())
    except Exception:
        return "unknown"
    if "eligible" in d:
        e = d["eligible"]
    elif "aggregate" in d:
        e = (d.get("aggregate") or {}).get("eligible")
    elif "result" in d:
        e = (d.get("result") or {}).get("eligible")
    else:
        e = None
    return "eligible" if e is True else "ineligible" if e is False else "unknown"


def process_pair(pair_dir: pathlib.Path, *, patient_note: str, trial_text: str,
                 template: str, engine, seed: int,
                 out_pair_dir: pathlib.Path) -> Dict[str, Any]:
    pair_name = pair_dir.name
    out_pair_dir.mkdir(parents=True, exist_ok=True)

    # Load decisions + destyled rationales
    candidates: Dict[str, Dict[str, str]] = {}
    for sys_key in ("smt", "llm_direct", "trialgpt"):
        dec_path = pair_dir / f"{sys_key}_decision.json"
        destyled = pair_dir / f"{sys_key}_rationale_destyled.txt"
        raw_rat = pair_dir / f"{sys_key}_rationale.txt"
        if not dec_path.exists():
            continue
        rat_text = ""
        if destyled.exists():
            rat_text = destyled.read_text(encoding="utf-8")
        elif raw_rat.exists():
            rat_text = raw_rat.read_text(encoding="utf-8")
        candidates[sys_key] = {
            "decision": _label_from_decision(dec_path),
            "rationale": rat_text.strip(),
        }

    if len(candidates) < 3:
        return {"pair": pair_name, "skipped": True,
                "reason": f"only {len(candidates)} candidates found"}

    # Shuffle seeded per-pair
    rng = random.Random(seed + hash(pair_name) % 10**7)
    order = list(candidates.keys())
    rng.shuffle(order)
    slot_to_sys = dict(zip(["A", "B", "C"], order))
    # inverse map
    sys_to_slot = {v: k for k, v in slot_to_sys.items()}

    # Build prompt
    subs = {
        "#PATIENT_NOTE#": patient_note[:6000],
        "#TRIAL_TEXT#": trial_text[:8000],
    }
    for slot in ("A", "B", "C"):
        sk = slot_to_sys[slot]
        subs[f"#DECISION_{slot}#"] = candidates[sk]["decision"]
        subs[f"#RATIONALE_{slot}#"] = candidates[sk]["rationale"]
    prompt = _fill(template, subs)

    raw = _call_with_retry(engine, prompt, tag=pair_name)
    parsed = _parse_response(raw)

    if not parsed:
        return {"pair": pair_name, "skipped": True,
                "reason": "parse failure", "raw_preview": raw[:500]}

    # Unscramble: move ratings from A/B/C -> system
    per_system: Dict[str, Any] = {}
    for slot, sk in slot_to_sys.items():
        r = (parsed.get("ratings") or {}).get(slot) or {}
        per_system[sk] = {
            "decision": candidates[sk]["decision"],
            "decision_correct": r.get("decision_correct"),
            "decision_rating": r.get("decision_rating"),
            "sharpness": r.get("sharpness") or {},
            "brief_comment": r.get("brief_comment", ""),
        }

    result = {
        "pair": pair_name,
        "slot_mapping": slot_to_sys,
        "judge_verdict": parsed.get("independent_verdict"),
        "judge_reasoning": parsed.get("independent_reasoning"),
        "per_system": per_system,
        "raw_preview": raw[:800],
    }
    (out_pair_dir / "accuracy_sharpness.json").write_text(
        json.dumps(result, indent=2, default=str))
    return result


def aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    systems = ("smt", "llm_direct", "trialgpt")
    n_valid = sum(1 for r in results if not r.get("skipped"))
    out: Dict[str, Any] = {"n_pairs": n_valid, "skipped": len(results) - n_valid}

    # Accuracy: system decision == judge verdict
    acc: Dict[str, Dict[str, int]] = {s: {"match": 0, "total": 0} for s in systems}
    for r in results:
        if r.get("skipped"):
            continue
        jv = r.get("judge_verdict", "").lower()
        if jv not in ("eligible", "ineligible"):
            continue
        for s, d in r["per_system"].items():
            if s not in acc:
                continue
            acc[s]["total"] += 1
            if d["decision"] == jv:
                acc[s]["match"] += 1
    out["accuracy"] = {s: {
        "rate": (v["match"] / v["total"] if v["total"] else None),
        "match": v["match"], "total": v["total"]
    } for s, v in acc.items()}

    # Decision rating (1-5)
    rating_sum: Dict[str, List[float]] = {s: [] for s in systems}
    for r in results:
        if r.get("skipped"):
            continue
        for s, d in r["per_system"].items():
            if s not in rating_sum:
                continue
            v = d.get("decision_rating")
            if isinstance(v, (int, float)):
                rating_sum[s].append(float(v))
    out["mean_decision_rating"] = {s: (sum(v) / len(v) if v else None) for s, v in rating_sum.items()}

    # Sharpness (4 or 5 axes — logical_consistency is optional)
    axes = ("decisiveness", "evidence_specificity", "conciseness", "actionability",
            "logical_consistency")
    sharp: Dict[str, Dict[str, List[float]]] = {s: {a: [] for a in axes} for s in systems}
    for r in results:
        if r.get("skipped"):
            continue
        for s, d in r["per_system"].items():
            if s not in sharp:
                continue
            sh = d.get("sharpness") or {}
            for a in axes:
                v = sh.get(a)
                if isinstance(v, (int, float)):
                    sharp[s][a].append(float(v))
    out["mean_sharpness"] = {
        s: {a: (sum(v) / len(v) if v else None) for a, v in axes_dict.items()}
        for s, axes_dict in sharp.items()
    }

    return out


def build_engine(judge_kind: str):
    if judge_kind == "gpt5":
        from smt_core.inference_engine_5 import AzureInferenceEngine as Eng
        ep = os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ.get("OPENAI_ENDPOINT")
        if not ep:
            print("FATAL: OPENAI_ENDPOINT_GPT5 not set", file=sys.stderr); sys.exit(2)
        return Eng(endpoint=ep, api_key_env_var="OPENAI_API_KEY",
                   model_name="gpt-5", default_temperature=0.0)
    elif judge_kind == "gpt4":
        from smt_core.inference_engine import AzureInferenceEngine as Eng
        ep = os.environ.get("OPENAI_ENDPOINT")
        return Eng(endpoint=ep, api_key_env_var="OPENAI_API_KEY",
                   model_name="gpt-4.1", default_temperature=0.0)
    elif judge_kind == "claude":
        import anthropic
        k = os.environ.get("ANTHROPIC_API_KEY")
        if not k:
            print("FATAL: ANTHROPIC_API_KEY not set (need real Anthropic API key)",
                  file=sys.stderr); sys.exit(2)
        client = anthropic.Anthropic(api_key=k, base_url=os.environ.get("ANTHROPIC_BASE_URL") or None)
        def call(prompt: str, temperature: float = 0.0):
            resp = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2000, temperature=temperature,
                messages=[{"role": "user", "content": prompt}])
            return resp.content[0].text
        class Wrap:
            def __call__(self, prompt, temperature=0.0): return call(prompt, temperature)
        return Wrap()
    raise ValueError(f"unknown judge: {judge_kind}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", default="evaluation/results/verbalize_judge_235")
    ap.add_argument("--out-dir", default=f"evaluation/results/accuracy_sharpness_{int(time.time())}")
    ap.add_argument("--data-root", default="/tmp/satir_full_dataset")
    ap.add_argument("--judge", default="gpt5", choices=("gpt5", "gpt4", "claude"))
    ap.add_argument("--max-workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--sample", type=int, default=0, help="limit to first N pairs (0=all)")
    ap.add_argument("--prompt", default=str(P_PROMPT), help="path to judge prompt template")
    args = ap.parse_args()

    in_dir = pathlib.Path(args.in_dir).resolve()
    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    template = pathlib.Path(args.prompt).read_text(encoding="utf-8")

    data_root = pathlib.Path(args.data_root)
    notes = load_patient_notes(data_root)
    trials = load_trial_texts(data_root)
    print(f"Loaded {len(notes)} notes, {len(trials)} trials", file=sys.stderr)

    # Discover pair dirs (support shard layout)
    pair_dirs = sorted([p for p in in_dir.glob("*/*") if p.is_dir() and "__" in p.name])
    if not pair_dirs:
        pair_dirs = sorted([p for p in in_dir.iterdir() if p.is_dir() and "__" in p.name])
    if args.sample > 0:
        pair_dirs = pair_dirs[: args.sample]
    print(f"Found {len(pair_dirs)} pair dirs", file=sys.stderr)
    if not pair_dirs:
        sys.exit(2)

    engine = build_engine(args.judge)
    print(f"Judge: {args.judge}", file=sys.stderr)

    def _task(pd: pathlib.Path):
        pid, tid = pd.name.split("__", 1)
        note = notes.get(pid, "")
        trial = trials.get(tid, "")
        out_pair = out_dir / pd.parent.name / pd.name if pd.parent.name.startswith("shard_") else out_dir / pd.name
        try:
            return process_pair(pd, patient_note=note, trial_text=trial,
                                template=template, engine=engine,
                                seed=args.seed, out_pair_dir=out_pair)
        except Exception as e:
            return {"pair": pd.name, "skipped": True, "reason": f"{type(e).__name__}: {e}"}

    t0 = time.perf_counter()
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futs = [pool.submit(_task, pd) for pd in pair_dirs]
        for i, f in enumerate(as_completed(futs), 1):
            results.append(f.result())
            if i % max(1, len(pair_dirs) // 20) == 0 or i == len(pair_dirs):
                print(f"  {i}/{len(pair_dirs)} pairs ({time.perf_counter()-t0:.1f}s)",
                      file=sys.stderr)

    agg = aggregate(results)
    (out_dir / "summary.json").write_text(json.dumps({
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "args": vars(args),
        "aggregate": agg,
        "per_pair": results,
    }, indent=2, default=str))
    print(f"\n=== Summary ===")
    print(json.dumps(agg, indent=2, default=str))
    print(f"\nSaved to {out_dir / 'summary.json'}")


if __name__ == "__main__":
    main()
