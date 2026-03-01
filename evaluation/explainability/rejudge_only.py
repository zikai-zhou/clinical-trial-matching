#!/usr/bin/env python3
"""
Re-run only the pairwise judge stage on an existing verbalize_judge output dir,
using a different judge prompt. Reuses cached destyled rationales + decisions.

Usage:
  set -a; source .env; set +a
  python -m evaluation.explainability.rejudge_only \
      --in-dir evaluation/results/verbalize_judge_scale \
      --out-dir evaluation/results/rejudge_prescreen \
      --judge-prompt sql_retrieval/meval/prompts/llm_adjudicate_disagreement_prescreen.prompt \
      --data-root /tmp/satir_full_dataset \
      --max-workers 6
"""
from __future__ import annotations
import argparse, json, os, pathlib, random, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine_5 import AzureInferenceEngine  # noqa: E402


def _fill(tmpl: str, subs: Dict[str, str]) -> str:
    for k, v in subs.items():
        tmpl = tmpl.replace(k, v)
    return tmpl


def _call_with_retry(engine, prompt: str, *, temperature: float = 0.0, tag: str = "judge", tries: int = 2) -> str:
    last = None
    for i in range(tries):
        try:
            out = engine(prompt, temperature=temperature)
            if isinstance(out, list):
                out = out[0] if out else ""
            return out
        except Exception as e:
            last = e
            time.sleep(2 + i * 2)
    raise RuntimeError(f"[{tag}] failed after {tries} tries: {last}")


def run_contest(engine, template: str, *, patient_note: str, trial_text: str,
                rat_A: str, rat_B: str, dec_A: str, dec_B: str) -> Dict[str, Any]:
    prompt = _fill(template, {
        "#PATIENT_NOTE#": patient_note,
        "#TRIAL_TEXT#": trial_text,
        "#TRIAL_ELIGIBILITY_TEXT#": trial_text,
        "#DECISION_A#": dec_A or "unknown", "#DECISION_B#": dec_B or "unknown",
        "#CANDIDATE_A_DECISION#": dec_A or "unknown", "#CANDIDATE_B_DECISION#": dec_B or "unknown",
        "#RATIONALE_A#": rat_A, "#RATIONALE_B#": rat_B,
        "#CANDIDATE_A_RATIONALE#": rat_A, "#CANDIDATE_B_RATIONALE#": rat_B,
    })
    raw = _call_with_retry(engine, prompt, tag="judge")
    parsed: Dict[str, Any] = {"raw": raw}
    m = re.search(r"<your_decision>\s*(\{.*?\})\s*</your_decision>", raw, flags=re.DOTALL)
    js = m.group(1) if m else None
    if js is None:
        m2 = re.search(r"\{[^{}]*\"decision_over_decisions\"[^{}]*\}", raw, flags=re.DOTALL)
        js = m2.group(0) if m2 else None
    if js:
        try:
            parsed.update(json.loads(re.sub(r",\s*}", "}", js)))
        except Exception as e:
            parsed["parse_error"] = str(e)
    return parsed


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
                    out[o.get("_id") or o.get("id") or ""] = o.get("text") or ""
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
                    tid = o.get("_id") or o.get("id") or ""
                    text_parts = [o.get("title", ""), o.get("text", "")]
                    meta = o.get("metadata") or {}
                    if isinstance(meta, dict):
                        for k in ("inclusion_criteria", "exclusion_criteria", "brief_summary"):
                            if k in meta and meta[k]:
                                text_parts.append(f"{k}:\n{meta[k]}")
                    out[tid] = "\n\n".join(p for p in text_parts if p)
                except Exception:
                    pass
    return out


def rejudge_pair(pair_dir: pathlib.Path, out_pair_dir: pathlib.Path, *,
                 engine, template: str, patient_note: str, trial_text: str,
                 seed: int) -> Dict[str, Any]:
    # Load cached destyled rationales
    def _read(name: str) -> str:
        p = pair_dir / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    smt_de = _read("smt_rationale_destyled.txt") or _read("smt_rationale.txt")
    llm_de = _read("llm_direct_rationale_destyled.txt") or _read("llm_direct_rationale.txt")
    tg_de = _read("trialgpt_rationale_destyled.txt") or _read("trialgpt_rationale.txt")

    # Load decisions from existing contests.json (reliable) or from *_decision.json
    old_contests = pair_dir / "contests.json"
    decisions = {}
    if old_contests.exists():
        cs = json.loads(old_contests.read_text())
        # Reconstruct decisions from one contest
        for c in cs:
            a_sys, b_sys = c["A_system"], c["B_system"]
            # We stored systems' decisions at the time via _label_from_eligible; we need them here.
            # Fall back to reading *_decision.json for reliability.
            break
    for sys_name in ("smt", "llm_direct", "trialgpt"):
        p = pair_dir / f"{sys_name}_decision.json"
        if p.exists():
            try:
                d = json.loads(p.read_text())
                e = d.get("eligible")
                decisions[sys_name] = ("eligible" if e is True else
                                        "ineligible" if e is False else "unknown")
            except Exception:
                decisions[sys_name] = "unknown"
        else:
            decisions[sys_name] = "unknown"

    if not (smt_de and llm_de and tg_de):
        return {"pair": pair_dir.name, "skipped": True, "reason": "missing rationale files"}

    out_pair_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed + hash(pair_dir.name) % 10**6)
    systems = {
        "smt": {"rationale": smt_de, "decision": decisions.get("smt", "unknown")},
        "llm_direct": {"rationale": llm_de, "decision": decisions.get("llm_direct", "unknown")},
        "trialgpt": {"rationale": tg_de, "decision": decisions.get("trialgpt", "unknown")},
    }
    pairs_list = [("smt", "llm_direct"), ("smt", "trialgpt"), ("llm_direct", "trialgpt")]
    contests = []
    for s1, s2 in pairs_list:
        a, b = (s1, s2) if rng.random() < 0.5 else (s2, s1)
        r = run_contest(engine, template,
                        patient_note=patient_note, trial_text=trial_text,
                        rat_A=systems[a]["rationale"], rat_B=systems[b]["rationale"],
                        dec_A=systems[a]["decision"], dec_B=systems[b]["decision"])
        dod = r.get("decision_over_decisions")
        winner = (a if dod == "A_wins" else
                  b if dod == "B_wins" else
                  dod)
        contests.append({
            "contest": f"{s1}_vs_{s2}",
            "A_system": a, "B_system": b,
            "judge_decision": dod, "winner": winner,
            "confidence": r.get("confidence"),
            "brief_rationale": r.get("brief_rationale"),
            "raw": r.get("raw"),
        })
    (out_pair_dir / "contests.json").write_text(json.dumps(contests, indent=2, default=str))
    return {"pair": pair_dir.name, "decisions": decisions, "contests": contests}


def aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    wins = Counter()
    ties = 0
    inconclusive = 0
    n = 0
    matchup: Dict[str, Counter] = {}
    for r in results:
        if r.get("skipped"):
            continue
        for c in r["contests"]:
            n += 1
            w = c["winner"]
            mu = matchup.setdefault(c["contest"], Counter())
            if w in ("smt", "llm_direct", "trialgpt"):
                wins[w] += 1; mu[w] += 1
            elif w in ("both_correct_missing_info_policy_difference",
                       "both_correct_clinical_interpretation_difference",
                       "both_incorrect"):
                ties += 1; mu["tie_or_other"] += 1
            else:
                inconclusive += 1; mu["inconclusive"] += 1
    total = sum(wins.values()) or 1
    return {
        "n_contests": n, "wins": dict(wins), "ties": ties, "inconclusive": inconclusive,
        "win_rate_over_decisive": {k: round(v / total, 3) for k, v in wins.items()},
        "per_matchup": {k: dict(v) for k, v in matchup.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", required=True,
                    help="dir containing shard_*/<pair>/{rationale,_destyled}.txt + *_decision.json")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--judge-prompt", required=True)
    ap.add_argument("--data-root", default="/tmp/satir_full_dataset")
    ap.add_argument("--max-workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    in_dir = pathlib.Path(args.in_dir).resolve()
    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    template = pathlib.Path(args.judge_prompt).read_text(encoding="utf-8")

    data_root = pathlib.Path(args.data_root)
    notes = load_patient_notes(data_root)
    trials = load_trial_texts(data_root)
    print(f"Loaded {len(notes)} notes, {len(trials)} trials", file=sys.stderr)

    # Discover pair dirs
    pair_dirs = sorted([p for p in in_dir.glob("*/*") if p.is_dir() and "__" in p.name])
    print(f"Found {len(pair_dirs)} pair dirs under {in_dir}", file=sys.stderr)
    if not pair_dirs:
        print("No pairs found", file=sys.stderr); sys.exit(2)

    endpoint_5 = os.environ.get("OPENAI_ENDPOINT_GPT5") or os.environ.get("OPENAI_ENDPOINT")
    if not endpoint_5:
        print("FATAL: OPENAI_ENDPOINT_GPT5 (or OPENAI_ENDPOINT) must be set", file=sys.stderr); sys.exit(2)
    engine = AzureInferenceEngine(endpoint=endpoint_5, api_key_env_var="OPENAI_API_KEY",
                                   model_name="gpt-5", default_temperature=0.0)

    def _task(p: pathlib.Path):
        pid, tid = p.name.split("__", 1)
        note = notes.get(pid) or ""
        trial = trials.get(tid) or ""
        out_pair = out_dir / p.parent.name / p.name
        try:
            return rejudge_pair(p, out_pair,
                                engine=engine, template=template,
                                patient_note=note, trial_text=trial,
                                seed=args.seed)
        except Exception as e:
            return {"pair": p.name, "skipped": True, "reason": f"{type(e).__name__}: {e}"}

    t0 = time.perf_counter()
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futs = [pool.submit(_task, p) for p in pair_dirs]
        done = 0
        for f in as_completed(futs):
            results.append(f.result())
            done += 1
            if done % max(1, len(pair_dirs) // 10) == 0 or done == len(pair_dirs):
                el = time.perf_counter() - t0
                print(f"  {done}/{len(pair_dirs)} done ({el:.1f}s)", file=sys.stderr)

    agg = aggregate(results)
    out_json = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "judge_prompt": str(args.judge_prompt),
        "aggregate": agg,
        "per_pair": results,
    }
    (out_dir / "judge_results.json").write_text(json.dumps(out_json, indent=2, default=str))
    print("\n=== Rejudge results ===")
    print(json.dumps(agg, indent=2))
    print(f"\nSaved to {out_dir / 'judge_results.json'}")


if __name__ == "__main__":
    main()
