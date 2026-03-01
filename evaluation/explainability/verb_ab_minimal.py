"""Minimal A/B test for verbalizer prompt: re-verbalizes existing SMT outputs
with a new prompt, without re-running the full match pipeline.

Usage:
  python -m evaluation.explainability.verb_ab_minimal \
      --in-dir evaluation/results/verbalize_judge_235_v3 \
      --pairs-file /tmp/verb_ab_10.txt \
      --new-prompt sql_retrieval/meval/prompts/verbalize_prescreen_unified_v2.prompt \
      --out-dir evaluation/results/verb_ab_v2_minimal
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from smt_core.inference_engine import AzureInferenceEngine  # noqa: E402


_NAMED_RE = re.compile(
    r"\(assert\s*\(?\s*!.*?:named\s+(REQ\w+)\)+[^;]*?;;\s*\"(.+?)\"",
    re.DOTALL,
)


def _req_map(smt_lines):
    out = {}
    text = "\n".join(smt_lines)
    for m in _NAMED_RE.finditer(text):
        label, crit = m.group(1), m.group(2)
        if label not in out:
            out[label] = crit.strip()
    return out


def _summarize(smt_result):
    art = {"sides": {}}
    for side in ("inclusion", "exclusion"):
        sr = smt_result.get(side, {}) or {}
        raw = sr.get("raw") or {}
        er = raw.get("eval_result") or {}
        ls = er.get("label_status") or {}
        rm = _req_map(raw.get("smt_program_lines") or [])
        def decorate(labels):
            return [{"label": l, "criterion": rm.get(l, "")} for l in (labels or [])]
        pvv = raw.get("patient_var_values") or {}
        concrete = {}
        for k, v in pvv.items():
            val = v.get("value") if isinstance(v, dict) else v
            if val is None:
                continue
            if isinstance(val, str) and val.strip().lower() in {"null", "none", ""}:
                continue
            concrete[k] = {
                "value": val,
                "evidence": (v.get("evidence") or "")[:200] if isinstance(v, dict) else "",
            }
        art["sides"][side] = {
            "sat_like": sr.get("sat_like"),
            "status": er.get("status"),
            "unsat_labels": decorate(ls.get("unsat") or []),
            "sat_labels": decorate(ls.get("sat") or [])[:8],
            "concrete_mined_values": concrete,
        }
    return art


def load_patient_text(data_root, pid):
    sub = "sigir2014_1" if pid.startswith("sigir-2014") else "sigir"
    # Most common path:
    p = data_root / sub / "patients" / f"{pid}.txt"
    if p.exists():
        return p.read_text(encoding="utf-8")
    # fallback
    for sub in ("sigir2014_1", "sigir2014_2", "sigir2014_3", "sigir"):
        p = data_root / sub / "patients" / f"{pid}.txt"
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def load_trial_text(data_root, tid):
    # try sibling corpora
    for sub in ("sigir2014_1", "sigir2014_2", "sigir2014_3", "sigir", "trec21", "trec22"):
        p = data_root / sub / "trials" / f"{tid}.json"
        if p.exists():
            d = json.load(open(p))
            return (d.get("criteria") or d.get("eligibility") or d.get("text") or "")
    return ""


def verbalize_once(engine, template, pair_dir, data_root):
    pid, tid = pair_dir.name.split("__")
    smt_dec = json.load(open(pair_dir / "smt_decision.json"))
    art = _summarize(smt_dec)
    smt_art_json = json.dumps(art, indent=2, default=str)[:8000]
    # Read the previous SMT rationale + decision
    old_rationale = (pair_dir / "smt_rationale.txt").read_text(encoding="utf-8") if (pair_dir / "smt_rationale.txt").exists() else ""
    eligible = smt_dec.get("eligible")
    label = "eligible" if eligible else ("ineligible" if eligible is False else "unknown")
    patient_text = load_patient_text(data_root, pid)
    trial_text = load_trial_text(data_root, tid)
    prompt = (template
              .replace("#PATIENT_NOTE#", patient_text)
              .replace("#TRIAL_ELIGIBILITY_TEXT#", trial_text)
              .replace("#SYSTEM_DECISION_LABEL#", label)
              .replace("#SOURCE_RATIONALE#", old_rationale)
              .replace("#SOURCE_ARTIFACTS#", smt_art_json))
    raw = engine(prompt, temperature=0.0)
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    try:
        parsed = json.loads(m.group(0)) if m else {"rationale": raw}
    except Exception:
        parsed = {"rationale": raw}
    return {
        "pair": pair_dir.name,
        "decision": label,
        "old_rationale": old_rationale.strip(),
        "new_rationale": parsed.get("rationale", "").strip(),
        "new_key_points": parsed.get("key_points", []),
        "new_deferred": parsed.get("deferred_criteria", []),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", required=True)
    ap.add_argument("--pairs-file", required=True)
    ap.add_argument("--new-prompt", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--data-root", default="/tmp/satir_full_dataset")
    ap.add_argument("--max-workers", type=int, default=4)
    args = ap.parse_args()

    in_dir = pathlib.Path(args.in_dir).resolve()
    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_root = pathlib.Path(args.data_root)
    template = pathlib.Path(args.new_prompt).read_text(encoding="utf-8")

    pairs = []
    for line in pathlib.Path(args.pairs_file).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pid, tid = line.split(",", 1)
        pairs.append(f"{pid.strip()}__{tid.strip()}")

    pair_dirs = []
    for p in pairs:
        for shard in in_dir.glob("shard_*"):
            d = shard / p
            if d.exists():
                pair_dirs.append(d); break

    print(f"Processing {len(pair_dirs)} / {len(pairs)} pairs", file=sys.stderr)

    import os
    endpoint_4 = os.environ.get("OPENAI_ENDPOINT")
    if not endpoint_4:
        print("FATAL: OPENAI_ENDPOINT must be set", file=sys.stderr); sys.exit(2)
    engine = AzureInferenceEngine(endpoint=endpoint_4,
                                   api_key_env_var="OPENAI_API_KEY",
                                   model_name="gpt-4.1",
                                   default_temperature=0.0)

    results = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futs = {pool.submit(verbalize_once, engine, template, pd, data_root): pd for pd in pair_dirs}
        for f in as_completed(futs):
            try:
                r = f.result()
                results.append(r)
                print(f"done: {r['pair']}", file=sys.stderr)
            except Exception as e:
                print(f"FAIL {futs[f].name}: {type(e).__name__}: {e}", file=sys.stderr)

    (out_dir / "results.json").write_text(json.dumps(results, indent=2))
    # Also write human-readable comparison
    lines = ["# Verbalizer v2 A/B comparison\n"]
    for r in sorted(results, key=lambda x: x["pair"]):
        lines.append(f"\n## {r['pair']}  [{r['decision']}]\n")
        lines.append(f"**OLD:** {r['old_rationale']}\n")
        lines.append(f"**NEW:** {r['new_rationale']}\n")
        if r.get('new_key_points'):
            lines.append(f"**Key points:** {r['new_key_points']}\n")
        if r.get('new_deferred'):
            lines.append(f"**Deferred:** {r['new_deferred']}\n")
    (out_dir / "comparison.md").write_text("".join(lines))
    print(f"Wrote {out_dir / 'comparison.md'}", file=sys.stderr)


if __name__ == "__main__":
    main()
